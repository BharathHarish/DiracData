# Local TPC-DS Harness

Generate the local TPC-DS scale factor 1 parquet files:

```bash
python3 scripts/generate_tpcds_parquet.py --scale-factor 1 --force
```

Run DuckDB smoke queries against the parquet files:

```bash
python3 scripts/smoke_tpcds_duckdb.py
```

Generate simulated TPC-DS query history:

```bash
python3 scripts/generate_tpcds_query_history.py --count 750 --output-path data/query_history/tpcds_query_history.csv --seed 42
```

Validate the generated query history CSV:

```bash
python3 -m unittest tests/test_query_history_csv_smoke.py -v
```

Generate simulated retail analytics query history:

```bash
.venv/bin/python scripts/generate_retail_analytics_query_history.py \
  --count 150 \
  --output-path data/query_history/retail_analytics_query_history.csv \
  --seed 20260607
```

Smoke test configured object storage:

```bash
.venv/bin/python scripts/smoke_object_store.py
```

Upload local TPC-DS parquet files to the configured lake bucket:

```bash
.venv/bin/python scripts/upload_tpcds_parquet_to_lake.py --data-dir data/tpcds/parquet/sf1 --prefix tpcds/sf1
```

Generate a retail analytics schema variant for schema-generalization testing:

```bash
.venv/bin/python scripts/generate_retail_analytics_parquet.py --force
```

Upload the retail analytics variant to a separate MinIO/S3 lake prefix:

```bash
.venv/bin/python scripts/upload_tpcds_parquet_to_lake.py \
  --data-dir data/retail_analytics/parquet/sf1 \
  --prefix retail_analytics/sf1
```

Query TPC-DS parquet from MinIO/S3 through DuckDB:

```bash
python3 scripts/smoke_tpcds_duckdb_s3.py
```

Run a small learning flow and write learning artifacts:

```bash
.venv/bin/python scripts/smoke_learning_flow.py --run-id learn_smoke
```

This uses the generic catalog settings from `.env`:

```text
DIRACDATA_QUERY_ENGINE=duckdb
DIRACDATA_CATALOG=commerce_pod
DIRACDATA_DATABASE=analytics
DIRACDATA_SCHEMA=main
DIRACDATA_CATALOG_CONFIG=conf/catalogs/commerce_pod.minio.json
```

For the retail analytics schema, override:

```text
DIRACDATA_CATALOG=retail_pod
DIRACDATA_SCHEMA=retail_analytics
DIRACDATA_CATALOG_CONFIG=conf/catalogs/retail_analytics.minio.json
```

Default paths:

- Parquet files: `data/tpcds/parquet/sf1`
- Retail analytics parquet files: `data/retail_analytics/parquet/sf1`
- Scratch DuckDB database: `data/tpcds/tpcds_sf1.duckdb`
- Query history CSV: `data/query_history/tpcds_query_history.csv`
- Artifact bucket: `diracdata`
- Lake bucket: `lake`

Generated data is ignored by git.

## Data Analyst Agent UAT Suite

The repeatable UAT inventory lives in:

```text
tests/harness/data_analyst_uat.csv
```

Each row is one turn. Rows with the same `case_id` run on the same checked-point
thread in `turn_index` order. The current suite has 16 customer-style
conversations and 55 turns. The CSV emphasizes natural business follow-ups,
exact scalar answers when known, essential SQL table/column coverage,
clarification behavior, prior-result reuse, and forbidden SQL fragments for
common semantic mistakes such as address-role confusion.

Supported expected behaviors are:

- `execute_sql`: factual analytical answer must execute SQL.
- `clarify`: question is under-specified and must not execute SQL.
- `explain_only`: metadata or prior-result explanation must not execute SQL.
- `inspect_data`: profile/value inspection may use learned profile artifacts or
  bounded SQL.

Run the full CSV against the default direct Anthropic profiles:

```bash
.venv/bin/python scripts/run_data_analyst_uat_suite.py
```

Run the online Jewelry customer slice across Sonnet, Haiku, and Qwen:

```bash
.venv/bin/python scripts/run_data_analyst_uat_suite.py \
  --case-id uat_002 \
  --model-profile anthropic_sonnet_46 \
  --model-profile anthropic_haiku_45 \
  --model-profile bedrock_qwen3_next_80b_a3b_ap_south_1 \
  --model-profile bedrock_qwen3_coder_480b_a35b_ap_south_1 \
  --model-profile gemini_2_5_flash \
  --model-profile openai_gpt_5_nano
```

Run the metadata exploration case across the same models:

```bash
.venv/bin/python scripts/run_data_analyst_uat_suite.py \
  --case-id uat_010 \
  --model-profile anthropic_sonnet_46 \
  --model-profile anthropic_haiku_45 \
  --model-profile bedrock_qwen3_next_80b_a3b_ap_south_1 \
  --model-profile bedrock_qwen3_coder_480b_a35b_ap_south_1 \
  --model-profile gemini_2_5_flash \
  --model-profile openai_gpt_5_nano
```

Reports are written under `/private/tmp/diracdata_uat_reports/<timestamp>/` by
default, including `summary.csv`, `UAT_run_<timestamp>.csv`, per-case stdout,
stderr, and JSONL traces.
