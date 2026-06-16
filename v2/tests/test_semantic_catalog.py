import json
import tempfile
import unittest
from pathlib import Path

from diracdata_v2.semantic_catalog import (
    QueryIntentFrame,
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


class FakeIntentExtractor:
    def extract(self, question, *, catalog_summary=None):
        self.question = question
        self.catalog_summary = catalog_summary
        return QueryIntentFrame(
            search_queries=("net revenue", "customer state"),
            measures=("net revenue",),
            dimensions=("customer state",),
            source="fake_llm",
        )


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
        self.assertIn("pattern:history:orders_customers", document["cards"])
        self.assertTrue(document["join_edges"])
        edge = next(iter(document["join_edges"].values()))
        self.assertEqual(edge["sql_condition"], "customers.customer_id = orders.customer_id")
        self.assertIn("orders", document["indexes"]["join_edges_by_table"])

    def test_builder_preserves_trusted_nl_sql_pair_provenance(self):
        library = _sql_library()
        pattern = library["patterns"]["pattern:history:orders_customers"]
        pattern["id"] = "pattern:nl_sql:gold_001"
        pattern["entry_id"] = "nl_sql:gold_001"
        pattern["source"] = "nl_sql_pair"
        pattern["review_status"] = "approved"
        library["patterns"] = {"pattern:nl_sql:gold_001": pattern}
        library["entries"] = {
            "nl_sql:gold_001": {
                **library["entries"]["history:orders_customers"],
                "source": "nl_sql_pair",
                "review_status": "approved",
                "join_edges": [
                    {
                        "left_column": "customers.customer_id",
                        "right_column": "orders.customer_id",
                    }
                ],
            }
        }

        document = build_semantic_catalog_document(
            metadata_descriptions=_metadata(),
            schema_ast=_schema_ast(),
            sql_library=library,
            catalog="commerce_pod",
            database="analytics",
            schema="commerce",
            run_id="catalog_run",
        )

        card = document["cards"]["pattern:nl_sql:gold_001"]
        self.assertEqual(card["source"], "nl_sql_pair")
        self.assertEqual(card["review_status"], "approved")
        self.assertEqual(document["validation"]["status"], "ok")

    def test_builder_can_build_catalog_without_schema_ast(self):
        document = build_semantic_catalog_document(
            metadata_descriptions=_metadata(),
            schema_ast=None,
            sql_library=_sql_library(),
            catalog="commerce_pod",
            database="analytics",
            schema="commerce",
            run_id="catalog_run",
        )

        self.assertEqual(document["validation"]["status"], "ok")
        self.assertIn("table:orders", document["cards"])
        self.assertIn("column:customers.state", document["cards"])
        self.assertIn("pattern:history:orders_customers", document["cards"])
        self.assertTrue(document["join_edges"])

    def test_compiler_blocks_only_terms_marked_definition_required_by_intent_frame(self):
        document = build_semantic_catalog_document(
            metadata_descriptions=_metadata(),
            schema_ast=_schema_ast(),
            sql_library=_sql_library(),
            catalog="commerce_pod",
            database="analytics",
            schema="commerce",
            run_id="catalog_run",
        )

        packet = SemanticCatalogCompiler(document).compile(
            "net revenue by state for active customers",
            intent_frame=QueryIntentFrame(
                search_queries=("net revenue by state", "active customers"),
                definition_required_terms=(
                    {"term": "active customers", "reason": "Requires approved customer activity definition."},
                ),
                source="test",
            ),
        )
        payload = packet.to_dict()

        self.assertTrue(payload["needs_clarification"])
        self.assertEqual(payload["unresolved_terms"][0]["term"], "active customers")
        self.assertEqual(
            payload["retrieval"]["intent_frame"]["definition_required_terms"][0]["term"],
            "active customers",
        )
        self.assertTrue(payload["sql_patterns"])
        self.assertTrue(payload["join_edges"])
        self.assertIn("Use net amount, not gross amount.", payload["sql_patterns"][0]["assumptions"])
        self.assertNotIn("Use net amount, not gross amount.", payload["assertions"])
        self.assertIn(
            "Treat SQL pattern assumptions as pattern-local evidence; apply them only when that pattern matches the final intent.",
            payload["assertions"],
        )

    def test_compiler_does_not_block_definition_term_when_catalog_supports_phrase(self):
        document = build_semantic_catalog_document(
            metadata_descriptions=_metadata(),
            schema_ast=_schema_ast(),
            sql_library=_sql_library(),
            catalog="commerce_pod",
            database="analytics",
            schema="commerce",
            run_id="catalog_run",
        )

        packet = SemanticCatalogCompiler(document).compile(
            "net revenue by state for completed orders",
            intent_frame=QueryIntentFrame(
                search_queries=("net revenue by state", "completed orders"),
                definition_required_terms=(
                    {"term": "order status", "reason": "LLM caution that should be resolved by catalog support."},
                ),
                source="test",
            ),
        )
        payload = packet.to_dict()

        self.assertFalse(payload["needs_clarification"])
        self.assertEqual(payload["unresolved_terms"], [])
        self.assertEqual(payload["retrieval"]["intent_frame"]["definition_required_terms"], [])

    def test_compiler_uses_intent_frame_for_negative_scope_clarification(self):
        document = build_semantic_catalog_document(
            metadata_descriptions=_metadata(),
            schema_ast=_schema_ast(),
            sql_library=_sql_library(),
            catalog="commerce_pod",
            database="analytics",
            schema="commerce",
            run_id="catalog_run",
        )

        packet = SemanticCatalogCompiler(document).compile(
            "count customers who bought jewelry online in 2002 but did not buy in 2001",
            intent_frame=QueryIntentFrame(
                search_queries=("customers bought jewelry online 2002 did not buy 2001",),
                definition_required_terms=(
                    {
                        "term": "did not buy",
                        "reason": (
                            "The exclusion clause omits SQL-affecting scope from the positive clause; "
                            "confirm whether the negative action uses the same scope or a broader scope."
                        ),
                    },
                ),
                source="test",
            ),
        )
        payload = packet.to_dict()

        self.assertTrue(payload["needs_clarification"])
        self.assertIn("did not buy", payload["unresolved_terms"][0]["term"])
        self.assertIn("same scope", payload["unresolved_terms"][0]["reason"])

    def test_deterministic_compiler_does_not_create_semantic_clarifications(self):
        document = build_semantic_catalog_document(
            metadata_descriptions=_metadata(),
            schema_ast=_schema_ast(),
            sql_library=_sql_library(),
            catalog="commerce_pod",
            database="analytics",
            schema="commerce",
            run_id="catalog_run",
        )

        packet = SemanticCatalogCompiler(document).compile(
            "count customers who bought jewelry online in 2002 but did not buy in 2001"
        )
        payload = packet.to_dict()

        self.assertFalse(payload["needs_clarification"])
        self.assertEqual(payload["unresolved_terms"], [])

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
        self.assertIn("intent_frame", payload["retrieval"])
        self.assertEqual(payload["retrieval"]["intent_frame"]["source"], "deterministic")

    def test_compiler_uses_injected_intent_extractor_for_search_terms(self):
        document = build_semantic_catalog_document(
            metadata_descriptions=_metadata(),
            schema_ast=_schema_ast(),
            sql_library=_sql_library(),
            catalog="commerce_pod",
            database="analytics",
            schema="commerce",
            run_id="catalog_run",
        )
        extractor = FakeIntentExtractor()

        packet = SemanticCatalogCompiler(document, intent_extractor=extractor).compile(
            "show the business result",
        )
        payload = packet.to_dict()

        self.assertEqual(extractor.question, "show the business result")
        self.assertEqual(extractor.catalog_summary["scope"]["schema"], "commerce")
        self.assertEqual(payload["retrieval"]["intent_frame"]["source"], "fake_llm")
        self.assertIn("pattern:history:orders_customers", {item["id"] for item in payload["sql_patterns"]})

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
