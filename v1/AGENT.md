# AGENT.md

This file is the quick orientation guide for agents working in this repository.

DiracData is a standalone Python package for trustworthy data agents. The product has two phases:

1. Learning: turn a scoped data pod into durable semantic context.
2. Answering: use learned context to answer business questions with verification.

Build generic product interfaces first. Keep TPC-DS and other benchmark details in harness scripts, tests, or catalog configs, not in product APIs.

## Quick Reading Index

Start here:

- `README.md`: project thesis and current status.
- `docs/architecture.md`: two-phase learning and answering architecture.
- `docs/low_level_design.md`: proposed data models and interfaces.
- `.env.example`: local/dev environment shape.
- `conf/catalogs/commerce_pod.minio.json`: harness catalog config for MinIO-backed parquet.

Then inspect package folders:

- `src/diracdata/config/`: environment-backed settings.
- `src/diracdata/storage/`: object store abstraction for artifacts.
- `src/diracdata/backends/`: catalog/table resolution.
- `src/diracdata/query_engines/`: SQL execution engines.
- `src/diracdata/llms/`: provider-agnostic chat model utilities.
- `src/diracdata/learning/`: learning-phase modules.
- `src/diracdata/agents/`: LangGraph answer-time agent entrypoints and tools.
- `src/diracdata/evals/`: future evaluation harnesses.

**Agent Construction and Tool Wiring**

The data-analyst agent is built by the factory `create_data_analyst_agent` in `src/diracdata/agents/data_analyst_agent.py`. The factory composes a runtime with these pieces:

- **Model:** resolved via `diracdata.llms.agent_chat_model_from_settings` or provided directly to the factory.
- **System prompt:** loaded from `src/diracdata/agents/prompts/SYSTEM_PROMPT_V1.md`.
- **Tools:** created by `diracdata.tools.build_data_analyst_tools` and include schema, join, profile, and SQL tools. Tools are implemented as LangChain `@tool` wrappers and are wired to a `LearnedArtifactRepository`, `DiracDataSettings`, and a configured `QueryEngine`.
- **Persistence & state:** checkpointer via `agents.checkpointers.checkpointer_from_settings` and store via `agents.stores.store_from_settings`.
- **Artifacts:** `LearnedArtifactRepository` (in `src/diracdata/agents/artifacts.py`) loads active learning artifacts, supports searching descriptions, profile reads, join persistence, and preflight checks.

Runtime wrapper `DataAnalystAgentRuntime` exposes `preflight()`, `invoke()`, `stream()`, and `run()` for programmatic usage. The recommended workflow in the system prompt (schema -> descriptions -> profiles -> join discovery -> SQL -> verify) is enforced by tool responsibilities (for example, `run_sql_tool` validates read-only SQL with `sqlglot` and restricts tables to the scoped pod).


Harness and local setup:

- `scripts/generate_tpcds_parquet.py`: generate local TPC-DS parquet.
- `scripts/generate_tpcds_query_history.py`: generate synthetic query history CSV.
- `scripts/generate_retail_analytics_query_history.py`: generate retail analytics query history CSV.
- `scripts/generate_fintech_query_history.py`: generate fintech schema query history CSV.
- `scripts/upload_tpcds_parquet_to_lake.py`: upload local parquet to MinIO/S3 lake bucket.
- `scripts/generate_retail_analytics_parquet.py`: create a retail analytics harness variant plus catalog/context files.
- `scripts/generate_fintech_schema_parquet.py`: create the compact fintech schema plus catalog/context/grounding files.
- `scripts/smoke_tpcds_duckdb.py`: local parquet DuckDB smoke test.
- `scripts/smoke_tpcds_duckdb_s3.py`: catalog-driven MinIO/S3 DuckDB smoke test.
- `scripts/smoke_object_store.py`: object store smoke test.
- `scripts/smoke_learning_flow.py`: small learning flow over the configured catalog.
- `scripts/run_learning_uat.py`: full learning UAT with artifact coverage verification.
- `scripts/inspect_learning_artifacts.py`: inspect active learned artifacts and vector-search hits.
- `scripts/run_join_discovery_uat.py`: join-only UAT from an existing profile/samples run.
- `scripts/run_data_analyst_agent_uat.py`: live data analyst agent UAT with streaming and JSONL trace output.
- `conf/business_contexts/commerce_pod.json`: harness-only business context for semantic description testing.
- `tests/harness/README.md`: command reference for local harness.

## Folder Responsibilities

### `config/`

Environment parsing and runtime settings.

`DiracDataSettings` should contain generic customer-facing settings such as:

- `DIRACDATA_QUERY_ENGINE`
- `DIRACDATA_SQL_DIALECT`
- `DIRACDATA_CATALOG`
- `DIRACDATA_DATABASE`
- `DIRACDATA_SCHEMA`
- `DIRACDATA_CATALOG_CONFIG`
- object-store settings

Do not add dataset-specific env vars such as TPC-DS paths here.

### `storage/`

Artifact storage only.

Use this for learned contexts, profiles, samples, traces, eval outputs, and other DiracData-owned artifacts.

Current implementations:

- `LocalObjectStore`
- `S3ObjectStore`, compatible with AWS S3 and MinIO

Keep source data separate from artifacts. Source parquet may live in a lake bucket, but learning outputs should go to the artifact bucket.

### `backends/`

Catalog and table-location resolution.

This layer answers:

- What catalog/database/schema is selected?
- What tables exist?
- Where is each table physically located?

Current implementation:

- `ConfigCatalogResolver`: JSON config-backed resolver.

Future implementations may include:

- Postgres catalog resolver
- Iceberg catalog resolver
- Databricks catalog resolver
- Snowflake catalog resolver

### `query_engines/`

SQL execution engines.

This layer answers:

- How do we register/query selected tables?
- How do we inspect schema?
- How do we execute SQL?

Current implementation:

- `DuckDBQueryEngine`
- `DuckDBRuntime`

Future implementations may include:

- Postgres query engine
- Databricks SQL query engine
- Snowflake query engine
- BigQuery query engine

### `learning/`

Learning-phase product modules.

This is where schema learning should be built:

- query history loading
- table sampling
- column profiling
- table profiling
- semantic descriptions
- join graph discovery
- learned context building

Current implementation:

- `models.py`: learning dataclasses and JSON-safe conversion.
- `collector.py`: table sampling, column profiling, and learning artifact writes.
- `descriptions.py`: prompt-driven metadata description generation with per-table/column-batch validation.
- `joins.py`: query-history and sample/profile-based joinable pair discovery.
- `context_graph.py`: context graph, query-pattern, BM25+, and RRF artifact generation.
- `embeddings.py`: optional vector embedding artifact generation from retrieval documents.
- `learning_pipeline.py`: customer-facing orchestration for collection, descriptions, joins, context graph building, embeddings, and context training.
- `training.py`: learned context artifact creation plus stable active context publishing.
- `paths.py`: learning artifact key helpers.
- `query_history.py`: query history CSV loading.
- `prompts/schema_descriptions.md`: editable learning prompt.

Context graph JSONL artifacts are canonical. `context_graph.pkl` is only an optional NetworkX cache when retrieval extras are installed. Do not require answer-time agents to inspect a full graph dump; future tools should return compact paths, related metrics/templates, and ambiguity warnings.

Retrieval artifacts are generated during learning:

- `retrieval/documents.jsonl`
- `retrieval/bm25_plus_index.json`
- `retrieval/rrf_manifest.json`

Embedding artifacts are generated in a separate learning step:

- `embeddings/manifest.json`
- `embeddings/column_embeddings.jsonl`
- `embeddings/faiss_hnsw.index`
- `embeddings/faiss_hnsw_metadata.json`

Column retrieval is the primary semantic retrieval unit. Tables are containers used for context and conflict resolution.

Learning code should depend on `QueryEngine`, `CatalogResolver`, `ObjectStore`, and `ChatModelClient` interfaces. It should not directly write into `data/` or directly assume TPC-DS.

### `llms/`

Shared chat model utilities for learning and future agents.

Current implementation:

- `chat_models.py`: `ChatModelClient` protocol, `ChatModelMessage`, and `LangChainChatModelClient`.

Use LangChain `init_chat_model` for provider-agnostic model setup. Test fakes belong in `tests/`, not production code.

### `agents/`

LangGraph answer-time agent modules.

Current implementation:

- `data_analyst_agent.py`: `create_agent` factory and runtime wrapper.
- `settings.py`: LangGraph stream-mode parsing and answer-time runtime settings.
- `artifacts.py`: active learned-context repository used by tools.
- `checkpointers.py`: answer-time checkpointer factory.
- `stores.py`: answer-time LangGraph store factory.
- `prompt_loader.py`: prompt loading helpers.
- `prompts/SYSTEM_PROMPT_V1.md`: main data analyst agent prompt.
- `src/diracdata/tools/`: schema, profile, join, and safe read-only SQL tools.

Keep orchestration in LangGraph `create_agent` unless a custom graph is truly required. Agent tools should read active learned artifacts through `ObjectStore`, execute SQL through `QueryEngine`, and return compact structured payloads.

Future answer-time behavior may add:

- epistemic SQL compiler
- result verification
- trace records
- result summarization
- final answer contracts

### `tools/`

Product-facing LangChain tool wrappers shared by answer-time agents.

Current implementation:

- `factory.py`: builds the full data analyst toolset.
- `schema_tools.py`: learned metadata search and table/column description tools.
- `profile_tools.py`: learned profile value lookup.
- `join_tools.py`: learned joinable pair lookup.
- `sql_tools.py`: safe read-only SQL execution tool.

Do not put core runtime infrastructure here. Query execution belongs in `query_engines/`; storage belongs in `storage/`; catalog resolution belongs in `backends/`.

### `core/`

Shared low-level helpers only.

Current example:

- SQL string/identifier quoting helpers

Avoid turning `core/` into a broad dumping ground.

## Environment Shape

Current local development env:

```text
DIRACDATA_MODE=dev
DIRACDATA_QUERY_ENGINE=duckdb
DIRACDATA_SQL_DIALECT=duckdb
DIRACDATA_CATALOG=commerce_pod
DIRACDATA_DATABASE=analytics
DIRACDATA_SCHEMA=main
DIRACDATA_CATALOG_CONFIG=conf/catalogs/commerce_pod.minio.json
DIRACDATA_DUCKDB_DATABASE=:memory:
DIRACDATA_LEARNING_SAMPLE_LIMIT=1000
DIRACDATA_LEARNING_DISTINCT_LIMIT=1000
DIRACDATA_LEARNING_TOP_VALUES_LIMIT=20
DIRACDATA_LEARNING_CONTEXT_DISTINCT_VALUES_LIMIT=50
DIRACDATA_LEARNING_DESCRIPTION_COLUMN_BATCH_SIZE=50
DIRACDATA_LEARNING_RUN_ID=dev_learning_run
DIRACDATA_LLM_PROVIDER=anthropic
DIRACDATA_LLM_MODEL=claude-sonnet-4-6
DIRACDATA_LLM_MAX_TOKENS=8192
DIRACDATA_LLM_TEMPERATURE=0
DIRACDATA_ANTHROPIC_BASE_URL=https://api.anthropic.com
DIRACDATA_ANTHROPIC_API_KEY=...
DIRACDATA_AGENT_LLM_PROVIDER=anthropic
DIRACDATA_AGENT_LLM_MODEL=claude-sonnet-4-6
DIRACDATA_AGENT_LLM_MAX_TOKENS=8192
DIRACDATA_AGENT_LLM_TEMPERATURE=0
DIRACDATA_AGENT_STREAMING=off
DIRACDATA_AGENT_STREAM_MODES=updates,messages
DIRACDATA_AGENT_STREAM_VERSION=v2
DIRACDATA_AGENT_CHECKPOINTER=memory
DIRACDATA_AGENT_STORE=memory
DIRACDATA_AGENT_SCHEMA_SEARCH_LIMIT=10
DIRACDATA_AGENT_PROFILE_VALUES_LIMIT=25
DIRACDATA_AGENT_SQL_MAX_ROWS=100
DIRACDATA_AGENT_SQL_TIMEOUT_SECONDS=30
DIRACDATA_OBJECT_STORE=s3
DIRACDATA_ARTIFACT_BUCKET=diracdata
DIRACDATA_LAKE_BUCKET=lake
DIRACDATA_S3_ENDPOINT_URL=http://localhost:9000
DIRACDATA_AWS_REGION=us-east-1
DIRACDATA_AWS_ACCESS_KEY_ID=minioadmin
DIRACDATA_AWS_SECRET_ACCESS_KEY=minioadmin
```

For AWS S3, keep the same object-store interface and remove the MinIO endpoint or set it to empty. Prefer IAM roles or external secret management for real production credentials.

## Catalog Configs

Catalog config maps customer-facing names to physical tables.

Customer-facing env:

```text
DIRACDATA_CATALOG=commerce_pod
DIRACDATA_DATABASE=analytics
DIRACDATA_SCHEMA=main
```

Physical table details belong in catalog config, for example:

```json
{
  "catalog": "commerce_pod",
  "database": "analytics",
  "schema": "main",
  "tables": [
    {
      "name": "store_sales",
      "path": "s3://lake/tpcds/sf1/store_sales.parquet",
      "format": "parquet"
    }
  ]
}
```

Do not add one env var per dataset/table.

## Testing And Verification

Run the full unit test suite:

```bash
python3 -m unittest discover -s tests -v
```

Run local parquet smoke:

```bash
python3 scripts/smoke_tpcds_duckdb.py
```

Run MinIO/S3 object-store smoke:

```bash
.venv/bin/python scripts/smoke_object_store.py
```

Run catalog-driven DuckDB over MinIO/S3 smoke:

```bash
python3 scripts/smoke_tpcds_duckdb_s3.py
```

Run a focused live learning smoke for semantic description quality:

```bash
.venv/bin/python scripts/smoke_learning_flow.py \
  --run-id live_income_band_quality \
  --tables income_band \
  --business-context-file conf/business_contexts/commerce_pod.json
```

For full catalog learning, leave `--tables` empty or pass `--tables all`. Description prompts are dynamically grouped by active column count using `DIRACDATA_LEARNING_DESCRIPTION_COLUMN_BATCH_SIZE`.

Run full learning UAT:

```bash
.venv/bin/python scripts/run_learning_uat.py \
  --run-id uat_full_schema \
  --tables all \
  --business-context-file conf/business_contexts/commerce_pod.json
```

Learning writes immutable run artifacts under:

```text
artifacts/learning/{catalog}/{database}/{schema}/{run_id}/...
```

It also publishes the latest successful learned context under stable active paths:

```text
artifacts/learning/{catalog}/{database}/{schema}/active/descriptions/metadata_descriptions.json
artifacts/learning/{catalog}/{database}/{schema}/active/joins/joinable_pairs.jsonl
artifacts/learning/{catalog}/{database}/{schema}/active/contexts/learned_context.json
artifacts/learning/{catalog}/{database}/{schema}/active/manifest.json
```

Join discovery is two-step:

1. Query-history extraction: keep successful SQL only, filter to SQL that mentions at least two current pod tables, dedupe exact SQL strings only, extract candidate joins with `ChatModelClient`.
2. Semantic/sample discovery: use type compatibility, normalized column-name similarity, profile values, and DuckDB sample joins.

Both steps feed deterministic validation before writing `joinable_pairs.jsonl`.

Keep `joinable_pairs.jsonl` compact for answer-time agents:

```json
{"left_table":"store_sales","left_column":"ss_item_sk","right_table":"item","right_column":"i_item_sk","join_type":"many_to_one","confidence":"high"}
```

Do not add source query ids, reasons, detailed evidence, or score fields to the JSONL artifact.

Run learning flow smoke:

```bash
.venv/bin/python scripts/smoke_learning_flow.py --run-id learn_smoke
```

Run the data analyst agent UAT with a one-run model override:

```bash
.venv/bin/python scripts/run_data_analyst_agent_uat.py \
  --question "count all male customers from california" \
  --agent-llm-provider anthropic \
  --agent-model claude-haiku-4-5-20251001 \
  --stream \
  --stream-modes updates,messages \
  --no-interactive
```

Use `--interactive` to keep the same checked-point thread open for follow-up questions.

Run gated live learning e2e with Anthropic:

```bash
DIRACDATA_RUN_LIVE_LEARNING=1 python3 -m unittest tests/test_learning_pipeline_live.py -v
```

Only claim MinIO/S3 behavior works after running the relevant smoke script against the local server.

## Design Rules

- Build generic customer-facing interfaces first.
- Keep benchmark/harness details out of product APIs.
- Use `catalog/database/schema` as the customer-facing data selection model.
- Use catalog resolvers to map those names to physical storage.
- Use query engines to execute SQL.
- Use object stores for DiracData artifacts.
- Keep source data and learned artifacts separated.
- Learning writes artifacts only through `ObjectStore`.
- Prefer small, testable modules over broad utility files.
- Avoid vague folders like `utils/` or `infra/`.
- Add tests for every new boundary module.
