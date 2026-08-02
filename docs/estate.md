# Declaring a data estate across multiple databases

A **data estate** is the set of sources DiracData reasons over — some tables in Postgres, some in a
lake, more elsewhere. Each source is an independent `QueryEngine`; the agent picks which one holds
what, queries each in its own dialect, and combines the reduced results. You declare the estate in one
of three ways (they all build the same `SourceRegistry`):

## 1. YAML manifest (recommended for the CLI)

One file lists every source. Secrets are `${ENV}` references, never literals. See
[docs/estate.fintech.yaml](estate.fintech.yaml):

```yaml
default: fintech_lake            # the "home" source (drives the default dialect)
sources:
  - name: fintech_lake           # DuckDB / parquet lake
    kind: duckdb
    data_root: ./data            # reads ./data/<schema>/parquet/*.parquet
    schema: fintech_lake
  - name: orders_pg              # Postgres
    kind: postgres
    dsn: ${FINTECH_PG_DSN}       # secret from ENV
    read_only: true
    timeout_s: 60
    params: {schema: public}
```

Add a source by adding a list item — any supported `kind` (`duckdb`, `postgres`; `mysql`/`trino`/
`clickhouse` as connectors land). Nothing else changes.

## 2. ENV (good for containers / CI)

```bash
export DIRACDATA_SOURCES=fintech_lake,orders_pg
export DIRACDATA_SOURCE_FINTECH_LAKE_KIND=duckdb
export DIRACDATA_SOURCE_FINTECH_LAKE_DATA_ROOT="$PWD/data"
export DIRACDATA_SOURCE_FINTECH_LAKE_SCHEMA=fintech_lake
export DIRACDATA_SOURCE_ORDERS_PG_KIND=postgres
export DIRACDATA_SOURCE_ORDERS_PG_DSN="postgresql://$USER@localhost:5433/fintech"
```

## 3. Programmatic (embedding the framework)

```python
from diracdata.engines import SourceRegistry, EngineSpec
reg = SourceRegistry([
    EngineSpec(name="fintech_lake", kind="duckdb", data_root="./data", schema="fintech_lake"),
    EngineSpec(name="orders_pg", kind="postgres", dsn=os.environ["FINTECH_PG_DSN"]),
])
```

`SourceRegistry.load(path)` is the one entry point the CLIs use: YAML `path` if given, else
`DIRACDATA_SOURCES` from ENV, else a single-source fallback.

## Run the learning agent + the query agent across the estate

```bash
export FINTECH_PG_DSN="postgresql://$USER@localhost:5433/fintech"

# LEARNING agent over the whole estate: per-source fabric + cross-source binding discovery
PYTHONPATH=src .venv/bin/python scripts/learn.py --estate --sources docs/estate.fintech.yaml --quiet

# QUERY agent, single source (pushed down to Postgres)
PYTHONPATH=src .venv/bin/python scripts/ask.py --sources docs/estate.fintech.yaml \
    --question "What is the total payment amount collected in 2024?"

# QUERY agent, CROSS-SOURCE (Postgres payments/orders reconciled with the lake customers dimension)
PYTHONPATH=src .venv/bin/python scripts/ask.py --sources docs/estate.fintech.yaml \
    --question "What is the total payment amount by customer segment?"
```

The query agent reads the estate map (each source's dialect + tables + verified bindings), reduces at
each source, and `combine_results` joins the small results in DuckDB — every number verified and
traced to a stored result. `--sources` also works with `--no-stream --quiet`.
