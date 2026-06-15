# Schema-Aware Retrieval And Join Miner Plan

## Purpose

This document is the implementation and test anchor for the next DiracData v2 workstream.

The goal is to improve semantic accuracy on `retail_analytics` by building two connected systems:

1. **Join Intelligence V2**: mine observed joins from query history, infer missing joins from schema/profile evidence, and expose intent-aware join paths.
2. **Schema-Aware Retrieval V2**: convert SQL history into NL-SQL-column training pairs, enrich coverage with validated synthetic SQL, train a RoBERTa-style reranker/linker, and compile compact high-confidence runtime context.

The guiding principle is:

> The SQL agent should not rediscover schema truth at runtime. It should receive a compact, evidence-scored packet of likely columns, join paths, patterns, assertions, and ambiguities.

## Current Retail Baseline

Known retail inputs:

- Schema descriptions: `v2/context/retail_analytics_metadata_descriptions.json`
- Query history: `v2/data/query_history/retail_analytics_query_history.csv`
- Parquet data: `v2/data/retail_analytics/parquet/sf1/`
- Current semantic catalog: `v2/learning/artifacts/retail_analytics_semantic_catalog_v2_clean_20260611/semantic_catalog.json`
- Current SQL library: `v2/learning/artifacts/retail_analytics_v2_20260610/sql_library.json`
- Current schema AST: `v2/learning/artifacts/retail_analytics_v2_20260610/schema_ast.json`
- Retail UAT suite seed: `v2/evals/TPCDS_test_suite.csv`

Current catalog shape observed before this plan:

- 24 tables
- 425 columns
- 501 catalog cards
- 22 observed join edges

Known current weakness:

- Query-history join extraction is high precision but low recall.
- Some transaction-time joins are missing or not consistently surfaced:
  - `online_purchases.billing_client_profile_ref -> client_profiles.client_profile_record`
  - `online_purchases.billing_household_profile_ref -> household_profiles.household_profile_record`
  - `store_purchases.client_profile_ref -> client_profiles.client_profile_record`
  - `store_purchases.household_profile_ref -> household_profiles.household_profile_record`
- Runtime context can favor `clients.current_*` paths where historical purchase-time refs are more appropriate.

## Non-Negotiable Engineering Rules

- No schema-specific hardcoding in prompts, code, tests, or ranking rules.
- Retail analytics is the first validation dataset, not the implementation target.
- Every phase must produce:
  - a local artifact or measurable output,
  - unit tests,
  - integration tests on retail where relevant,
  - UAT CLI commands for manual testing,
  - before/after metrics or an explicit reason why metrics are not yet applicable.
- Query history is evidence, not truth.
- Synthetic examples are never approved truth by default.
- LLM-generated labels must be validated against schema references and executable SQL before use.
- Runtime context must stay compact. The goal is better context, not more context.

## Output Artifact Roadmap

The workstream should eventually produce these artifacts under a run-specific retail folder, for example:

```text
v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/
  baseline_report.json
  join_miner/
    query_history_join_edges.jsonl
    unresolved_sql_references.jsonl
    pairwise_join_candidates.jsonl
    join_edges.jsonl
    join_graph.json
    join_paths.jsonl
    manifest.json
  semantic_search/
    sql_nl_pairs.jsonl
    synthetic_sql_nl_pairs.jsonl
    training_pairs.jsonl
    hard_negatives.jsonl
    roberta_dataset_manifest.json
    retrieval_eval.json
  compiler/
    compiled_context_samples.jsonl
    compiler_eval.json
  uat/
    model_runs/
    uat_summary.json
```

Exact names may evolve, but the artifact contract should remain explicit and inspectable.

## Phase 0: Baseline And Gold Test Set

### Objective

Freeze the current behavior so future phases can prove improvement.

### Technical Tasks

1. Create a retail benchmark file with 30-50 questions.
2. For each question, label:
   - expected business intent,
   - expected tables,
   - expected columns,
   - expected join path,
   - ambiguity if any,
   - expected SQL or ground-truth SQL family,
   - expected result or result-check query.
3. Include hard semantic cases:
   - customer geography vs store geography vs warehouse geography,
   - billing address vs shipping address vs current address,
   - billing profile vs current profile,
   - online vs store vs mail purchase channels,
   - sale date vs ship date vs return date,
   - promotion/campaign attribution,
   - warehouse stock and fulfillment,
   - undefined business term clarification,
   - anti-joins and exclusion windows.
4. Add a baseline runner that can run current compiler/agent behavior on this set and emit a report.

### Suggested Benchmark Queries

Start with these and expand to 30-50:

1. `count all male customers from california`
2. `how many customers from Arizona shopped electronic items online in 2002? split by male vs female`
3. `count all customers who shopped online in 2002 and slice them by gender, household income and state and give me some insights`
4. `How many female customers from Maine shopped jewelry online in 2002 but did not shop any product in 2001?`
5. `For 2002 store purchases tied to marketing campaigns, show the top 10 household income bands and warehouses that had positive stock for the sold items on the sale date, ranked by store net sales and distinct customers.`
6. `How many active female customers from Maine shopped at least 1 jewelry item and at most 3 electronic items in the year 2002?`
7. `Which warehouses served the most online electronics customers in 2002?`
8. `Compare customers by billing state and shipping state for online purchases in 2002.`
9. `Which marketing campaigns drove the most online net paid sales by product category in 2002?`
10. `Show refund amount by return reason and product category for online purchases in 2002.`

### Unit Tests

- Benchmark loader validates required fields.
- Expected join path syntax validates as `table.column = table.column`.
- Expected columns exist in metadata.

### Integration Tests

- Ground-truth SQL references must validate against retail schema.
- Result-check queries must execute in DuckDB on retail parquet data.

### E2E UAT CLI

After implementation of Phase 0, provide commands similar to:

```bash
PYTHONPATH=v2/src .venv/bin/python v2/scripts/run_retail_baseline_eval.py \
  --questions v2/evals/retail_schema_aware_benchmark.csv \
  --semantic-catalog-path v2/learning/artifacts/retail_analytics_semantic_catalog_v2_clean_20260611/semantic_catalog.json \
  --output-dir v2/data/uat_runs/schema_aware_baseline_<run_id>
```

For live model comparison:

```bash
DIRACDATA_SCHEMA=retail_analytics DIRACDATA_CATALOG=retail_pod \
.venv/bin/python v2/scripts/run_primitive_agent.py \
  --model-profile anthropic_haiku_45 \
  --workflow supervisor \
  --interactive \
  --stream \
  --stream-format text \
  --semantic-catalog-path v2/learning/artifacts/retail_analytics_semantic_catalog_v2_clean_20260611/semantic_catalog.json \
  --question "count all customers who shopped online in 2002 and slice them by gender, household income and state and give me some insights"
```

### Phase Stop Condition

Stop after producing:

- benchmark file,
- baseline report,
- test results,
- commands for the user to run the same benchmark.

## Phase 1: Robust Query-History Join Miner

### Objective

Extract observed joins from query history with better alias, CTE, subquery, and dialect handling.

### Technical Tasks

1. Introduce a join-miner module with a clean interface:

```python
class QueryHistoryJoinMiner:
    def mine(self, sql: str, schema: SchemaMap, dialect: SqlDialect) -> JoinMiningResult: ...
```

2. Support configurable SQL dialects:
   - DuckDB
   - Databricks/Spark
   - Snowflake
   - Postgres
3. Use SQLGlot parse and schema-aware qualification where possible:
   - parse AST,
   - qualify columns with schema,
   - walk scopes,
   - inspect `JOIN ... ON`,
   - inspect `WHERE` equality predicates,
   - handle `USING`,
   - trace CTE/subquery output columns back to base table columns where possible.
4. Preserve unresolved references instead of silently dropping them.
5. Output structured evidence:

```json
{
  "source_query_id": "...",
  "left_column": "table_a.column_x",
  "right_column": "table_b.column_y",
  "sql_condition": "table_a.column_x = table_b.column_y",
  "join_operator": "inner|left|right|full|cross|unknown",
  "source": "query_history",
  "parser": "sqlglot_lineage",
  "confidence": 0.0,
  "observed_count": 1,
  "unresolved": false,
  "warnings": []
}
```

6. Keep compatibility with current SQL library and semantic catalog builder.

### Unit Tests

Add tests for:

- simple aliased join,
- multiple joins,
- CTE projection alias,
- nested subquery alias,
- `USING` join,
- unqualified join columns resolvable from schema,
- unqualified ambiguous columns are unresolved,
- `WHERE a.id = b.id` implicit join,
- same-table equality is not emitted as table join,
- invalid table/column refs are not emitted.

### Integration Tests

Run miner on:

- `v2/data/query_history/retail_analytics_query_history.csv`
- only successful rows,
- compare old and new join edge counts,
- produce unresolved SQL reference report.

### Acceptance Criteria

- No unknown table/column refs.
- New miner extracts all joins current miner extracted.
- New miner extracts additional valid joins from CTE/subquery cases where present.
- Missing edge report is inspectable.

### E2E UAT CLI

After Phase 1 implementation:

```bash
PYTHONPATH=v2/src .venv/bin/python v2/scripts/mine_query_history_joins.py \
  --query-history-path v2/data/query_history/retail_analytics_query_history.csv \
  --metadata-descriptions-path v2/context/retail_analytics_metadata_descriptions.json \
  --dialect duckdb \
  --output-dir v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/join_miner
```

### Phase Stop Condition

Stop after showing:

- join count before/after,
- sample extracted joins,
- unresolved cases,
- test output,
- CLI command for user verification.

## Phase 2: Pairwise Join Candidate Discovery

### Objective

Find plausible joins that query history did not cover.

### Technical Tasks

1. Build column profiles if not already present:
   - row count,
   - null count/rate,
   - distinct count,
   - approximate distinct count,
   - sample values,
   - min/max for numeric/date,
   - value type,
   - top-k frequent values.
2. Generate table-pair column candidates.
3. Apply hard filters:
   - compatible data type,
   - not both low-cardinality categorical dimensions,
   - not free text unless evidence is very strong,
   - not obvious measure-to-key mismatch.
4. Score candidate evidence:
   - name similarity,
   - description similarity,
   - suffix/prefix role similarity,
   - key-likeness,
   - containment,
   - overlap,
   - null behavior,
   - sampled join row-count multiplier,
   - query-history support if any.
5. Classify candidates:
   - `relationship_join`,
   - `comparable_dimension`,
   - `ambiguous`,
   - `rejected`.
6. Store candidates with evidence:

```json
{
  "left_column": "online_purchases.billing_client_profile_ref",
  "right_column": "client_profiles.client_profile_record",
  "candidate_type": "relationship_join",
  "confidence": 0.91,
  "evidence": {
    "type_compatible": true,
    "left_distinct": 1234,
    "right_distinct": 698,
    "containment": 0.97,
    "row_multiplier": 1.0,
    "name_similarity": 0.72,
    "description_similarity": 0.81
  },
  "review_status": "inferred_high_confidence"
}
```

### Unit Tests

- Reject incompatible types.
- Reject low-cardinality dimension-to-dimension traps.
- Accept many-to-one fact-to-dimension candidates.
- Mark many-to-many candidates as risky.
- Classify `state` to `state` as comparable dimension, not join key.

### Integration Tests

On retail:

- Discover purchase-time profile joins.
- Discover household profile joins.
- Keep existing observed joins.
- Reject state-to-state as relationship join.

### Acceptance Criteria

Must discover or explicitly justify not discovering:

- `online_purchases.billing_client_profile_ref = client_profiles.client_profile_record`
- `online_purchases.billing_household_profile_ref = household_profiles.household_profile_record`
- `store_purchases.client_profile_ref = client_profiles.client_profile_record`
- `store_purchases.household_profile_ref = household_profiles.household_profile_record`

### E2E UAT CLI

```bash
PYTHONPATH=v2/src .venv/bin/python v2/scripts/discover_pairwise_joins.py \
  --data-root v2/data/retail_analytics/parquet/sf1 \
  --metadata-descriptions-path v2/context/retail_analytics_metadata_descriptions.json \
  --query-history-joins-path v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/join_miner/query_history_join_edges.jsonl \
  --output-dir v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/join_miner
```

### Phase Stop Condition

Stop after showing:

- inferred join candidates,
- rejected false positives,
- expected retail snapshot joins,
- test output,
- user CLI command.

## Phase 3: Semantic Role Labeling For Joins

### Objective

Assign business roles to multiple joins between the same tables.

### Technical Tasks

1. Use deterministic hints first:
   - `billing`,
   - `shipping`,
   - `current`,
   - `refunded`,
   - `returning`,
   - `sale`,
   - `ship`,
   - `return`.
2. Optionally use an LLM labeler over:
   - table descriptions,
   - column descriptions,
   - candidate join evidence,
   - sample values/profile.
3. Store:
   - `relationship_role`,
   - `temporal_role`,
   - `entity_role`,
   - `usage_guidance`,
   - `ambiguity_group`.

Example:

```json
{
  "left_column": "online_purchases.billing_address_ref",
  "right_column": "addresses.address_record",
  "relationship_role": "billing_address_at_purchase_time",
  "temporal_role": "transaction_time",
  "usage_guidance": "Use for customer billing geography at the time of online purchase.",
  "ambiguity_group": "customer_geography"
}
```

### Unit Tests

- Same table-pair edges can coexist.
- Billing and shipping roles are distinct.
- Current and transaction-time roles are distinct.

### Integration Tests

On retail:

- `online_purchases.billing_address_ref` role differs from `shipping_address_ref`.
- `clients.current_address_ref` role differs from both.

### E2E UAT CLI

```bash
PYTHONPATH=v2/src .venv/bin/python v2/scripts/label_join_semantics.py \
  --join-candidates-path v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/join_miner/pairwise_join_candidates.jsonl \
  --metadata-descriptions-path v2/context/retail_analytics_metadata_descriptions.json \
  --output-dir v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/join_miner
```

### Phase Stop Condition

Stop after showing:

- role-labeled join examples,
- ambiguity groups,
- tests,
- command for user.

## Phase 4: Join Graph And Intent-Aware Path Search

### Objective

Build a graph that can return safe join paths for an intent.

### Technical Tasks

1. Create graph nodes:
   - table nodes,
   - column nodes,
   - entity/domain nodes where useful.
2. Create graph edges:
   - observed joins,
   - inferred joins,
   - comparable dimensions,
   - column-to-table containment,
   - pattern-to-column usage later.
3. Add edge weights:
   - confidence,
   - evidence type,
   - cardinality risk,
   - semantic role match.
4. Implement path search:
   - shortest reliable path,
   - top-k paths,
   - avoid risky many-to-many paths unless intent requires,
   - prefer query-history observed paths when semantics match,
   - prefer transaction-time paths for historical fact queries.
5. Emit result:

```json
{
  "intent": "historical online purchase customer demographics",
  "required_tables": ["online_purchases", "addresses", "client_profiles"],
  "recommended_paths": [
    {
      "edges": [
        "online_purchases.billing_address_ref = addresses.address_record",
        "online_purchases.billing_client_profile_ref = client_profiles.client_profile_record"
      ],
      "confidence": 0.92,
      "guidance": "Transaction-time customer geography and profile."
    }
  ],
  "alternatives": [
    {
      "edges": ["online_purchases.billing_client_ref = clients.client_record", "clients.current_address_ref = addresses.address_record"],
      "warning": "Current customer geography, not purchase-time geography."
    }
  ]
}
```

### Unit Tests

- Multiple valid paths are returned with distinct semantics.
- Risky paths are ranked lower.
- No path returns explicit failure.

### Integration Tests

Retail path searches:

- online purchase -> customer state,
- online purchase -> customer gender,
- online purchase -> household income,
- store purchase -> retail location,
- store purchase -> stock/warehouse,
- mail order -> support center and warehouse.

### E2E UAT CLI

```bash
PYTHONPATH=v2/src .venv/bin/python v2/scripts/search_join_paths.py \
  --join-graph-path v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/join_miner/join_graph.json \
  --from-table online_purchases \
  --to-columns addresses.state client_profiles.gender income_ranges.lower_bound income_ranges.upper_bound \
  --intent "historical online purchase customer demographics"
```

### Phase Stop Condition

Stop after showing:

- path search output for retail examples,
- alternatives and warnings,
- tests,
- user command.

## Phase 5: SQL To NL Pattern Library

### Objective

Convert trusted SQL history into NL-SQL-column training and retrieval examples.

### Technical Tasks

1. Start from successful query history.
2. Deduplicate exact normalized SQL.
3. Parse SQL into:
   - tables,
   - columns,
   - join path,
   - filters,
   - measures,
   - dimensions,
   - time windows,
   - grain.
4. Score query trust:
   - success,
   - repeated template,
   - dashboard/job usage,
   - parser confidence,
   - result shape,
   - join graph compatibility.
5. Use LLM to generate:
   - canonical question,
   - paraphrases,
   - compact intent signature.
6. Validate:
   - no invented table/column,
   - NL aligns with SQL intent,
   - SQL still executable or dry-runnable.
7. Store:

```json
{
  "id": "pattern:...",
  "source_sql_id": "...",
  "canonical_question": "...",
  "paraphrases": [],
  "sql_template": "...",
  "tables": [],
  "columns": [],
  "join_path": [],
  "intent_signature": {},
  "trust_score": 0.0,
  "review_status": "observed"
}
```

### Unit Tests

- SQL pattern JSON validates schema.
- Pattern cannot contain unknown refs.
- Failed query history is excluded.
- Dedupe works.

### Integration Tests

On retail:

- Generate at least one pattern for each repeated template family in query history.
- Patterns are searchable by paraphrases.
- Trust scores are populated.

### E2E UAT CLI

```bash
PYTHONPATH=v2/src .venv/bin/python v2/scripts/build_sql_nl_patterns.py \
  --query-history-path v2/data/query_history/retail_analytics_query_history.csv \
  --metadata-descriptions-path v2/context/retail_analytics_metadata_descriptions.json \
  --join-graph-path v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/join_miner/join_graph.json \
  --model-profile anthropic_sonnet_46 \
  --batch-size 50 \
  --output-dir v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/semantic_search
```

### Phase Stop Condition

Stop after showing:

- pattern samples,
- trust score distribution,
- tests,
- user command.

## Phase 6: Synthetic Coverage Factory

### Objective

Cover schema areas not present in query history without inventing unsupported semantics.

### Technical Tasks

1. Build coverage report:
   - table coverage,
   - column coverage,
   - join path coverage,
   - role coverage,
   - query-operation coverage.
2. Generate SQL using constrained templates:
   - select dimension,
   - filter dimension,
   - aggregate measure,
   - group by dimension,
   - time-window filter,
   - anti-join,
   - top-k,
   - join path traversal.
3. Use join graph for valid paths only.
4. Use profile/sample values for valid predicates.
5. Execute or dry-run every SQL.
6. Generate NL only after SQL is validated.
7. Mark outputs:
   - `synthetic_validated`,
   - `needs_review`,
   - `rejected`.

### Unit Tests

- Synthetic SQL never references unknown refs.
- SQL is generated only from valid graph paths.
- Low-confidence joins do not produce approved examples.

### Integration Tests

On retail:

- Improve coverage for previously missing important columns.
- Generate examples for snapshot profile/household joins.
- Validate every generated SQL with DuckDB.

### E2E UAT CLI

```bash
PYTHONPATH=v2/src .venv/bin/python v2/scripts/generate_synthetic_sql_nl.py \
  --metadata-descriptions-path v2/context/retail_analytics_metadata_descriptions.json \
  --join-graph-path v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/join_miner/join_graph.json \
  --data-root v2/data/retail_analytics/parquet/sf1 \
  --model-profile anthropic_sonnet_46 \
  --output-dir v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/semantic_search
```

### Phase Stop Condition

Stop after showing:

- before/after coverage,
- accepted/rejected synthetic examples,
- tests,
- command.

## Phase 7: RoBERTa Training Dataset

### Objective

Build training data for a schema-aware entity linker/reranker.

### Technical Tasks

1. Create positive pairs:

```text
NL query + candidate schema object/path -> relevant
```

2. Create hard negatives:

```text
NL query + confounding schema object/path -> not relevant
```

3. Include candidate types:
   - table,
   - column,
   - join edge,
   - join path,
   - SQL pattern.
4. Balance:
   - positives/negatives,
   - easy/hard negatives,
   - high-frequency/low-frequency columns,
   - observed/synthetic sources.
5. Split:
   - train,
   - validation,
   - test,
   - gold-only holdout.
6. Store source provenance for every row.

Training row shape:

```json
{
  "query": "customers who shopped online in 2002 by state",
  "candidate_type": "column",
  "candidate_ref": "addresses.state",
  "candidate_text": "State abbreviation for customer address...",
  "path_context": "online_purchases.billing_address_ref = addresses.address_record",
  "label": 1,
  "source": "query_history|synthetic|gold",
  "difficulty": "hard_negative|positive|easy_negative"
}
```

### Unit Tests

- Dataset rows validate.
- Labels are binary or graded by explicit enum.
- All candidate refs exist.
- No train/test leakage by exact query ID.

### Integration Tests

- Retail dataset has coverage by table/column/path.
- Hard negatives include known confounders:
  - customer state vs store state vs warehouse state,
  - current profile vs transaction-time profile,
  - billing address vs shipping address.

### E2E UAT CLI

```bash
PYTHONPATH=v2/src .venv/bin/python v2/scripts/build_roberta_retrieval_dataset.py \
  --sql-nl-patterns-path v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/semantic_search/sql_nl_pairs.jsonl \
  --synthetic-patterns-path v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/semantic_search/synthetic_sql_nl_pairs.jsonl \
  --metadata-descriptions-path v2/context/retail_analytics_metadata_descriptions.json \
  --join-graph-path v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/join_miner/join_graph.json \
  --output-dir v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/semantic_search
```

### Phase Stop Condition

Stop after showing:

- dataset counts,
- coverage report,
- hard negative samples,
- tests,
- command.

## Phase 8: RoBERTa Retriever Training And Evaluation

### Objective

Train a compact schema-aware reranker/linker.

### Technical Tasks

1. Choose model:
   - initial: `roberta-base` or small sentence-transformer/cross-encoder equivalent,
   - config-driven for later alternatives.
2. Train pairwise classifier/reranker:
   - input: query + candidate text + path context,
   - output: relevance score.
3. Evaluate:
   - recall@5/10/20,
   - MRR,
   - nDCG,
   - confounder accuracy,
   - join-path selection accuracy.
4. Export model artifact and manifest.
5. Provide CPU-friendly inference path for local UAT.

### Unit Tests

- Model wrapper returns deterministic shape.
- Empty candidates handled.
- Batch inference works.

### Integration Tests

- Evaluate on retail holdout.
- Compare BM25/vector vs RoBERTa reranker.

### E2E UAT CLI

```bash
PYTHONPATH=v2/src .venv/bin/python v2/scripts/train_schema_retriever.py \
  --dataset-dir v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/semantic_search \
  --model-name roberta-base \
  --output-dir v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/semantic_search/roberta_retriever
```

```bash
PYTHONPATH=v2/src .venv/bin/python v2/scripts/evaluate_schema_retriever.py \
  --dataset-dir v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/semantic_search \
  --model-dir v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/semantic_search/roberta_retriever \
  --output-path v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/semantic_search/retrieval_eval.json
```

### Phase Stop Condition

Stop after showing:

- retrieval metrics,
- failure cases,
- comparison with current search,
- user commands.

## Phase 9: Context Compiler V2 Integration

### Objective

Use RoBERTa retrieval, SQL-NL patterns, and join graph to produce compact runtime context.

### Technical Tasks

1. Build compiler input:
   - NL query,
   - optional conversation summary,
   - schema scope,
   - model budget.
2. Retrieve:
   - candidate tables/columns,
   - SQL patterns,
   - join paths,
   - assertions,
   - ambiguities.
3. Emit context packet:

```yaml
intent_frame:
  ...
recommended_columns:
  ...
recommended_join_paths:
  ...
similar_patterns:
  ...
assertions:
  ...
ambiguities:
  ...
confidence:
  ...
```

4. Add trace output so user can see:
   - which retrievers ran,
   - which candidates were scored,
   - why paths were selected,
   - where clarification is required.

### Unit Tests

- Compiler packet schema validates.
- High confidence candidates included.
- Low confidence irrelevant candidates excluded.
- Ambiguities are surfaced.

### Integration Tests

Retail queries:

- snapshot/current paths,
- geography confounders,
- promotion/warehouse complex joins,
- undefined business terms.

### E2E UAT CLI

```bash
PYTHONPATH=v2/src .venv/bin/python v2/scripts/compile_schema_aware_context.py \
  --question "count all customers who shopped online in 2002 and slice them by gender, household income and state" \
  --metadata-descriptions-path v2/context/retail_analytics_metadata_descriptions.json \
  --join-graph-path v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/join_miner/join_graph.json \
  --retriever-model-dir v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/semantic_search/roberta_retriever \
  --patterns-path v2/learning/artifacts/retail_analytics_schema_aware_v2_<run_id>/semantic_search/sql_nl_pairs.jsonl
```

### Phase Stop Condition

Stop after showing:

- compiled context samples,
- before/after candidate quality,
- tests,
- user command.

## Phase 10: Agent Integration And Full UAT

### Objective

Wire compiler output into the agent harness and prove final SQL accuracy improves.

### Technical Tasks

1. Add config switch:
   - current compiler,
   - schema-aware compiler v2.
2. Keep trace visible in CLI:
   - retrieval candidates,
   - join path,
   - ambiguity,
   - assertions.
3. Do not add model-specific prompt hacks.
4. Run identical UAT across:
   - Haiku,
   - GLM5,
   - GPT-5.4 Mini,
   - Qwen.

### Unit Tests

- Agent receives compiler packet.
- Clarification gate triggers on unresolved ambiguity.
- SQL author sees recommended path.
- Validator can inspect chosen path.

### Integration Tests

- Dry run SQL.
- Execute SQL with max rows.
- Compare result with ground truth.

### E2E UAT CLI

```bash
DIRACDATA_SCHEMA=retail_analytics DIRACDATA_CATALOG=retail_pod \
DIRACDATA_CONTEXT_COMPILER_MODE=schema_aware_v2 \
.venv/bin/python v2/scripts/run_primitive_agent.py \
  --model-profile anthropic_haiku_45 \
  --workflow supervisor \
  --interactive \
  --stream \
  --stream-format text \
  --question "count all customers who shopped online in 2002 and slice them by gender, household income and state and give me some insights"
```

Repeat for:

```bash
--model-profile bedrock_zai_glm_5_ap_south_1
--model-profile openai_gpt_5_4_mini
--model-profile bedrock_qwen3_next_80b_a3b_ap_south_1
```

### Phase Stop Condition

Stop after showing:

- UAT summary,
- model comparison,
- SQL failures,
- semantic failures,
- whether smaller models improved.

## Absolute Gold Tests

These tests decide whether the system is actually improving.

### Gold Test 1: Customer Geography Confounder

Question:

```text
how many customers from Arizona shopped electronic items online in 2002? split by male vs female
```

Expected:

- Customer geography, not store or warehouse geography.
- Online purchases fact.
- Product category through merchandise.
- Year through calendar dimension.
- Gender through transaction-time or clarified/current profile path.

Pass if:

- `retail_locations.state` and `fulfillment_centers.state` are not selected as customer state.

### Gold Test 2: Snapshot Vs Current

Question:

```text
count all customers who shopped online in 2002 and slice them by gender, household income and state
```

Expected:

- Compiler surfaces transaction-time path as recommended.
- Compiler surfaces current path as alternative if applicable.
- Agent either uses transaction-time path or asks clarification.

Pass if:

- The choice is explicit, not accidental.

### Gold Test 3: Multi-Hop Income

Question:

```text
online customers in 2002 by household income band
```

Expected path:

```text
online_purchases.billing_household_profile_ref
-> household_profiles.household_profile_record
-> household_profiles.income_range_ref
-> income_ranges.income_range_record
```

Pass if:

- Compiler can retrieve this path even if query history coverage is weak.

### Gold Test 4: Warehouse Stock

Question:

```text
For 2002 store purchases tied to marketing campaigns, show the top 10 household income bands and warehouses that had positive stock for the sold items on the sale date.
```

Expected:

- Store purchases.
- Campaign relation.
- Stock levels via merchandise and calendar day.
- Warehouse via fulfillment centers.
- Explicit handling of whether warehouse stock is item-specific or aggregate.

Pass if:

- Compiler surfaces ambiguity and safe stock join path.

### Gold Test 5: Undefined Business Term

Question:

```text
How many active female customers from Maine bought jewelry online in 2002?
```

Expected:

- `active` is unresolved unless provided by business grounding.

Pass if:

- Agent asks clarification before SQL execution.

## Benchmark Metrics

### Retrieval Metrics

- column recall@5/10/20
- table recall@5/10
- join path recall@3
- MRR for correct column
- nDCG
- confounder accuracy
- snapshot/current classification accuracy

### Compiler Metrics

- context packet token size
- relevant card ratio
- missing expected column count
- missing expected join edge count
- ambiguity surfaced yes/no
- assertion relevance

### Agent Metrics

- SQL execution success
- semantic SQL correctness
- result accuracy
- unnecessary clarification rate
- missed clarification rate
- tool calls
- model tokens
- cost per correct answer

### Model Comparison

Run at least:

- Haiku
- GLM5
- GPT-5.4 Mini
- Qwen

Success is especially meaningful if Haiku/GLM5/Qwen improve, because that proves the harness is doing more work and model size matters less.

## Implementation Discipline

After each phase:

1. Run unit tests.
2. Run retail integration test.
3. Generate artifact.
4. Show sample artifact snippets.
5. Run the smallest useful UAT.
6. Provide exact CLI commands for user verification.
7. Stop and wait for review before the next phase.

Do not move to the next phase if:

- artifact schema is unclear,
- test coverage is missing,
- retail expected examples fail silently,
- implementation uses retail-specific hardcoding,
- compiler context gets larger without becoming more accurate.

