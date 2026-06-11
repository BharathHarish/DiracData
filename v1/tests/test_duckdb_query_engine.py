import json
from pathlib import Path
import sys
import tempfile
import unittest

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config.settings import DiracDataSettings
from diracdata.query_engines import query_engine_from_settings


class DuckDBQueryEngineTest(unittest.TestCase):
    def test_duckdb_query_engine_uses_generic_catalog_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            parquet_path = tmp_path / "orders.parquet"
            catalog_path = tmp_path / "catalog.json"

            con = duckdb.connect(":memory:")
            con.execute(
                """
                CREATE TABLE orders AS
                SELECT * FROM (
                    VALUES
                        (1, 100, 12.50),
                        (2, 101, 25.00),
                        (3, 100, 30.00)
                ) AS t(order_id, customer_id, revenue)
                """
            )
            con.execute(f"COPY orders TO '{parquet_path}' (FORMAT parquet)")
            con.close()

            catalog_path.write_text(
                json.dumps(
                    {
                        "catalog": "commerce_pod",
                        "database": "analytics",
                        "schema": "main",
                        "tables": [
                            {
                                "name": "orders",
                                "path": str(parquet_path),
                                "format": "parquet",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            settings = DiracDataSettings(
                query_engine="duckdb",
                sql_dialect="duckdb",
                catalog="commerce_pod",
                database="analytics",
                schema="main",
                catalog_config=catalog_path,
                duckdb_database=":memory:",
            )

            engine = query_engine_from_settings(settings)
            try:
                self.assertEqual(engine.list_tables(), ["orders"])
                self.assertEqual(engine.row_count("orders"), 3)
                schema = engine.describe_table("orders")
                self.assertEqual([column.name for column in schema], ["order_id", "customer_id", "revenue"])
                result = engine.query(
                    """
                    SELECT customer_id, sum(revenue) AS total_revenue
                    FROM orders
                    GROUP BY customer_id
                    ORDER BY customer_id
                    """
                )
                self.assertEqual(result.columns, ["customer_id", "total_revenue"])
                self.assertEqual(len(result.rows), 2)
            finally:
                engine.close()

