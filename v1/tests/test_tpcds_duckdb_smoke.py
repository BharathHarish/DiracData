from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.query_engines import DuckDBRuntime

DATA_DIR = Path("data/tpcds/parquet/sf1")


def register_tpcds_parquet_views(runtime: DuckDBRuntime, data_dir: Path) -> list[str]:
    """Harness helper for the local TPC-DS parquet dataset."""
    return runtime.register_parquet_views(data_dir)


class TpcdsDuckdbSmokeTest(unittest.TestCase):
    def test_tpcds_sf1_parquet_is_queryable_with_duckdb(self) -> None:
        if not DATA_DIR.exists():
            raise unittest.SkipTest("TPC-DS parquet data has not been generated")

        runtime = DuckDBRuntime(":memory:")
        tables = register_tpcds_parquet_views(runtime, DATA_DIR)

        self.assertEqual(len(tables), 24)
        self.assertEqual(runtime.row_count("store_sales"), 2_880_404)
        self.assertEqual(runtime.row_count("customer"), 100_000)

        revenue_rows = runtime.query(
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

        self.assertEqual(
            revenue_rows,
            [
                (1998, Decimal("927771565.58")),
                (1999, Decimal("912621579.19")),
                (2000, Decimal("932928816.40")),
                (2001, Decimal("917918948.37")),
                (2002, Decimal("922846230.33")),
            ],
        )
        runtime.close()
