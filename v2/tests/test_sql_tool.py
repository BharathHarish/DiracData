import unittest
from pathlib import Path

from diracdata_v2.query import DuckDBEngine
from diracdata_v2.tools.sql import build_sql_dry_run_tool, validate_sql


class FakeDryRunEngine:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def list_tables(self):
        return ["payments"]

    def query(self, sql, max_rows):
        self.queries.append(sql)
        return type(
            "Result",
            (),
            {
                "columns": ["explain_key", "explain_value"],
                "rows": [("physical_plan", "plan")],
            },
        )()


class SQLToolTests(unittest.TestCase):
    def test_validate_allows_select_scoped_tables(self):
        result = validate_sql("SELECT * FROM payments", available_tables={"payments"})
        self.assertEqual(result["status"], "ok")

    def test_validate_allows_explain_select_scoped_tables(self):
        result = validate_sql("EXPLAIN SELECT * FROM payments", available_tables={"payments"})
        self.assertEqual(result["status"], "ok")

    def test_validate_rejects_explain_analyze(self):
        result = validate_sql("EXPLAIN ANALYZE SELECT * FROM payments", available_tables={"payments"})
        self.assertEqual(result["status"], "error")

    def test_validate_rejects_mutation(self):
        result = validate_sql("DROP TABLE payments", available_tables={"payments"})
        self.assertEqual(result["status"], "error")

    def test_validate_rejects_unknown_tables(self):
        result = validate_sql("SELECT * FROM unknown_table", available_tables={"payments"})
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["unknown_tables"], ["unknown_table"])

    def test_validate_allows_cte_names(self):
        sql = """
        WITH base AS (
            SELECT * FROM payments
        )
        SELECT * FROM base
        """
        result = validate_sql(sql, available_tables={"payments"})
        self.assertEqual(result["status"], "ok")

    def test_validate_allows_leading_probe_or_final_comments(self):
        probe = validate_sql("-- probe: count rows\nSELECT * FROM payments", available_tables={"payments"})
        final = validate_sql("-- final: answer query\nWITH base AS (SELECT * FROM payments) SELECT * FROM base", available_tables={"payments"})

        self.assertEqual(probe["status"], "ok")
        self.assertEqual(final["status"], "ok")

    def test_distinct_value_probes_keep_columns_and_rows_aligned(self):
        engine = DuckDBEngine(data_root=Path("data"), schema_name="fintech_schema")

        product_area = engine.query("SELECT DISTINCT product_area FROM orders ORDER BY 1", max_rows=20)
        kyc_status = engine.query("SELECT DISTINCT kyc_status FROM user_attributes ORDER BY 1", max_rows=20)

        self.assertEqual(product_area.columns, ["product_area"])
        self.assertIn(("checkout",), product_area.rows)
        self.assertEqual(kyc_status.columns, ["kyc_status"])
        self.assertIn(("verified",), kyc_status.rows)

    def test_duckdb_engine_runs_explain_without_wrapping_as_subquery(self):
        engine = DuckDBEngine(data_root=Path("data"), schema_name="fintech_schema")

        result = engine.query("EXPLAIN SELECT * FROM orders", max_rows=5)

        self.assertGreaterEqual(len(result.columns), 1)
        self.assertGreaterEqual(len(result.rows), 1)

    def test_sql_dry_run_runs_explain_only(self):
        engine = FakeDryRunEngine()
        tool = build_sql_dry_run_tool(engine=engine)

        result = tool.invoke({"sql": "SELECT * FROM payments"})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["explain_sql"], "EXPLAIN SELECT * FROM payments")
        self.assertEqual(engine.queries, ["EXPLAIN SELECT * FROM payments"])

    def test_sql_dry_run_rejects_mutation(self):
        engine = FakeDryRunEngine()
        tool = build_sql_dry_run_tool(engine=engine)

        result = tool.invoke({"sql": "DROP TABLE payments"})

        self.assertEqual(result["status"], "error")
        self.assertEqual(engine.queries, [])


if __name__ == "__main__":
    unittest.main()
