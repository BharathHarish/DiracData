import csv
import json
import tempfile
import unittest
from pathlib import Path

from diracdata_v2.learning.sql_library import (
    SQLLibraryBuilder,
    build_sql_patterns,
    mine_history_templates,
    query_history_coverage,
)


class FakePatternGenerator:
    def complete(self, messages):
        return json.dumps(
            {
                "patterns": [
                    {
                        "entry_id": "history:payments:payment_status:abc",
                        "canonical_question": "Count payments by status.",
                        "paraphrases": ["How many payments have each status?"],
                        "intent_signature": {
                            "grain": "payment status",
                            "measure": "count payments",
                            "filters": [],
                            "dimensions": ["payment status"],
                            "time_window": "",
                        },
                        "summary": "Counts payment attempts by outcome.",
                        "assumptions": [],
                    }
                ]
            }
        )


class SQLLibraryLearningTests(unittest.TestCase):
    def test_query_history_coverage_marks_missing_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["execution_status", "statement_text"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "execution_status": "FINISHED",
                        "statement_text": "SELECT p.payment_status FROM payments p",
                    }
                )

            coverage = query_history_coverage(
                query_history_path=path,
                table_columns={"payments": ["payment_status", "amount"]},
            )

        self.assertEqual(coverage["columns_covered"], ["payments.payment_status"])
        self.assertEqual(coverage["columns_missing"], ["payments.amount"])

    def test_history_templates_are_key_value_sql_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["execution_status", "statement_text"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "execution_status": "FINISHED",
                        "statement_text": (
                            "SELECT p.payment_status, COUNT(*) "
                            "FROM payments p GROUP BY p.payment_status"
                        ),
                    }
                )

            templates = mine_history_templates(
                query_history_path=path,
                table_columns={"payments": ["payment_status", "amount"]},
                limit=10,
            )

        self.assertEqual(len(templates), 1)
        entry = next(iter(templates.values()))
        self.assertEqual(entry["source"], "query_history")
        self.assertEqual(entry["review_status"], "observed")
        self.assertEqual(entry["columns"], ["payments.payment_status"])
        json.dumps(entry)

    def test_builder_combines_history_and_self_play(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root = root / "data"
            schema_name = "merchant_schema"
            parquet_root = data_root / schema_name / "parquet" / "sf1"
            parquet_root.mkdir(parents=True)
            history_path = root / "history.csv"
            with history_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["execution_status", "statement_text"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "execution_status": "FINISHED",
                        "statement_text": "SELECT p.payment_status FROM payments p",
                    }
                )
            import duckdb

            duckdb.connect(":memory:").execute(
                f"""
                COPY (
                  SELECT 'SUCCESS' AS payment_status, 100.0::DOUBLE AS amount
                )
                TO '{(parquet_root / "payments.parquet").as_posix()}'
                (FORMAT PARQUET)
                """
            )
            schema_graph = {
                "nodes": [
                    {
                        "id": "column:payments.payment_status",
                        "kind": "column",
                        "sql_ref": "payments.payment_status",
                        "metadata": {"role": "status"},
                    },
                    {
                        "id": "column:payments.amount",
                        "kind": "column",
                        "sql_ref": "payments.amount",
                        "metadata": {"role": "measure"},
                    },
                ]
            }

            result = SQLLibraryBuilder().build(
                schema_graph=schema_graph,
                query_history_path=history_path,
                data_root=data_root,
                catalog="fintech_pod",
                database="analytics",
                schema=schema_name,
                run_id="test",
                output_dir=root / "out",
            )

        self.assertEqual(result.document["coverage"]["columns_missing"], ["payments.amount"])
        self.assertIn("self_play:payments.amount", result.document["entries"])
        self.assertEqual(
            result.document["entries"]["self_play:payments.amount"]["validation"]["status"],
            "passed",
        )
        self.assertIn("patterns", result.document)
        self.assertTrue(result.document["patterns"])

    def test_build_sql_patterns_uses_generator_output(self):
        entries = {
            "history:payments:payment_status:abc": {
                "source": "query_history",
                "review_status": "observed",
                "tables": ["payments"],
                "columns": ["payments.payment_status"],
                "sql": "SELECT p.payment_status, COUNT(*) FROM payments p GROUP BY 1",
            }
        }
        schema_graph = {
            "nodes": [
                {
                    "id": "column:payments.payment_status",
                    "kind": "column",
                    "name": "payment_status",
                    "sql_ref": "payments.payment_status",
                    "description": "Outcome of the payment attempt.",
                }
            ]
        }

        patterns = build_sql_patterns(
            entries=entries,
            schema_graph=schema_graph,
            generator=FakePatternGenerator(),
            batch_size=10,
            limit=10,
        )

        pattern = patterns["pattern:history:payments:payment_status:abc"]
        self.assertEqual(pattern["canonical_question"], "Count payments by status.")
        self.assertEqual(pattern["intent_signature"]["measure"], "count payments")


if __name__ == "__main__":
    unittest.main()
