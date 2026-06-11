"""Register local TPC-DS parquet files in DuckDB and run smoke queries."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.query_engines import DuckDBRuntime


DEFAULT_DATA_DIR = Path("data/tpcds/parquet/sf1")


def register_tpcds_parquet_views(runtime: DuckDBRuntime, data_dir: Path) -> list[str]:
    """Harness helper for the local TPC-DS parquet dataset."""
    return runtime.register_parquet_views(data_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    runtime = DuckDBRuntime(":memory:")
    tables = register_tpcds_parquet_views(runtime, args.data_dir)

    print(f"Registered {len(tables)} parquet-backed views from {args.data_dir}")
    print("First tables:", ", ".join(tables[:8]))

    row_count_tables = ["store_sales", "customer", "item", "date_dim"]
    for table in row_count_tables:
        count = runtime.row_count(table)
        print(f"{table}: {count:,} rows")

    print("\nRevenue by year sample:")
    rows = runtime.query(
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
    for year, revenue in rows:
        print(f"{year}: {revenue}")

    runtime.close()


if __name__ == "__main__":
    main()
