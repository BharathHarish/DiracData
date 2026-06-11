"""Query TPC-DS parquet files from MinIO/S3 with DuckDB."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import settings_from_env
from diracdata.query_engines import query_engine_from_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = settings_from_env(".env")
    engine = query_engine_from_settings(settings)

    tables = engine.list_tables()
    print(
        f"Registered {len(tables)} catalog-backed views for "
        f"{settings.catalog}.{settings.database}.{settings.schema}"
    )
    for table in ["store_sales", "customer", "item", "date_dim"]:
        print(f"{table}: {engine.row_count(table):,} rows")

    rows = engine.query(
        """
        SELECT
            d.d_year,
            round(sum(ss.ss_net_paid), 2) AS store_net_paid
        FROM store_sales AS ss
        JOIN date_dim AS d
            ON ss.ss_sold_date_sk = d.d_date_sk
        GROUP BY d.d_year
        ORDER BY d.d_year
        LIMIT 5
        """
    ).rows
    print("Revenue by year sample:")
    for year, revenue in rows:
        print(f"{year}: {revenue}")
    engine.close()


if __name__ == "__main__":
    main()
