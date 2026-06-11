# Learning Module Design

## Scope

The learning module converts one selected `catalog/database/schema` into durable semantic context.

Current assumption:

- One catalog
- One database
- One schema

Future support should allow cross-catalog, cross-database, and cross-schema learning by extending the scope model rather than rewriting collection logic.

## Flow

1. Resolve the selected catalog/database/schema.
2. List scoped tables through the configured query engine.
3. For each table, collect a bounded sample using `DIRACDATA_LEARNING_SAMPLE_LIMIT`.
4. For each column, collect type, null rate, distinct count, min/max where useful, top values, and bounded distinct values using `DIRACDATA_LEARNING_DISTINCT_LIMIT`.
5. Write table sample CSVs to object storage under `artifacts/learning/.../samples/`.
6. Write raw profile metadata to object storage.
7. Build an LLM prompt from structured business context plus compact schema/profile evidence.
8. Generate table and column short and long descriptions, batching active tables by column count when needed.
9. Write `metadata_descriptions.json` to object storage.
10. Discover joinable table-column pairs from query history, schema/profile evidence, and sample CSV validation.
11. Build context graph, query-pattern, BM25+, and RRF artifacts from schema profiles, descriptions, joins, query history, and business grounding.
12. Build optional vector embedding artifacts from retrieval documents as a separate phase.
13. Train a learned context artifact that links profiles, LLM context, generated descriptions, joinable pairs, context graph, retrieval indexes, and embedding artifacts.

## Artifact Layout

```text
artifacts/learning/{catalog}/{database}/{schema}/{run_id}/
  samples/{table}.csv
  profiles/table_profiles.json
  profiles/llm_context.json
  descriptions/batches/{table}/batch_001.json
  descriptions/metadata_descriptions.json
  joins/joinable_pairs.jsonl
  context_graph/nodes.jsonl
  context_graph/edges.jsonl
  context_graph/query_patterns.jsonl
  context_graph/manifest.json
  context_graph/context_graph.pkl
  retrieval/documents.jsonl
  retrieval/bm25_plus_index.json
  retrieval/rrf_manifest.json
  embeddings/manifest.json
  embeddings/column_embeddings.jsonl
  contexts/learned_context.json

artifacts/learning/{catalog}/{database}/{schema}/active/
  descriptions/metadata_descriptions.json
  joins/joinable_pairs.jsonl
  context_graph/nodes.jsonl
  context_graph/edges.jsonl
  context_graph/query_patterns.jsonl
  context_graph/events.jsonl
  context_graph/manifest.json
  context_graph/context_graph.pkl
  retrieval/documents.jsonl
  retrieval/bm25_plus_index.json
  retrieval/rrf_manifest.json
  embeddings/manifest.json
  embeddings/column_embeddings.jsonl
  contexts/learned_context.json
  manifest.json
```

Run-scoped paths are immutable audit artifacts. Active paths are stable pointers to the latest successfully trained context for the selected `catalog/database/schema`.

## Business Context

Learning should receive business context from the customer, not infer business meaning from schema names alone.

The `BusinessContext` API supports:

- `text`: plain-language pod context.
- `table_descriptions`: table-level business meaning.
- `column_descriptions`: known column-level business meaning.
- `glossary`: business terms and synonyms.

Harness-only example:

```text
conf/business_contexts/commerce_pod.json
```

Real deployments should replace that file with customer-provided pod context.

## LLM Design

Learning uses the shared `ChatModelClient` abstraction in `src/diracdata/llms/`.

The current production client is `LangChainChatModelClient`, built with LangChain `init_chat_model` so the learning path is provider-agnostic. Test fakes stay in `tests/`; no mock client should live in production learning code.

The prompt is stored in:

```text
src/diracdata/learning/prompts/schema_descriptions.md
```

The model is selected with:

```text
DIRACDATA_LLM_PROVIDER=anthropic
DIRACDATA_LLM_MODEL=claude-sonnet-4-6
DIRACDATA_LLM_MAX_TOKENS=8192
DIRACDATA_LLM_TEMPERATURE=0
DIRACDATA_ANTHROPIC_BASE_URL=https://api.anthropic.com
DIRACDATA_ANTHROPIC_API_KEY=...
```

Description generation is coverage-validated. If the model omits a table or column, the generator fails instead of writing partial semantic metadata.

Description prompts are batched by active column count:

```text
DIRACDATA_LEARNING_DESCRIPTION_COLUMN_BATCH_SIZE=50
```

The generator dynamically groups tables into bounded prompt batches. Wide tables are split when needed. The final `metadata_descriptions.json` is merged and validated against every profiled table and column.

When structured business grounding is supplied, the description prompt receives glossary terms, metric definitions, SQL templates, default policies, and ground-truth SQL examples as semantic evidence. The prompt should use those inputs to describe business meaning and synonyms, not to copy SQL into descriptions.

Run full schema UAT:

```bash
.venv/bin/python scripts/run_learning_uat.py \
  --run-id uat_full_schema \
  --tables all \
  --business-context-file conf/business_contexts/commerce_pod.json
```

## Join Discovery

Join discovery writes JSONL so answer-time agents can retrieve a compact trusted join graph.

Each row is intentionally small:

```json
{
  "left_table": "store_sales",
  "left_column": "ss_item_sk",
  "right_table": "item",
  "right_column": "i_item_sk",
  "join_type": "many_to_one",
  "confidence": "high"
}
```

Do not write source query ids, reasons, detailed scores, or evidence blobs into `joinable_pairs.jsonl`. Those are validation internals, not answer-time context.

Query-history mode:

- Load Databricks-style query history CSV.
- Keep only successful statements.
- Keep only statements that mention at least two tables in the current pod scope.
- Dedupe exact SQL strings only.
- Use the shared `ChatModelClient` to extract explicit join candidates from SQL batches.
- Validate candidates against current tables, columns, data types, profiles, and sample CSV joins.

No-history mode:

- Generate candidates from compatible data types, normalized column-name similarity, profile values, and sample CSV overlap.
- Verify candidates by running sample joins in DuckDB.

Artifact:

```text
artifacts/learning/{catalog}/{database}/{schema}/{run_id}/joins/joinable_pairs.jsonl
artifacts/learning/{catalog}/{database}/{schema}/active/joins/joinable_pairs.jsonl
```

Answer-time recovery:

- The agent-facing `join_discovery_tool` first reads the active join graph.
- If a requested table pair is missing, the tool can infer candidate keys from table names, column names, data types, and learned profile metadata.
- Candidate recovery must not use generated semantic descriptions. It should use compact schema/profile evidence so recovery is explainable and cheap.
- A candidate must pass name-shape compatibility before SQL validation. A `LIMIT 1` join match alone is not enough, because unrelated surrogate-key domains can share numeric values by coincidence.
- SQL validation runs a bounded read-only join probe through the configured query engine.
- Confirmed runtime joins are merged into the mutable active `joinable_pairs.jsonl`.
- Run-scoped join artifacts remain immutable. Runtime recovery updates only the active artifact so later turns and sessions can reuse the learned repair.

Run join-only UAT without regenerating descriptions:

```bash
.venv/bin/python scripts/run_join_discovery_uat.py \
  --run-id uat_full_schema_20260606 \
  --query-history-path data/query_history/tpcds_query_history.csv
```

## Context Graph And Retrieval Artifacts

Context graph building is a learning-stage artifact generation step, not an answer-time rediscovery step.

Inputs:

- Table and column profiles.
- `metadata_descriptions.json`.
- `joinable_pairs.jsonl`.
- Successful exact-deduped query history.
- Active or supplied business grounding.

Canonical graph artifacts:

```text
context_graph/nodes.jsonl
context_graph/edges.jsonl
context_graph/query_patterns.jsonl
context_graph/manifest.json
```

`context_graph/context_graph.pkl` is a rebuildable NetworkX cache when optional retrieval dependencies are installed. JSONL remains the source of truth.

Retrieval artifacts:

```text
retrieval/documents.jsonl
retrieval/bm25_plus_index.json
retrieval/rrf_manifest.json
```

Embedding artifacts are generated by a separate pipeline step from `retrieval/documents.jsonl`:

```text
embeddings/manifest.json
embeddings/column_embeddings.jsonl
embeddings/faiss_hnsw.index
embeddings/faiss_hnsw_metadata.json
```

Column retrieval is first-class. Table retrieval is treated as container/context retrieval for conflict resolution and surrounding schema awareness.

BM25+ is generated deterministically from learned descriptions, names, grounding text, joins, and query patterns. Vector embeddings are optional and provider-driven:

```text
DIRACDATA_LEARNING_EMBEDDING_PROVIDER=none
DIRACDATA_LEARNING_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
DIRACDATA_LEARNING_EMBEDDING_LOCAL_FILES_ONLY=false
DIRACDATA_LEARNING_VECTOR_INDEX_BACKEND=faiss
DIRACDATA_LEARNING_VECTOR_INDEX_ALGORITHM=hnsw_flat
DIRACDATA_LEARNING_VECTOR_INDEX_METRIC=inner_product
```

Set `DIRACDATA_LEARNING_EMBEDDING_PROVIDER=bge` after installing retrieval extras to generate BGE column embeddings. Set `DIRACDATA_LEARNING_EMBEDDING_LOCAL_FILES_ONLY=true` for cache-only reruns after the model has been downloaded once. When FAISS is available, the same step writes a rebuildable HNSW index and metadata next to the canonical embedding JSONL. If the provider or index backend is disabled or unavailable, the embedding step still writes a manifest describing why vectors or index files were not produced. Graph and BM25 artifacts should still succeed independently.

The main orchestration API is:

```python
from diracdata.learning import LearningPipeline
```

The pipeline exposes separate APIs for:

- `collect_data(...)`
- `generate_descriptions(...)`
- `discover_joins(...)`
- `build_context_graph(...)`
- `build_embeddings(...)`
- `build_query_libraries(...)`
- `build_nuance_artifacts(...)`
- `build_agentic_artifacts(...)`
- `train_context(...)`
- `load_run_state(...)`
- `run_stages(...)`

`run_stages(...)` is the main staged orchestration API for incremental runs. It
can start from any valid stage for the current learning strategy as long as the
required upstream artifacts already exist for the same `run_id`.

Deterministic stage order:

```text
data_collection
description_generation
join_discovery
context_graph_building
embedding_generation
query_library_building
nuance_building
context_training
```

Agentic stage order:

```text
data_collection
description_generation
join_discovery
context_graph_building
embedding_generation
agentic_artifact_generation
context_training
```

General staged runner:

```bash
.venv/bin/python scripts/run_learning_pipeline.py \
  --env-file .env \
  --run-id fintech_agentic_linear_001 \
  --business-context-file conf/business_contexts/fintech_schema.json \
  --business-grounding-file conf/business_grounding/fintech_pod.analytics.fintech_schema.yaml \
  --query-history-path data/query_history/fintech_schema_query_history.csv \
  --artifact-strategy agentic \
  --context-mode linear
```

Resume the same run from joins onward:

```bash
.venv/bin/python scripts/run_learning_pipeline.py \
  --env-file .env \
  --run-id fintech_agentic_linear_001 \
  --query-history-path data/query_history/fintech_schema_query_history.csv \
  --artifact-strategy agentic \
  --context-mode linear \
  --start-stage join_discovery \
  --end-stage context_training
```

Stricter UAT runner with artifact verification:

```bash
.venv/bin/python scripts/run_learning_uat.py \
  --env-file .env \
  --run-id fintech_agentic_linear_001 \
  --business-context-file conf/business_contexts/fintech_schema.json \
  --business-grounding-file conf/business_grounding/fintech_pod.analytics.fintech_schema.yaml \
  --query-history-path data/query_history/fintech_schema_query_history.csv \
  --artifact-strategy agentic \
  --context-mode linear
```
- `build_embeddings(...)`
- `train_context(...)`
- `run(...)`
