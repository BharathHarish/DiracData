import json
import tempfile
import unittest
from pathlib import Path

from diracdata_v2.evals.schema_benchmark import BenchmarkCase
from diracdata_v2.retrieval.column_cards import column_cards_from_catalog
from diracdata_v2.retrieval.evaluator import evaluate_column_retrieval
from diracdata_v2.retrieval.training_data import build_column_retrieval_pairs, write_column_retrieval_pairs


class ColumnRetrievalTrainingTests(unittest.TestCase):
    def test_column_cards_include_table_and_column_semantics(self):
        cards = column_cards_from_catalog(_catalog())

        card = next(item for item in cards if item.sql_ref == "orders.net_amount")

        self.assertIn("table: orders", card.text)
        self.assertIn("column: net_amount", card.text)
        self.assertIn("Net order revenue", card.text)
        self.assertIn("Order fact table", card.text)

    def test_training_pairs_use_exact_column_labels_and_hard_negatives(self):
        cases = [
            BenchmarkCase(
                case_id="case_1",
                question="net revenue by customer state",
                category="unit",
                expected_columns=("orders.net_amount", "customers.state"),
            )
        ]
        cards = column_cards_from_catalog(_catalog())

        pairs = build_column_retrieval_pairs(
            cases=cases,
            column_cards=cards,
            negatives_per_positive=2,
            bm25_pool_size=10,
        )

        positives = {row.sql_ref for row in pairs if row.label == 1}
        negatives = {row.sql_ref for row in pairs if row.label == 0}
        self.assertEqual(positives, {"orders.net_amount", "customers.state"})
        self.assertFalse(positives & negatives)
        self.assertGreaterEqual(len(negatives), 2)

    def test_training_pairs_write_csv(self):
        cases = [
            BenchmarkCase(
                case_id="case_1",
                question="net revenue",
                category="unit",
                expected_columns=("orders.net_amount",),
            )
        ]
        pairs = build_column_retrieval_pairs(cases=cases, column_cards=column_cards_from_catalog(_catalog()))
        with tempfile.TemporaryDirectory() as tmp:
            path = write_column_retrieval_pairs(pairs, Path(tmp) / "pairs.csv")

            text = path.read_text(encoding="utf-8")

        self.assertIn("candidate_text", text)
        self.assertIn("orders.net_amount", text)

    def test_column_retrieval_evaluator_reports_recall_at_k(self):
        cases = [
            BenchmarkCase(
                case_id="case_1",
                question="net revenue by customer state",
                category="unit",
                expected_columns=("orders.net_amount", "customers.state"),
            )
        ]

        report = evaluate_column_retrieval(
            cases=cases,
            column_cards=column_cards_from_catalog(_catalog()),
            top_ks=(2, 20),
            candidate_pool_size=10,
        )

        self.assertIn("column_recall@20", report.aggregate_scores)
        self.assertGreater(report.aggregate_scores["column_recall@20"], 0.0)
        self.assertEqual(report.case_count, 1)


def _catalog():
    return {
        "cards": {
            "table:orders": {
                "id": "table:orders",
                "kind": "table",
                "name": "orders",
                "description": "Order fact table with commercial events.",
                "sql_ref": "orders",
            },
            "table:customers": {
                "id": "table:customers",
                "kind": "table",
                "name": "customers",
                "description": "Customer dimension table.",
                "sql_ref": "customers",
            },
            "column:orders.net_amount": {
                "id": "column:orders.net_amount",
                "kind": "column",
                "name": "net_amount",
                "description": "Net order revenue after adjustments.",
                "sql_ref": "orders.net_amount",
                "terms": ["net revenue", "sales"],
                "metadata": {"table_name": "orders", "column_name": "net_amount", "role": "measure"},
            },
            "column:orders.customer_id": {
                "id": "column:orders.customer_id",
                "kind": "column",
                "name": "customer_id",
                "description": "Customer identifier on the order.",
                "sql_ref": "orders.customer_id",
                "terms": ["customer"],
                "metadata": {"table_name": "orders", "column_name": "customer_id"},
            },
            "column:customers.customer_id": {
                "id": "column:customers.customer_id",
                "kind": "column",
                "name": "customer_id",
                "description": "Stable customer identifier.",
                "sql_ref": "customers.customer_id",
                "terms": ["customer"],
                "metadata": {"table_name": "customers", "column_name": "customer_id"},
            },
            "column:customers.state": {
                "id": "column:customers.state",
                "kind": "column",
                "name": "state",
                "description": "Customer residence state.",
                "sql_ref": "customers.state",
                "terms": ["customer state", "geography"],
                "metadata": {"table_name": "customers", "column_name": "state"},
            },
        }
    }


if __name__ == "__main__":
    unittest.main()
