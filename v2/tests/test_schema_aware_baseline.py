import json
import tempfile
import unittest
from pathlib import Path

from diracdata_v2.evals.schema_benchmark import (
    BenchmarkCase,
    evaluate_compiled_context,
    evaluate_semantic_catalog_baseline,
    load_benchmark_cases,
    validate_benchmark_cases,
    write_report,
)


ROOT = Path(__file__).resolve().parents[2]


class SchemaAwareBaselineTests(unittest.TestCase):
    def test_retail_benchmark_labels_validate_against_metadata(self):
        cases = load_benchmark_cases(ROOT / "v2" / "evals" / "retail_schema_aware_benchmark.csv")
        metadata = json.loads((ROOT / "v2" / "context" / "retail_analytics_metadata_descriptions.json").read_text())

        errors = validate_benchmark_cases(cases, metadata)

        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(cases), 30)

    def test_compiled_context_scoring_reports_missing_refs(self):
        case = BenchmarkCase(
            case_id="case_1",
            question="customers by state",
            category="unit",
            expected_tables=("orders", "customers"),
            expected_columns=("orders.customer_id", "customers.state"),
            expected_join_edges=("customers.customer_id = orders.customer_id",),
            expected_ambiguities=("active",),
        )
        packet = {
            "candidate_cards": [
                {"kind": "column", "sql_ref": "orders.customer_id", "metadata": {"table_name": "orders"}},
            ],
            "sql_patterns": [],
            "join_edges": [],
            "unresolved_terms": [],
            "retrieval": {"required_tables": ["orders"]},
        }

        scores = evaluate_compiled_context(case=case, packet=packet)

        self.assertEqual(scores.table_recall, 0.5)
        self.assertEqual(scores.column_recall, 0.5)
        self.assertEqual(scores.direct_column_recall_at_k["20"], 0.5)
        self.assertEqual(scores.expanded_column_recall_at_k["20"], 0.5)
        self.assertEqual(scores.join_recall, 0.0)
        self.assertEqual(scores.ambiguity_recall, 0.0)
        self.assertEqual(scores.missing_tables, ("customers",))
        self.assertEqual(scores.missing_columns, ("customers.state",))
        self.assertEqual(scores.missing_direct_columns_at_k["20"], ("customers.state",))

    def test_semantic_catalog_baseline_writes_report(self):
        cases = [
            BenchmarkCase(
                case_id="case_1",
                question="net revenue by customer state",
                category="unit",
                expected_tables=("orders", "customers"),
                expected_columns=("orders.customer_id", "orders.net_amount", "customers.state"),
                expected_join_edges=("customers.customer_id = orders.customer_id",),
            )
        ]
        catalog = {
            "cards": {
                "column:orders.customer_id": {
                    "id": "column:orders.customer_id",
                    "kind": "column",
                    "name": "customer_id",
                    "description": "Customer identifier on orders.",
                    "sql_ref": "orders.customer_id",
                    "terms": ["customer", "orders"],
                    "metadata": {"table_name": "orders", "column_name": "customer_id"},
                },
                "column:orders.net_amount": {
                    "id": "column:orders.net_amount",
                    "kind": "column",
                    "name": "net_amount",
                    "description": "Net revenue amount.",
                    "sql_ref": "orders.net_amount",
                    "terms": ["net", "revenue"],
                    "metadata": {"table_name": "orders", "column_name": "net_amount"},
                },
                "column:customers.state": {
                    "id": "column:customers.state",
                    "kind": "column",
                    "name": "state",
                    "description": "Customer state.",
                    "sql_ref": "customers.state",
                    "terms": ["customer", "state"],
                    "metadata": {"table_name": "customers", "column_name": "state"},
                },
            },
            "join_edges": {
                "join:customers.customer_id:orders.customer_id": {
                    "id": "join:customers.customer_id:orders.customer_id",
                    "left_column": "customers.customer_id",
                    "right_column": "orders.customer_id",
                    "sql_condition": "customers.customer_id = orders.customer_id",
                    "tables": ["customers", "orders"],
                    "observed_count": 1,
                }
            },
            "indexes": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

            report = evaluate_semantic_catalog_baseline(cases=cases, semantic_catalog_path=catalog_path)
            report_path = write_report(report, root / "out")

            self.assertTrue(report_path.exists())
            payload = json.loads(report_path.read_text())
            self.assertEqual(payload["case_count"], 1)
            self.assertIn("column_recall", payload["aggregate_scores"])
            self.assertIn("direct_column_recall@20", payload["aggregate_scores"])
            self.assertIn("expanded_column_recall@20", payload["aggregate_scores"])


if __name__ == "__main__":
    unittest.main()
