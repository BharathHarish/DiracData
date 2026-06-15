import csv
import json
import tempfile
import unittest
from pathlib import Path

from diracdata_v2.learning.sql_library import (
    SQLLibraryBuilder,
    build_sql_patterns,
    mine_nl_sql_pair_templates,
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

    def test_history_templates_store_validated_join_edges(self):
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
                            "SELECT c.state, SUM(o.amount) "
                            "FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
                            "GROUP BY c.state"
                        ),
                    }
                )

            templates = mine_history_templates(
                query_history_path=path,
                table_columns={
                    "orders": ["customer_id", "amount"],
                    "customers": ["customer_id", "state"],
                },
                limit=10,
            )

        entry = next(iter(templates.values()))
        self.assertEqual(
            entry["join_edges"],
            [
                {
                    "left_column": "customers.customer_id",
                    "right_column": "orders.customer_id",
                    "tables": ["customers", "orders"],
                    "sql_condition": "customers.customer_id = orders.customer_id",
                }
            ],
        )

    def test_nl_sql_pairs_become_trusted_library_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            pair_path = Path(tmp) / "trusted_pairs.csv"
            with pair_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "case_id",
                        "nl_query",
                        "sql",
                        "tables_used",
                        "columns_used",
                        "join_edges",
                        "difficulty",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "case_id": "gold_001",
                        "nl_query": "Show order value by customer state.",
                        "sql": (
                            "SELECT c.state, SUM(o.amount) "
                            "FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
                            "GROUP BY c.state"
                        ),
                        "tables_used": "orders;customers",
                        "columns_used": "orders.customer_id;orders.amount;customers.customer_id;customers.state",
                        "join_edges": "orders.customer_id = customers.customer_id",
                        "difficulty": "medium",
                    }
                )

            templates = mine_nl_sql_pair_templates(
                pair_paths=(pair_path,),
                table_columns={
                    "orders": ["customer_id", "amount"],
                    "customers": ["customer_id", "state"],
                },
                limit=None,
                review_status="approved",
            )

        self.assertEqual(len(templates), 1)
        entry_id, entry = next(iter(templates.items()))
        self.assertTrue(entry_id.startswith("nl_sql:gold_001:"))
        self.assertEqual(entry["source"], "nl_sql_pair")
        self.assertEqual(entry["review_status"], "approved")
        self.assertEqual(entry["canonical_question"], "Show order value by customer state.")
        self.assertEqual(entry["source_case_id"], "gold_001")
        self.assertEqual(entry["source_difficulty"], "medium")
        self.assertEqual(entry["tables"], ["customers", "orders"])
        self.assertEqual(
            entry["columns"],
            [
                "customers.customer_id",
                "customers.state",
                "orders.amount",
                "orders.customer_id",
            ],
        )
        self.assertEqual(
            entry["join_edges"],
            [
                {
                    "left_column": "customers.customer_id",
                    "right_column": "orders.customer_id",
                    "tables": ["customers", "orders"],
                    "sql_condition": "customers.customer_id = orders.customer_id",
                }
            ],
        )
        json.dumps(entry)

    def test_query_history_coverage_includes_trusted_nl_sql_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_path = root / "history.csv"
            with history_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["execution_status", "statement_text"])
                writer.writeheader()

            pair_path = root / "trusted_pairs.csv"
            with pair_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["case_id", "nl_query", "sql", "tables_used", "columns_used"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "case_id": "gold_001",
                        "nl_query": "Count customers by state.",
                        "sql": "SELECT c.state, COUNT(*) FROM customers c GROUP BY c.state",
                        "tables_used": "customers",
                        "columns_used": "customers.customer_id;customers.state",
                    }
                )

            coverage = query_history_coverage(
                query_history_path=history_path,
                table_columns={"customers": ["customer_id", "state"]},
                nl_sql_pair_paths=(pair_path,),
            )

        self.assertEqual(coverage["successful_queries"], 0)
        self.assertEqual(coverage["trusted_pair_queries"], 1)
        self.assertEqual(coverage["columns_missing"], [])
        self.assertEqual(
            coverage["columns_covered"],
            ["customers.customer_id", "customers.state"],
        )

    def test_builder_uses_trusted_pairs_for_patterns_and_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_path = root / "history.csv"
            with history_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["execution_status", "statement_text"])
                writer.writeheader()

            pair_path = root / "trusted_pairs.csv"
            with pair_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["case_id", "nl_query", "sql", "tables_used", "columns_used"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "case_id": "gold_001",
                        "nl_query": "Count payments by outcome.",
                        "sql": "SELECT p.payment_status, COUNT(*) FROM payments p GROUP BY p.payment_status",
                        "tables_used": "payments",
                        "columns_used": "payments.payment_status;payments.amount",
                    }
                )

            schema_graph = {
                "nodes": [
                    {
                        "id": "column:payments.payment_status",
                        "kind": "column",
                        "name": "payment_status",
                        "sql_ref": "payments.payment_status",
                        "description": "Business outcome of the payment attempt.",
                        "metadata": {"role": "status"},
                    },
                    {
                        "id": "column:payments.amount",
                        "kind": "column",
                        "name": "amount",
                        "sql_ref": "payments.amount",
                        "description": "Payment value.",
                        "metadata": {"role": "measure"},
                    },
                ]
            }

            result = SQLLibraryBuilder(pattern_limit=10).build(
                schema_graph=schema_graph,
                query_history_path=history_path,
                data_root=root / "data",
                catalog="generic_pod",
                database="analytics",
                schema="generic_schema",
                run_id="trusted_pairs_run",
                output_dir=root / "out",
                nl_sql_pair_paths=(pair_path,),
            )

        self.assertEqual(result.document["coverage"]["trusted_pair_queries"], 1)
        self.assertEqual(result.document["coverage"]["columns_missing"], [])
        entries = result.document["entries"]
        self.assertEqual(sum(1 for item in entries.values() if item["source"] == "nl_sql_pair"), 1)
        self.assertEqual(sum(1 for item in entries.values() if item["source"] == "self_play"), 0)
        patterns = result.document["patterns"]
        self.assertEqual(len(patterns), 1)
        pattern = next(iter(patterns.values()))
        self.assertEqual(pattern["source"], "nl_sql_pair")
        self.assertEqual(pattern["canonical_question"], "Count payments by outcome.")
        self.assertEqual(pattern["tables"], ["payments"])
        self.assertIn("payments.payment_status", pattern["columns"])

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
