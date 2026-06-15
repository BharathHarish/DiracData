# DiracData

**An experimental context fabric and data-agent harness for reliable text-to-SQL.**

DiracData explores a simple thesis: smaller and cheaper models can answer harder
analytics questions when the runtime gives them the right semantic context,
the right SQL patterns, and the right safety gates.

Instead of asking an LLM to rediscover a warehouse from scratch on every query,
DiracData builds a compact, inspectable context layer from:

- schema and column descriptions
- query history
- approved NL-SQL examples
- learned SQL pattern libraries
- semantic catalog cards
- join and column retrieval evidence
- dry-run and steward validation gates

The current active implementation is in `v2/`. The older `v1/` implementation is
kept in the repo as a preserved reference.

## Why This Exists

Text-to-SQL systems fail less often because SQL syntax is hard, and more often
because the model is missing business context:

- Which table represents the action the user asked about?
- Which column is the right customer, account, date, or geography role?
- Should a phrase like "did not buy in 2001" inherit the same product/channel
  scope from the positive cohort?
- Is "customer from Arizona" current customer address, billing address, shipping
  address, store geography, or something else?
- Which historical query pattern is close enough to trust?

DiracData treats those as context compilation and semantic validation problems,
not just prompt engineering problems.

## Current Capabilities

- **Semantic catalog builder**: converts schema descriptions and SQL libraries
  into compact runtime cards for tables, columns, joins, and patterns.
- **SQL library learning**: mines query history and trusted NL-SQL pairs into
  reusable SQL snippets and intent signatures.
- **Schema-aware retrieval**: builds column cards and retrieval datasets for
  recall@k evaluation and reranker experiments.
- **Primitive data-agent harness**: runs a lightweight data-agent workflow with
  tool traces, streaming, SQL dry runs, clarification handling, and final
  execution gates.
- **Typed workflow mode**: separates intent, SQL authoring, dry run, steward
  validation, optional data engineering optimization, and final execution.
- **Interactive clarification**: stops before SQL execution when the compiler or
  steward finds a SQL-affecting ambiguity.
- **Retail/TPC-DS style benchmark assets**: includes a generated retail schema,
  query history, gold queries, benchmark queries, and context artifacts for
  repeatable evaluation.
- **Multi-model factory**: supports Anthropic, OpenAI, and Bedrock Converse
  profiles through the v2 model factory.

## Architecture

```text
User question
   |
   v
Semantic context compiler
   |  retrieves catalog cards, SQL patterns, joins, and unresolved terms
   v
Intent stage
   |  produces a grounded intent packet or asks for clarification
   v
SQL authoring stage
   |  probes values, writes SQL, and runs SQL dry-run / EXPLAIN
   v
Steward validation stage
   |  checks semantic alignment against intent, schema, values, and patterns
   v
Optional data engineering stage
   |  optimizes complex SQL, then re-enters validation
   v
Final read-only SQL execution
```

The important design choice: LLMs reason inside stages, but the harness owns
stage transitions, clarification stops, dry-run requirements, and final
execution.

## Repository Layout

```text
.
├── README.md
├── .env.example
├── docs/
│   ├── schema_aware_retrieval_join_miner.md
│   └── typed_workflow_kernel_plan.md
├── v1/
│   └── preserved first implementation
└── v2/
    ├── context/                 # schema descriptions and context documents
    ├── data/                    # local datasets and query history
    ├── evals/                   # gold and benchmark NL-SQL/eval files
    ├── learning/artifacts/      # generated schema AST, SQL library, catalog
    ├── scripts/                 # CLI entrypoints
    ├── src/diracdata_v2/        # active v2 package
    └── tests/                   # unit and integration tests
```

## Quick Start

DiracData is currently a research prototype. The repo includes generated
artifacts for the retail analytics schema, so you can inspect and run the v2
harness without rebuilding everything first.

### 1. Clone And Configure

```bash
git clone https://github.com/BharathHarish/DiracData.git
cd DiracData
cp .env.example .env
```

Fill `.env` with the provider credentials you want to use. Real secrets should
stay local and are ignored by Git.

### 2. Create A Python Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e v2
```

The checked-in `v2/pyproject.toml` is intentionally minimal while the prototype
is moving quickly. For live model runs, install the provider integrations you
use, for example LangChain chat integrations and the relevant Anthropic,
OpenAI, or AWS/Bedrock packages.

### 3. Run The Focused Test Suite

```bash
PYTHONPATH=v2/src .venv/bin/python -m unittest \
  v2.tests.test_typed_workflow \
  v2.tests.test_primitive_data_agent \
  v2.tests.test_primitive_gated_workflow \
  v2.tests.test_primitive_supervisor_workflow \
  v2.tests.test_sql_tool -v
```

### 4. Ask A Question With The Primitive Agent

```bash
DIRACDATA_SCHEMA=retail_analytics \
DIRACDATA_CATALOG=retail_pod \
DIRACDATA_PRIMITIVE_MAX_ITERATIONS=10 \
DIRACDATA_PRIMITIVE_SUBAGENT_MAX_ITERATIONS=10 \
.venv/bin/python v2/scripts/run_primitive_agent.py \
  --model-profile anthropic_haiku_45 \
  --workflow typed \
  --interactive \
  --stream \
  --stream-format text \
  --question "count all customers who bought jewelry online in 2002 but did not buy in 2001 and are from Arizona; slice by gender and marital status"
```

Useful model profiles currently include:

- `anthropic_haiku_45`
- `anthropic_sonnet_46`
- `openai_gpt_5_4_mini`
- `bedrock_qwen3_next_80b_a3b_ap_south_1`
- `bedrock_zai_glm_5_ap_south_1`

## Learning Pipeline

The learning pipeline builds the artifacts the agent consumes at runtime:

- schema graph / schema AST
- SQL library
- semantic catalog
- retrieval training pairs
- benchmark and recall reports

Example command for a full learning run:

```bash
DIRACDATA_SCHEMA=retail_analytics \
DIRACDATA_CATALOG=retail_pod \
.venv/bin/python v2/scripts/run_learning_pipeline.py \
  --metadata-descriptions v2/context/retail_analytics_metadata_descriptions.json \
  --query-history v2/data/query_history/retail_analytics_query_history.csv \
  --nl-sql-pairs v2/evals/Goldset_retail_queries.csv \
  --run-id retail_analytics_learning_local \
  --no-upload
```

Build only the semantic catalog:

```bash
.venv/bin/python v2/scripts/build_semantic_catalog.py \
  --metadata-descriptions v2/context/retail_analytics_metadata_descriptions.json \
  --sql-library v2/learning/artifacts/retail_analytics_sql_library_patterns_v2_20260615/sql_library.json \
  --run-id retail_analytics_semantic_catalog_local
```

## Evaluation And Benchmarks

DiracData includes retail/TPC-DS style eval files under `v2/evals/`:

- `Goldset_retail_queries.csv`: trusted NL-SQL examples
- `Benchmark_retail_customer_history.csv`: broader customer-history style query set
- `retail_schema_aware_benchmark.csv`: schema-aware retrieval benchmark
- `retail_column_retrieval_pairs_gold.csv`: gold retrieval pairs
- `retail_column_retrieval_pairs_history.csv`: query-history retrieval pairs

Run the baseline retrieval eval:

```bash
PYTHONPATH=v2/src .venv/bin/python v2/scripts/run_retail_baseline_eval.py \
  --questions v2/evals/retail_schema_aware_benchmark.csv \
  --semantic-catalog-path v2/learning/artifacts/retail_analytics_semantic_catalog_patterns_v2_20260615/semantic_catalog.json \
  --output-dir v2/data/uat_runs/retail_baseline_local
```

Run column retrieval evaluation:

```bash
PYTHONPATH=v2/src .venv/bin/python v2/scripts/evaluate_column_retrieval.py \
  --pairs v2/evals/retail_column_retrieval_pairs_gold.csv \
  --semantic-catalog-path v2/learning/artifacts/retail_analytics_semantic_catalog_patterns_v2_20260615/semantic_catalog.json
```

## What Makes DiracData Different

Most text-to-SQL demos emphasize prompting. DiracData emphasizes the harness:

- **Context first**: the agent receives compact, evidence-scored context instead
  of a giant schema dump.
- **Pattern grounded**: query history and gold NL-SQL pairs become searchable SQL
  patterns and assertions.
- **Agentic but gated**: models reason, but cannot skip dry-run and validation.
- **Clarification as a feature**: undefined or ambiguous business terms stop the
  workflow before SQL execution.
- **Smaller-model friendly**: the goal is to make Haiku, GLM, Qwen, and small GPT
  models useful through better context, not by always escalating to the largest
  model.

## Roadmap

- Stronger schema-aware retrieval using NL-SQL pairs, column cards, and rerankers.
- Join intelligence from both query history and inferred pairwise key evidence.
- Semantic assertions learned from approved SQL patterns.
- Runtime row-count probes for fanout and grain validation.
- Cleaner dependency packaging and reproducible setup.
- Larger benchmark coverage across retail, fintech, and additional schemas.

## Search Keywords

Text-to-SQL, NL2SQL, semantic catalog, semantic layer, data agents, AI analytics,
agentic BI, query history mining, schema-aware retrieval, SQL generation, SQL
validation, LangChain, Bedrock, Anthropic Claude, OpenAI, DuckDB, TPC-DS, retail
analytics, data-agent harness.

## Contributing

This repo is evolving quickly. Good contributions are:

- new schema/query-history testbeds
- gold NL-SQL pairs with expected tables, columns, joins, and results
- retrieval and join-mining benchmarks
- failing traces where the agent produced plausible but wrong SQL
- packaging, docs, and reproducibility improvements

## License

License is not defined yet.

