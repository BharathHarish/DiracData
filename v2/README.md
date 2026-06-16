# DiracData v2

Active clean-room implementation of DiracData's context fabric and primitive
data-agent harness.

The v2 rule is still simple: keep only context and workflow machinery that can
change SQL accuracy.

## What v2 Owns

- `context/`: schema descriptions and compact description documents
- `data/query_history/`: query-history inputs for learning
- `evals/`: gold NL-SQL pairs and benchmark query sets
- `learning/artifacts/`: generated schema graph, schema AST, SQL library, and semantic catalog artifacts
- `scripts/`: runnable build, eval, and agent CLIs
- `src/diracdata_v2/`: active package code
- `tests/`: unit and integration coverage

## Runtime Shape

```text
schema descriptions + query history + gold NL-SQL pairs
   -> learning pipeline
   -> SQL library + semantic catalog
   -> context compiler
   -> typed / gated / supervisor agent workflow
   -> SQL dry run
   -> steward validation
   -> final read-only execution
```

## Active Contracts

- `schema_graph`: domain/entity/table/column graph
- `schema_ast`: traversal-friendly hierarchy for schema context
- `sql_library`: query-history, gold-pair, and self-play SQL patterns
- `semantic_catalog`: runtime cards for columns, tables, joins, values, and patterns
- `compiled_context`: compact context packet for the agent
- `typed_workflow`: harness-owned gates around model reasoning

## Development Rules

- Do not import from `v1/` in v2 code or scripts.
- Keep provider credentials in the root `.env`; never commit real keys.
- Default generated artifact uploads to local storage unless S3/MinIO is explicitly configured.
- Keep prompts generic and schema-agnostic.
- Prefer learned artifacts and explicit context packets over schema-specific code paths.
- Tests may use tiny synthetic schemas, but runtime code must remain schema-agnostic.

## Useful Commands

Run focused workflow tests:

```bash
PYTHONPATH=v2/src .venv/bin/python -m unittest \
  v2.tests.test_typed_workflow \
  v2.tests.test_primitive_data_agent \
  v2.tests.test_primitive_gated_workflow \
  v2.tests.test_primitive_supervisor_workflow \
  v2.tests.test_sql_tool -v
```

Run the primitive harness:

```bash
DIRACDATA_SCHEMA=retail_analytics DIRACDATA_CATALOG=retail_pod \
.venv/bin/python v2/scripts/run_primitive_agent.py \
  --workflow typed \
  --interactive \
  --stream \
  --stream-format text \
  --question "count customers by state"
```

## Generated Assets

Versioned artifacts:

- query-history CSVs under `data/query_history/`
- curated benchmark files under `evals/`
- selected learning artifacts under `learning/artifacts/`

Ignored local outputs:

- generated parquet/data folders under `data/*/`
- local UAT traces under `data/uat_runs/`
- local reranker/model outputs under `models/`
- local object-store mirrors under `../.diracdata/`

One-time generation scripts live in `scripts/` and should remain runnable rather
than being copied into notebooks or ad hoc shell history.
