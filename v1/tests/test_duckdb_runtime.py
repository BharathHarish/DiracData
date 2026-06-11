from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.query_engines import DuckDBRuntime


class DuckDBRuntimeTest(unittest.TestCase):
    def test_table_schema_and_query_helpers(self) -> None:
        runtime = DuckDBRuntime(":memory:")
        runtime.connection.execute(
            """
            CREATE TABLE example_orders (
                order_id INTEGER,
                customer_id INTEGER,
                revenue DECIMAL(10, 2)
            )
            """
        )
        runtime.connection.execute(
            """
            INSERT INTO example_orders VALUES
                (1, 100, 12.50),
                (2, 101, 25.00),
                (3, 100, 30.00)
            """
        )

        self.assertEqual(runtime.list_tables(), ["example_orders"])
        self.assertEqual(runtime.row_count("example_orders"), 3)

        schema = runtime.describe_table("example_orders")
        self.assertEqual([column.name for column in schema], ["order_id", "customer_id", "revenue"])
        self.assertEqual(schema[0].data_type, "INTEGER")

        result = runtime.query(
            """
            SELECT customer_id, sum(revenue) AS total_revenue
            FROM example_orders
            GROUP BY customer_id
            ORDER BY customer_id
            """
        )
        self.assertEqual(result.columns, ["customer_id", "total_revenue"])
        self.assertEqual(len(result.rows), 2)

        limited = runtime.query("SELECT * FROM example_orders ORDER BY order_id", max_rows=2)
        self.assertEqual(len(limited.rows), 2)
        runtime.close()
