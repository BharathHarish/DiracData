import csv
import json
import tempfile
import unittest
from pathlib import Path

from diracdata_v2.learning import LearningPipeline, LearningPipelineConfig


class FakeGenerator:
    def complete(self, messages):
        return json.dumps(
            {
                "domains": [
                    {
                        "id": "domain:commerce",
                        "name": "Commerce",
                        "description": "Commerce activity.",
                        "aliases": ["shopping"],
                    }
                ],
                "entities": [
                    {
                        "id": "entity:order",
                        "domain_id": "domain:commerce",
                        "name": "Order",
                        "description": "Customer order events.",
                        "aliases": ["purchase"],
                    }
                ],
                "tables": {
                    "orders": {
                        "entity_id": "entity:order",
                        "description": "Order table.",
                        "grain": "one row per order",
                    }
                },
                "columns": {
                    "orders": {
                        "order_status": {
                            "description": "Current order state.",
                            "aliases": ["status"],
                            "role": "status",
                        },
                        "order_amount": {
                            "description": "Order value.",
                            "aliases": ["revenue"],
                            "role": "measure",
                        },
                    }
                },
            }
        )


class LearningPipelineTests(unittest.TestCase):
    def test_pipeline_builds_graph_library_ast_and_manifest_for_generic_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata_path = root / "metadata_descriptions.json"
            metadata_path.write_text(
                json.dumps(
                    {
                        "tables": {
                            "orders": {
                                "short_description": "Orders placed by customers.",
                                "long_description": "Orders placed by customers.",
                            }
                        },
                        "columns": {
                            "orders": {
                                "order_status": {
                                    "short_description": "State of the order.",
                                    "long_description": "State of the order.",
                                },
                                "order_amount": {
                                    "short_description": "Value of the order.",
                                    "long_description": "Value of the order.",
                                },
                            }
                        },
                    }
                )
            )
            history_path = root / "history.csv"
            with history_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["execution_status", "statement_text"])
                writer.writeheader()
                writer.writerow(
                    {
                        "execution_status": "FINISHED",
                        "statement_text": "SELECT o.order_status FROM orders o GROUP BY o.order_status",
                    }
                )
            data_root = root / "data"
            parquet_root = data_root / "commerce_schema" / "parquet"
            parquet_root.mkdir(parents=True)

            import duckdb

            duckdb.connect(":memory:").execute(
                f"""
                COPY (
                  SELECT 'paid' AS order_status, 10.0::DOUBLE AS order_amount
                )
                TO '{(parquet_root / "orders.parquet").as_posix()}'
                (FORMAT PARQUET)
                """
            )

            result = LearningPipeline(generator=FakeGenerator()).run(
                config=LearningPipelineConfig(
                    catalog="commerce_pod",
                    database="analytics",
                    schema="commerce_schema",
                    metadata_descriptions_path=metadata_path,
                    query_history_path=history_path,
                    data_root=data_root,
                    artifact_root=root / "artifacts",
                    run_id="commerce_run",
                )
            )

        self.assertTrue(result.manifest_path.name, "manifest.json")
        self.assertEqual(result.manifest["summary"]["tables"], 1)
        self.assertEqual(result.manifest["summary"]["columns"], 2)
        self.assertGreater(result.manifest["summary"]["semantic_catalog_cards"], 0)
        self.assertIn("self_play:orders.order_amount", result.sql_library.document["entries"])
        self.assertEqual(result.schema_ast.document["domains"][0]["id"], "domain:commerce")
        self.assertEqual(result.semantic_catalog.document["artifact_type"], "semantic_catalog")


if __name__ == "__main__":
    unittest.main()
