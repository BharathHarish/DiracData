# Tests

The default suite is intentionally non-live and should be safe to run without
LLM, MinIO, or AWS access:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Focused fintech learning harness checks:

```bash
.venv/bin/python scripts/generate_fintech_schema_parquet.py --force
.venv/bin/python scripts/generate_fintech_query_history.py \
  --count 750 \
  --unique-success-sql 60 \
  --output-path data/query_history/fintech_schema_query_history.csv
.venv/bin/python -m unittest \
  tests/test_fintech_query_history_generator.py \
  tests/test_fintech_learning_pipeline_artifacts.py \
  tests/test_query_history_csv_smoke.py \
  -v
```

Stageable learning runner:

```bash
.venv/bin/python scripts/run_learning_pipeline.py \
  --env-file .env \
  --run-id fintech_stageable_dev \
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
  --run-id fintech_stageable_dev \
  --query-history-path data/query_history/fintech_schema_query_history.csv \
  --artifact-strategy agentic \
  --context-mode linear \
  --start-stage join_discovery \
  --end-stage context_training
```

Learning UAT with artifact verification:

```bash
.venv/bin/python scripts/run_learning_uat.py \
  --env-file .env \
  --run-id fintech_stageable_dev \
  --business-context-file conf/business_contexts/fintech_schema.json \
  --business-grounding-file conf/business_grounding/fintech_pod.analytics.fintech_schema.yaml \
  --query-history-path data/query_history/fintech_schema_query_history.csv \
  --artifact-strategy agentic \
  --context-mode linear
```

FAISS/HNSW unit tests are opt-in because macOS native-library load order can
make FAISS collide with other OpenMP users inside a broad single-process test
run:

```bash
DIRACDATA_RUN_FAISS_TESTS=1 .venv/bin/python -m unittest tests/test_embedding_builder.py -v
```

Live fintech artifact verification is also opt-in. It expects active MinIO
learning artifacts for `fintech_pod.analytics.fintech_schema` and verifies
artifact shape plus BGE/FAISS vector-search behavior:

```bash
DIRACDATA_RUN_LIVE_FINTECH_ARTIFACTS=1 \
DIRACDATA_EXPECTED_LEARNING_RUN_ID=fintech_live_full_20260607_v2 \
DIRACDATA_OBJECT_STORE=minio \
DIRACDATA_CATALOG=fintech_pod \
DIRACDATA_DATABASE=analytics \
DIRACDATA_SCHEMA=fintech_schema \
DIRACDATA_CATALOG_CONFIG=conf/catalogs/fintech_schema.minio.json \
DIRACDATA_S3_ENDPOINT_URL=http://localhost:9000 \
DIRACDATA_LAKE_BUCKET=lake \
DIRACDATA_ARTIFACT_BUCKET=diracdata \
DIRACDATA_AWS_REGION=us-east-1 \
DIRACDATA_AWS_ACCESS_KEY_ID=minioadmin \
DIRACDATA_AWS_SECRET_ACCESS_KEY=minioadmin \
DIRACDATA_LEARNING_EMBEDDING_PROVIDER=bge \
DIRACDATA_LEARNING_EMBEDDING_LOCAL_FILES_ONLY=true \
DIRACDATA_LEARNING_VECTOR_INDEX_BACKEND=faiss \
DIRACDATA_LEARNING_VECTOR_INDEX_ALGORITHM=hnsw_flat \
DIRACDATA_LEARNING_VECTOR_INDEX_METRIC=inner_product \
.venv/bin/python -m unittest tests/test_fintech_live_learning_artifacts.py -v
```

Live learning UAT uses the real configured learning LLM. Test fakes exist only
inside unit tests for deterministic artifact-shape checks.
