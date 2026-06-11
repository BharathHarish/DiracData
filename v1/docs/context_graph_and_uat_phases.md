# Context Graph And Customer UAT Phases

DiracData should prove that learned context can make smaller models behave like careful analysts. The next implementation phases should therefore improve the customer-facing test harness first, then add richer learned structure one layer at a time.

## Phase 1: Customer-Pattern UAT

Goal: replace benchmark-style prompts with natural business conversations.

Scope:

- Keep multi-turn conversations with realistic follow-ups.
- Preserve exact result checks where the answer is deterministic.
- Reduce brittle requirements around internal tool order and grounding ids.
- Keep essential checks for SQL execution, table/column usage, forbidden address-role mistakes, clarification behavior, and prior-result reuse.

Stage gates:

- Unit: CSV loader groups conversations and validates turn order.
- Unit: evaluator catches missing SQL, unexpected SQL, missing result, forbidden SQL fragments, and missing essential tables/columns.
- Integration: one or two selected cases run through the CLI with direct Anthropic.
- E2E/UAT: full CSV runs across Sonnet, Haiku, and Qwen after each agent change.

Primary artifact:

```text
tests/harness/data_analyst_uat.csv
```

## Phase 2: Retail Query-History Simulation

Goal: create realistic query history for the `retail_analytics` schema so learned joins and query patterns are not inherited from TPC-DS naming.

Scope:

- Generate 100-150 successful Databricks-style query-history rows.
- Include realistic families: customer geography, demographics, online/store/mail channels, refunds, inventory, marketing campaigns, financial metrics, and retention.
- Include a small number of failed rows for filtering tests.
- Dedupe exact SQL only during learning.
- Keep generated data/scripts separate from core agent code.

Stage gates:

- Unit: generated CSV has required Databricks-style fields and status distribution.
- Integration: query-history loader filters successful scoped queries.
- E2E: join discovery on `retail_analytics` improves or preserves join coverage versus no-history discovery.

Primary future artifact:

```text
data/query_history/retail_analytics_query_history.csv
```

## Phase 3: Context Graph Artifacts

Goal: build a graph search layer from learned artifacts without bloating the agent-facing join JSONL.

Canonical storage:

```text
active/context_graph/nodes.jsonl
active/context_graph/edges.jsonl
active/context_graph/query_patterns.jsonl
active/context_graph/events.jsonl
active/context_graph/manifest.json
active/context_graph/context_graph.pkl
```

The JSONL files are the canonical auditable artifacts. The NetworkX pickle is only a fast local/session cache and must be rebuildable from JSONL.

Node types:

- `table`
- `column`
- `metric`
- `sql_template`
- `business_term`
- `default_policy`
- `query_pattern`

Edge types:

- `table_has_column`
- `column_joins_column`
- `metric_uses_column`
- `template_uses_metric`
- `template_uses_join`
- `business_term_maps_to_metric`
- `business_term_maps_to_column`
- `query_pattern_used_join`
- `query_pattern_used_filter`

Stage gates:

- Unit: graph builder creates expected nodes and typed edges from fixture artifacts.
- Integration: graph artifacts round-trip through object storage and rebuild the same graph.
- E2E: learned graph contains expected retail join paths and metric/template links.

## Phase 4: Graph Search And Join Path Tooling

Goal: give the agent compact structural plans instead of full graph dumps.

Tool behavior:

- Find direct joins between two tables.
- Find weighted join paths across multiple tables.
- Return related metrics/templates/default policies when they sit on the same path.
- Return ambiguity warnings when multiple plausible paths exist.
- Never dump the full graph to the model.

Example output:

```json
{
  "status": "ok",
  "join_path": [
    {
      "left": "online_purchases.billing_client_ref",
      "right": "clients.client_record",
      "clause": "online_purchases.billing_client_ref = clients.client_record",
      "confidence": "high"
    }
  ],
  "related_metrics": ["online_jewelry_customers"],
  "related_templates": ["count_online_jewelry_customers_by_demographic_location_year"],
  "ambiguities": []
}
```

Stage gates:

- Unit: path search prefers higher-confidence query-history joins over weak inferred joins.
- Unit: ambiguous address-role paths are surfaced rather than hidden.
- Integration: graph tool returns compact plans from active artifacts.
- E2E: UAT cases using address roles and multi-table metrics improve versus joinable-pairs-only baseline.

## Phase 5: Runtime Graph Events

Goal: let validated runtime discoveries improve later turns and sessions.

Scope:

- Runtime join recovery appends an event to `active/context_graph/events.jsonl`.
- The active graph can merge validated events into JSONL edge artifacts.
- `joinable_pairs.jsonl` remains the compact compatibility artifact for answer-time retrieval.
- Run-scoped learning artifacts stay immutable.

Stage gates:

- Unit: runtime event schema validation.
- Integration: validated join recovery writes an event and updates active graph state.
- E2E: second query reuses the newly discovered graph edge without rediscovery.

## Phase 6: Context Compiler

Goal: reduce model burden by compiling only the relevant context for a turn.

Inputs:

- User question.
- Verified conversation summary.
- Business grounding matches.
- Schema/profile matches.
- Context graph paths.
- Metric/template/default policy matches.

Output:

- Intent frame.
- Candidate metrics and definitions.
- Required tables/columns.
- Join path plan.
- Defaults and ambiguities.
- Required verification checks.

Stage gates:

- Unit: deterministic compiler fixtures for simple, ambiguous, and multi-table questions.
- Integration: agent receives compiled context instead of broad static context.
- E2E: Qwen/Haiku tool use and follow-up accuracy improve on customer UAT.

## Phase 7: Truth Compiler And Scored Evals

Goal: move from trace pass/fail toward analyst-quality scoring.

Checks:

- Clarification gate.
- Grounding gate.
- SQL safety and schema gate.
- Join-path gate.
- Result verification gate.
- Data-quality/freshness gate.
- Final-answer humility gate.

Score dimensions:

- Tool use correctness.
- Hallucinated tables/columns.
- Entity-to-schema mapping.
- SQL correctness.
- Result accuracy.
- Follow-up state handling.
- Business-definition grounding.
- Clarification quality.

Stage gates:

- Unit: each gate fails on a known bad trace.
- Integration: scoring report explains failure layer per turn.
- E2E: full UAT report compares Sonnet, Haiku, Qwen, and future providers with the same harness.
