from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.learning import load_query_history_csv, query_history_fieldnames


QUERY_HISTORY_PATH = Path("data/query_history/tpcds_query_history.csv")
RETAIL_QUERY_HISTORY_PATH = Path("data/query_history/retail_analytics_query_history.csv")
FINTECH_QUERY_HISTORY_PATH = Path("data/query_history/fintech_schema_query_history.csv")


class QueryHistoryCsvSmokeTest(unittest.TestCase):
    def test_simulated_query_history_csv_shape(self) -> None:
        if not QUERY_HISTORY_PATH.exists():
            raise unittest.SkipTest("simulated query history CSV has not been generated")

        rows = load_query_history_csv(QUERY_HISTORY_PATH)
        fieldnames = query_history_fieldnames(QUERY_HISTORY_PATH)

        self.assertGreaterEqual(len(rows), 500)
        self.assertLessEqual(len(rows), 1000)
        self.assertIn("statement_id", fieldnames)
        self.assertIn("statement_text", fieldnames)
        self.assertIn("execution_status", fieldnames)
        self.assertIn("compute", fieldnames)
        self.assertIn("query_source", fieldnames)

        statement_texts = [row.statement_text for row in rows]
        joined_sql = "\n".join(statement_texts)
        self.assertIn("JOIN date_dim", joined_sql)
        self.assertIn("JOIN customer", joined_sql)
        self.assertIn("JOIN item", joined_sql)
        self.assertTrue(any(row.execution_status == "FAILED" for row in rows))
        self.assertTrue(any(row.execution_status == "FINISHED" for row in rows))
        self.assertIsInstance(rows[0].values["compute"], dict)
        self.assertIsInstance(rows[0].values["query_tags"], dict)

    def test_fintech_query_history_csv_shape(self) -> None:
        if not FINTECH_QUERY_HISTORY_PATH.exists():
            raise unittest.SkipTest("fintech query history CSV has not been generated")

        rows = load_query_history_csv(FINTECH_QUERY_HISTORY_PATH)
        fieldnames = query_history_fieldnames(FINTECH_QUERY_HISTORY_PATH)

        self.assertGreaterEqual(len(rows), 500)
        self.assertLessEqual(len(rows), 1000)
        self.assertIn("statement_id", fieldnames)
        self.assertIn("statement_text", fieldnames)
        self.assertIn("execution_status", fieldnames)
        self.assertIn("compute", fieldnames)
        self.assertIn("query_source", fieldnames)

        statement_texts = [row.statement_text for row in rows]
        joined_sql = "\n".join(statement_texts)
        self.assertIn("JOIN payment_attributes", joined_sql)
        self.assertIn("JOIN user_attributes", joined_sql)
        self.assertIn("JOIN users", joined_sql)
        self.assertIn("JOIN payments", joined_sql)
        self.assertNotIn("customer", joined_sql)
        self.assertNotIn("TPCDS", joined_sql.upper())
        self.assertTrue(any(row.execution_status == "FAILED" for row in rows))
        self.assertTrue(any(row.execution_status == "FINISHED" for row in rows))
        self.assertIsInstance(rows[0].values["compute"], dict)
        self.assertIsInstance(rows[0].values["query_tags"], dict)

    def test_retail_analytics_query_history_csv_shape(self) -> None:
        if not RETAIL_QUERY_HISTORY_PATH.exists():
            raise unittest.SkipTest("retail analytics query history CSV has not been generated")

        rows = load_query_history_csv(RETAIL_QUERY_HISTORY_PATH)
        fieldnames = query_history_fieldnames(RETAIL_QUERY_HISTORY_PATH)

        self.assertGreaterEqual(len(rows), 100)
        self.assertLessEqual(len(rows), 150)
        self.assertIn("statement_id", fieldnames)
        self.assertIn("statement_text", fieldnames)
        self.assertIn("execution_status", fieldnames)
        self.assertIn("compute", fieldnames)
        self.assertIn("query_source", fieldnames)

        statement_texts = [row.statement_text for row in rows]
        joined_sql = "\n".join(statement_texts)
        self.assertIn("JOIN clients", joined_sql)
        self.assertIn("JOIN calendar_days", joined_sql)
        self.assertIn("JOIN merchandise", joined_sql)
        self.assertIn("JOIN marketing_campaigns", joined_sql)
        self.assertNotIn("store_sales", joined_sql)
        self.assertNotIn("customer_demographics", joined_sql)
        self.assertTrue(any(row.execution_status == "FAILED" for row in rows))
        self.assertTrue(any(row.execution_status == "FINISHED" for row in rows))
        self.assertIsInstance(rows[0].values["compute"], dict)
        self.assertIsInstance(rows[0].values["query_tags"], dict)
