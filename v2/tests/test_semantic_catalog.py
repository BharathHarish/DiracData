import json
import tempfile
import unittest
from pathlib import Path

from diracdata_v2.semantic_catalog import (
    SemanticCatalogBuilder,
    SemanticCatalogCompiler,
    build_semantic_catalog_document,
)


def _metadata():
    return {
        "tables": {
            "orders": {
                "short_description": "Orders placed by customers.",
                "long_description": "Orders placed by customers.",
            },
            "customers": {
                "short_description": "Customer accounts and geography.",
                "long_description": "Customer accounts and geography.",
            },
        },
        "columns": {
            "orders": {
                "order_id": {"short_description": "Unique order identifier."},
                "customer_id": {"short_description": "Customer who placed the order."},
                "order_status": {"short_description": "Order status such as 'completed' or 'cancelled'."},
                "net_amount": {"short_description": "Net order amount after discounts."},
            },
            "customers": {
                "customer_id": {"short_description": "Unique customer identifier."},
                "state": {"short_description": "US state abbreviation for the customer address."},
            },
        },
    }


def _schema_ast():
    return {
        "artifact_type": "schema_ast",
        "run_id": "ast_run",
        "domains": [
            {
                "id": "domain:commerce",
                "kind": "domain",
                "name": "Commerce",
                "description": "Customer ordering activity.",
                "path": ["commerce"],
                "entities": [
                    {
                        "id": "entity:orders",
                        "kind": "entity",
                        "name": "Orders",
                        "description": "Orders and customers.",
                        "path": ["commerce", "orders"],
                        "tables": [
                            {
                                "id": "table:orders",
                                "kind": "table",
                                "name": "orders",
                                "description": "Orders.",
                                "path": ["commerce", "orders", "orders"],
                                "sql_ref": "orders",
                                "grain": "one row per order",
                                "columns": [
                                    {
                                        "id": "column:orders.order_id",
                                        "kind": "column",
                                        "name": "order_id",
                                        "description": "Unique order identifier.",
                                        "path": ["commerce", "orders", "orders", "order_id"],
                                        "sql_ref": "orders.order_id",
                                    },
                                    {
                                        "id": "column:orders.customer_id",
                                        "kind": "column",
                                        "name": "customer_id",
                                        "description": "Customer who placed the order.",
                                        "path": ["commerce", "orders", "orders", "customer_id"],
                                        "sql_ref": "orders.customer_id",
                                    },
                                    {
                                        "id": "column:orders.net_amount",
                                        "kind": "column",
                                        "name": "net_amount",
                                        "description": "Net order amount after discounts.",
                                        "path": ["commerce", "orders", "orders", "net_amount"],
                                        "sql_ref": "orders.net_amount",
                                    },
                                    {
                                        "id": "column:orders.order_status",
                                        "kind": "column",
                                        "name": "order_status",
                                        "description": "Order status such as completed or cancelled.",
                                        "path": ["commerce", "orders", "orders", "order_status"],
                                        "sql_ref": "orders.order_status",
                                    },
                                ],
                            },
                            {
                                "id": "table:customers",
                                "kind": "table",
                                "name": "customers",
                                "description": "Customer accounts.",
                                "path": ["commerce", "orders", "customers"],
                                "sql_ref": "customers",
                                "grain": "one row per customer",
                                "columns": [
                                    {
                                        "id": "column:customers.customer_id",
                                        "kind": "column",
                                        "name": "customer_id",
                                        "description": "Unique customer identifier.",
                                        "path": ["commerce", "orders", "customers", "customer_id"],
                                        "sql_ref": "customers.customer_id",
                                    },
                                    {
                                        "id": "column:customers.state",
                                        "kind": "column",
                                        "name": "state",
                                        "description": "US state abbreviation for the customer address.",
                                        "path": ["commerce", "orders", "customers", "state"],
                                        "sql_ref": "customers.state",
                                    },
                                ],
                            },
                        ],
                    }
                ],
            }
        ],
    }


def _sql_library():
    sql = (
        "SELECT c.state, sum(o.net_amount) AS net_revenue "
        "FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
        "WHERE o.order_status = {{string}} GROUP BY c.state"
    )
    return {
        "artifact_type": "sql_library",
        "run_id": "lib_run",
        "entries": {
            "history:orders_customers": {
                "template": "orders + customers net revenue by state",
                "sql": sql,
                "source": "query_history",
                "review_status": "observed",
                "tables": ["orders", "customers"],
                "columns": [
                    "orders.customer_id",
                    "orders.net_amount",
                    "orders.order_status",
                    "customers.customer_id",
                    "customers.state",
                ],
            }
        },
        "patterns": {
            "pattern:history:orders_customers": {
                "id": "pattern:history:orders_customers",
                "entry_id": "history:orders_customers",
                "canonical_question": "What is net revenue by customer state?",
                "summary": "Sums net order amount by customer state using completed orders.",
                "source": "query_history",
                "review_status": "observed",
                "tables": ["orders", "customers"],
                "columns": [
                    "orders.customer_id",
                    "orders.net_amount",
                    "orders.order_status",
                    "customers.customer_id",
                    "customers.state",
                ],
                "intent_signature": {
                    "measure": "net revenue",
                    "dimensions": ["customer state"],
                    "grain": "one row per customer state",
                    "filters": ["order status equals completed"],
                },
                "assumptions": ["Use net amount, not gross amount."],
                "sql_template": sql,
            }
        },
    }


class SemanticCatalogTests(unittest.TestCase):
    def test_builder_creates_cards_indexes_and_observed_join_edges(self):
        document = build_semantic_catalog_document(
            metadata_descriptions=_metadata(),
            schema_ast=_schema_ast(),
            sql_library=_sql_library(),
            catalog="commerce_pod",
            database="analytics",
            schema="commerce",
            run_id="catalog_run",
        )

        self.assertEqual(document["validation"]["status"], "ok")
        self.assertIn("column:orders.net_amount", document["cards"])
        self.assertIn("dimension:customers.state", document["cards"])
        self.assertIn("pattern:history:orders_customers", document["cards"])
        self.assertTrue(document["join_edges"])
        edge = next(iter(document["join_edges"].values()))
        self.assertEqual(edge["sql_condition"], "customers.customer_id = orders.customer_id")
        self.assertIn("orders", document["indexes"]["join_edges_by_table"])

    def test_compiler_returns_compact_packet_and_blocks_unresolved_business_terms(self):
        document = build_semantic_catalog_document(
            metadata_descriptions=_metadata(),
            schema_ast=_schema_ast(),
            sql_library=_sql_library(),
            catalog="commerce_pod",
            database="analytics",
            schema="commerce",
            run_id="catalog_run",
        )

        packet = SemanticCatalogCompiler(document).compile("net revenue by state for active customers")
        payload = packet.to_dict()

        self.assertTrue(payload["needs_clarification"])
        self.assertEqual(payload["unresolved_terms"][0]["term"], "active")
        self.assertTrue(payload["sql_patterns"])
        self.assertTrue(payload["join_edges"])
        self.assertIn("Use net amount, not gross amount.", payload["assertions"])

    def test_compiler_expands_top_patterns_into_required_columns(self):
        document = build_semantic_catalog_document(
            metadata_descriptions=_metadata(),
            schema_ast=_schema_ast(),
            sql_library=_sql_library(),
            catalog="commerce_pod",
            database="analytics",
            schema="commerce",
            run_id="catalog_run",
        )

        packet = SemanticCatalogCompiler(document).compile("net revenue by state")
        payload = packet.to_dict()
        candidate_ids = {item["id"] for item in payload["candidate_cards"]}

        self.assertFalse(payload["needs_clarification"])
        self.assertIn("pattern:history:orders_customers", {item["id"] for item in payload["sql_patterns"]})
        self.assertIn("column:orders.net_amount", candidate_ids)
        self.assertIn("column:customers.state", candidate_ids)
        self.assertIn("column:orders.order_status", candidate_ids)

    def test_builder_writes_semantic_catalog_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = SemanticCatalogBuilder().build(
                metadata_descriptions=_metadata(),
                schema_ast=_schema_ast(),
                sql_library=_sql_library(),
                catalog="commerce_pod",
                database="analytics",
                schema="commerce",
                run_id="catalog_run",
                output_dir=Path(tmp),
            )
            self.assertTrue(result.local_path.exists())
            stored = json.loads(result.local_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["artifact_type"], "semantic_catalog")


if __name__ == "__main__":
    unittest.main()
