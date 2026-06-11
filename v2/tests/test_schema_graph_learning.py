import json
import tempfile
import unittest
from pathlib import Path

from diracdata_v2.learning.schema_graph import SchemaGraphBuilder, load_prompt


class FakeGenerator:
    def __init__(self):
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        user_prompt = messages[-1]["content"]
        if "Input table descriptions" in user_prompt:
            return json.dumps(
                {
                    "domains": [
                        {
                            "id": "domain:orders",
                            "name": "Orders",
                            "description": "Order activity.",
                        }
                    ],
                    "entities": [
                        {
                            "id": "entity:order",
                            "domain_id": "domain:orders",
                            "name": "Order",
                            "description": "Customer order event.",
                        }
                    ],
                    "tables": {
                        "orders": {
                            "entity_id": "entity:order",
                            "description": "Customer orders.",
                            "grain": "one row per order",
                        }
                    },
                }
            )
        return json.dumps(
            {
                "domains": [
                    {
                        "id": "domain:payments",
                        "name": "Payments",
                        "description": "Payment attempts and outcomes.",
                    }
                ],
                "entities": [
                    {
                        "id": "entity:payment_attempt",
                        "domain_id": "domain:payments",
                        "name": "Payment Attempt",
                        "description": "One attempted payment transaction.",
                    }
                ],
                "tables": {
                    "payments": {
                        "entity_id": "entity:payment_attempt",
                        "description": "Payment attempts with amount and status.",
                        "grain": "one row per payment attempt",
                    }
                },
                "columns": {
                    "payments": {
                        "payment_status": {
                            "description": "Outcome of a payment attempt.",
                            "role": "status",
                            "aliases": ["payment outcome"],
                        },
                        "amount": {
                            "description": "Payment amount.",
                            "role": "measure",
                        },
                    }
                },
            }
        )


class SchemaGraphLearningTests(unittest.TestCase):
    def test_builder_creates_complete_graph_from_llm_hierarchy(self):
        metadata = {
            "tables": {
                "payments": {
                    "short_description": "Payment transaction attempts.",
                    "long_description": "Payment transaction attempts.",
                }
            },
            "columns": {
                "payments": {
                    "payment_status": {
                        "short_description": "Payment outcome.",
                        "long_description": "Payment outcome.",
                    },
                    "amount": {
                        "short_description": "Payment amount.",
                        "long_description": "Payment amount.",
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            builder = SchemaGraphBuilder(generator=FakeGenerator(), prompt=load_prompt())
            result = builder.build(
                metadata_descriptions=metadata,
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
                run_id="test_run",
                output_dir=Path(tmp),
            )

        node_ids = {node["id"] for node in result.document["nodes"]}

        self.assertIn("domain:payments", node_ids)
        self.assertIn("entity:payment_attempt", node_ids)
        self.assertIn("table:payments", node_ids)
        self.assertIn("column:payments.payment_status", node_ids)
        self.assertIn("column:payments.amount", node_ids)
        self.assertEqual(
            result.document["indexes"]["columns_by_table"]["table:payments"],
            ["column:payments.amount", "column:payments.payment_status"],
        )

    def test_large_schema_uses_hierarchy_prompt_and_preserves_columns(self):
        metadata = {
            "tables": {
                "orders": {
                    "short_description": "Customer orders.",
                    "long_description": "Customer orders.",
                }
            },
            "columns": {
                "orders": {
                    "order_status": {
                        "short_description": "Order status.",
                        "long_description": "Order status.",
                    },
                    "order_amount": {
                        "short_description": "Order amount.",
                        "long_description": "Order amount.",
                    },
                }
            },
        }
        generator = FakeGenerator()
        with tempfile.TemporaryDirectory() as tmp:
            builder = SchemaGraphBuilder(
                generator=generator,
                prompt=load_prompt(),
                full_prompt_column_limit=1,
            )
            result = builder.build(
                metadata_descriptions=metadata,
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
                run_id="retail_run",
                output_dir=Path(tmp),
            )

        user_prompt = generator.calls[0][1]["content"]
        self.assertIn("Input table descriptions", user_prompt)
        self.assertNotIn("order_status", user_prompt)
        node_ids = {node["id"] for node in result.document["nodes"]}
        self.assertIn("column:orders.order_status", node_ids)
        self.assertIn("column:orders.order_amount", node_ids)


if __name__ == "__main__":
    unittest.main()
