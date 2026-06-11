import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.learning import load_query_history_csv, query_history_fieldnames


class QueryHistoryLoaderTest(unittest.TestCase):
    def test_load_query_history_csv_decodes_json_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.csv"
            with path.open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=[
                        "statement_id",
                        "execution_status",
                        "statement_text",
                        "statement_type",
                        "compute",
                        "query_tags",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "statement_id": "stmt-1",
                        "execution_status": "FINISHED",
                        "statement_text": "SELECT 1",
                        "statement_type": "SELECT",
                        "compute": json.dumps({"type": "WAREHOUSE", "warehouse_id": "abc"}),
                        "query_tags": json.dumps({"pod": "tpcds_commerce"}),
                    }
                )

            records = load_query_history_csv(path)
            fieldnames = query_history_fieldnames(path)

        self.assertEqual(
            fieldnames,
            [
                "statement_id",
                "execution_status",
                "statement_text",
                "statement_type",
                "compute",
                "query_tags",
            ],
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].statement_id, "stmt-1")
        self.assertEqual(records[0].statement_text, "SELECT 1")
        self.assertEqual(records[0].execution_status, "FINISHED")
        self.assertEqual(records[0].values["compute"]["type"], "WAREHOUSE")
        self.assertEqual(records[0].values["query_tags"]["pod"], "tpcds_commerce")
