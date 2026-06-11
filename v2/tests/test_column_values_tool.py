import unittest
from pathlib import Path

from diracdata_v2.query import DuckDBEngine
from diracdata_v2.settings import V2Settings
from diracdata_v2.tools.column_values import build_column_values_tool


class ColumnValuesToolTests(unittest.TestCase):
    def setUp(self):
        self.settings = V2Settings(data_root=Path("data"), schema="fintech_schema")
        self.engine = DuckDBEngine(data_root=self.settings.data_root, schema_name=self.settings.schema)
        self.tool = build_column_values_tool(settings=self.settings, engine=self.engine)

    def test_returns_distinct_values_with_counts(self):
        result = self.tool.invoke({"table_name": "user_attributes", "column_name": "kyc_status"})

        self.assertEqual(result["status"], "ok")
        values = {item["value"] for item in result["values"]}
        self.assertIn("verified", values)
        self.assertIn("pending", values)
        self.assertTrue(all("row_count" in item for item in result["values"]))

    def test_search_text_filters_values(self):
        result = self.tool.invoke(
            {
                "table_name": "user_attributes",
                "column_name": "state",
                "search_text": "maha",
            }
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual([item["value"] for item in result["values"]], ["Maharashtra"])

    def test_rejects_unknown_column(self):
        result = self.tool.invoke({"table_name": "user_attributes", "column_name": "missing"})

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "Unknown column")
        self.assertIn("kyc_status", result["available_columns"])


if __name__ == "__main__":
    unittest.main()
