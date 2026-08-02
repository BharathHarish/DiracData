# Postgres setup (multi-engine fintech UAT)

The multi-engine tests and the fintech UAT need a Postgres to point at. Local unit tests need **no**
Postgres (they use a second DuckDB); only the `DIRACDATA_TEST_PG_DSN`-gated tests and the fintech
end-to-end use a real server.

## 1. Install + start Postgres (macOS, no sudo)

```bash
brew install postgresql@16
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"
export LC_ALL=C LANG=C                       # avoids the macOS "postmaster became multithreaded" start error
PGDATA="$PWD/data/pgdata"                     # under data/ (gitignored)
initdb -D "$PGDATA" -U "$USER" --auth=trust
pg_ctl -D "$PGDATA" -l "$PWD/data/pg.log" -o "-p 5433" start
createdb -p 5433 -U "$USER" fintech
```

Any other Postgres works too — just set the DSN below to it.

## 2. Install the Arrow driver

The connector uses ADBC (Arrow-native — maps `jsonb`/arrays/`timestamptz` for free). It is an
optional dependency (conceptually `diracdata[postgres]`):

```bash
.venv/bin/pip install adbc-driver-postgresql adbc-driver-manager
```

## 3. Seed the fintech data

```bash
export DIRACDATA_SOURCE_ORDERS_PG_DSN="postgresql://$USER@localhost:5433/fintech"
PYTHONPATH=src .venv/bin/python scripts/setup_fintech_pg.py
```

This creates + seeds `orders` and `payments` in Postgres (deterministic; incl. `jsonb`/array/
`timestamptz`) and writes a `customers` dimension into a local DuckDB/parquet lake
(`data/fintech_lake/`) for the cross-source run. It prints the ground truths (e.g. 5000 orders, 3750
payments, total payment amount 2024 = 973750.00). Re-run any time — it is idempotent. `--check` prints
the counts without reseeding.

## 4. Point the harness at it

- Single Postgres source (ENV): set `DIRACDATA_TEST_PG_DSN` to run the live tests, and/or configure a
  multi-source registry:
  ```bash
  export DIRACDATA_SOURCES=orders_pg,fintech_lake
  export DIRACDATA_SOURCE_ORDERS_PG_KIND=postgres
  export DIRACDATA_SOURCE_ORDERS_PG_DSN="postgresql://$USER@localhost:5433/fintech"
  export DIRACDATA_SOURCE_FINTECH_LAKE_KIND=duckdb
  export DIRACDATA_SOURCE_FINTECH_LAKE_DATA_ROOT="$PWD/data/fintech_lake"
  export DIRACDATA_SOURCE_FINTECH_LAKE_SCHEMA=.        # parquet lives directly under data/fintech_lake/parquet
  ```
- The DSN is always read from ENV, never written to a file. A YAML manifest may reference it as
  `dsn: ${ORDERS_DSN}`.

## Stopping / restarting

```bash
pg_ctl -D "$PWD/data/pgdata" stop           # stop
# restart later:
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" LC_ALL=C LANG=C
pg_ctl -D "$PWD/data/pgdata" -l "$PWD/data/pg.log" -o "-p 5433" start
```
