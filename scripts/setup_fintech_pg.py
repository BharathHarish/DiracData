#!/usr/bin/env python3
"""Seed the fintech Postgres (orders + payments) + a lake `customers` dimension for the multi-engine
UAT. Idempotent (safe to re-run). The DSN comes from ENV/--dsn (never hardcoded); data is deterministic
(generate_series), so the printed ground truths are reproducible.

    DIRACDATA_SOURCE_ORDERS_PG_DSN=postgresql://USER@localhost:5433/fintech \
        PYTHONPATH=src .venv/bin/python scripts/setup_fintech_pg.py            # seed
    ... scripts/setup_fintech_pg.py --check                                    # verify counts only
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

N_ORDERS = 5000          # deterministic size -> reproducible ground truths
N_CUSTOMERS = 500

_DDL = [
    "DROP TABLE IF EXISTS payments",
    "DROP TABLE IF EXISTS orders",
    """CREATE TABLE orders (
        order_id     integer PRIMARY KEY,
        customer_id  integer NOT NULL,
        order_ts     timestamptz NOT NULL,
        status       text NOT NULL,
        amount       numeric(10,2) NOT NULL,
        items        jsonb NOT NULL,
        tags         text[] NOT NULL)""",
    f"""INSERT INTO orders
        SELECT g,
               (g % {N_CUSTOMERS}) + 1,
               TIMESTAMP '2024-01-01 00:00:00+00' + ((g % 365) * INTERVAL '1 day') + ((g % 24) * INTERVAL '1 hour'),
               (ARRAY['placed','shipped','delivered','cancelled'])[(g % 4) + 1],
               round((10 + (g * 7 % 500))::numeric, 2),
               jsonb_build_object('sku', 'S' || (g % 50), 'qty', (g % 5) + 1),
               ARRAY['t' || (g % 3), 'c' || (g % 7)]
        FROM generate_series(1, {N_ORDERS}) AS g""",
    """CREATE TABLE payments (
        payment_id integer PRIMARY KEY,
        order_id   integer NOT NULL,
        paid_ts    timestamptz NOT NULL,
        method     text NOT NULL,
        amount     numeric(10,2) NOT NULL,
        currency   text NOT NULL)""",
    """INSERT INTO payments
        SELECT o.order_id, o.order_id, o.order_ts + INTERVAL '1 hour',
               (ARRAY['card','upi','wallet'])[(o.order_id % 3) + 1],
               o.amount, 'USD'
        FROM orders o WHERE o.status <> 'cancelled'""",
]

_TRUTHS = [
    ("orders", "SELECT COUNT(*) FROM orders"),
    ("payments", "SELECT COUNT(*) FROM payments"),
    ("cancelled orders", "SELECT COUNT(*) FROM orders WHERE status = 'cancelled'"),
    ("distinct customers", "SELECT COUNT(DISTINCT customer_id) FROM orders"),
    ("total payment amount (2024)", "SELECT SUM(amount) FROM payments"),
    ("total paid, method=card", "SELECT SUM(amount) FROM payments WHERE method = 'card'"),
]


def _dsn(arg: str | None) -> str:
    dsn = arg or os.environ.get("DIRACDATA_SOURCE_ORDERS_PG_DSN") or os.environ.get("DIRACDATA_TEST_PG_DSN")
    if not dsn:
        sys.exit("No DSN: pass --dsn or set DIRACDATA_SOURCE_ORDERS_PG_DSN / DIRACDATA_TEST_PG_DSN")
    return dsn


def _pg(dsn: str):
    try:
        import adbc_driver_postgresql.dbapi as pg
    except ImportError:
        sys.exit("Install the postgres driver: pip install adbc-driver-postgresql adbc-driver-manager")
    return pg.connect(dsn)


def seed_pg(dsn: str) -> None:
    con = _pg(dsn)
    with con.cursor() as cur:
        for stmt in _DDL:
            cur.execute(stmt)
    con.commit()
    print("[pg] seeded orders + payments")
    _report(con)
    con.close()


def check_pg(dsn: str) -> None:
    con = _pg(dsn)
    _report(con)
    con.close()


def _report(con) -> None:
    with con.cursor() as cur:
        for label, sql in _TRUTHS:
            cur.execute(sql)
            print(f"    {label:<32} = {cur.fetchone()[0]}")


def seed_lake(data_root: Path) -> None:
    """Write the `customers` dimension into a local DuckDB/parquet lake (a DIFFERENT engine), so the
    cross-source UAT joins Postgres orders/payments against it."""
    import duckdb
    out = data_root / "fintech_lake" / "parquet"
    out.mkdir(parents=True, exist_ok=True)
    path = (out / "customers.parquet").as_posix()
    con = duckdb.connect(":memory:")
    con.execute(f"""COPY (
        SELECT g AS customer_id,
               'Customer ' || g AS name,
               (ARRAY['smb','mid','enterprise'])[(g % 3) + 1] AS segment,
               (ARRAY['NA','EU','APAC','LATAM'])[(g % 4) + 1] AS region
        FROM range(1, {N_CUSTOMERS + 1}) t(g)
    ) TO '{path}' (FORMAT PARQUET)""")
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{path}')").fetchone()[0]
    con.close()
    print(f"[lake] wrote customers dimension: {n} rows -> {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=None, help="Postgres DSN (else ENV).")
    ap.add_argument("--data-root", default=str(ROOT / "data"), help="Lake root for the customers parquet.")
    ap.add_argument("--check", action="store_true", help="Only print the ground-truth counts.")
    ap.add_argument("--skip-lake", action="store_true")
    args = ap.parse_args()
    dsn = _dsn(args.dsn)
    if args.check:
        check_pg(dsn)
        return 0
    seed_pg(dsn)
    if not args.skip_lake:
        seed_lake(Path(args.data_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
