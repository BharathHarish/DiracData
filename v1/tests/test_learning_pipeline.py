import json
from pathlib import Path
import sys
import tempfile
import unittest

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config.settings import DiracDataSettings
from diracdata.llms import ChatModelMessage
from diracdata.learning import BusinessContext, LearningPipeline
from diracdata.query_engines import query_engine_from_settings
from diracdata.storage import LocalObjectStore


class FakeLearningLLMClient:
    model = "fake-model"

    def complete(self, messages: list[ChatModelMessage]) -> str:
        context = _context_from_prompt(messages[0].content)
        tables = {}
        columns = {}
        for table in context["tables"]:
            table_name = table["table_name"]
            column_names = [column["column_name"] for column in table["columns"]]
            tables[table_name] = {
                "short_description": "Orders describe customer purchase and revenue activity.",
                "long_description": "Orders contains customer purchase rows with region and revenue evidence. This is based on the supplied business context and profile.",
            }
            columns[table_name] = {
                column_name: {
                    "short_description": "Business field described by supplied evidence.",
                    "long_description": "This column description is based on the supplied business context and profile evidence.",
                }
                for column_name in column_names
            }

        return json.dumps(
            {
                "tables": tables,
                "columns": columns,
            }
        )


class LearningPipelineTest(unittest.TestCase):
    def test_pipeline_collects_describes_and_trains_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            parquet_path = tmp_path / "orders.parquet"
            catalog_path = tmp_path / "catalog.json"
            artifact_root = tmp_path / "artifacts"

            con = duckdb.connect(":memory:")
            con.execute(
                """
                CREATE TABLE orders AS
                SELECT * FROM (
                    VALUES
                        (1, 100, 'west', 12.50),
                        (2, 101, 'east', 25.00),
                        (3, 100, 'west', 30.00)
                ) AS t(order_id, customer_id, region, revenue)
                """
            )
            con.execute(f"COPY orders TO '{parquet_path}' (FORMAT parquet)")
            con.close()

            catalog_path.write_text(
                json.dumps(
                    {
                        "catalog": "commerce_pod",
                        "database": "analytics",
                        "schema": "main",
                        "tables": [
                            {
                                "name": "orders",
                                "path": str(parquet_path),
                                "format": "parquet",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            settings = DiracDataSettings(
                catalog="commerce_pod",
                database="analytics",
                schema="main",
                catalog_config=catalog_path,
                learning_sample_limit=2,
                learning_distinct_limit=10,
                llm_provider="anthropic",
                llm_model="claude-sonnet-4-6",
            )
            engine = query_engine_from_settings(settings)
            store = LocalObjectStore(artifact_root)
            pipeline = LearningPipeline(
                settings=settings,
                query_engine=engine,
                object_store=store,
                llm_client=FakeLearningLLMClient(),
            )

            try:
                result = pipeline.run(
                    business_context=BusinessContext(
                        "Commerce order analytics.",
                        table_descriptions={"orders": "Customer purchase facts."},
                        column_descriptions={"orders": {"region": "Sales geography."}},
                        glossary={"revenue": "Money collected from customer purchases."},
                    ),
                    run_id="pipeline_test",
                    tables=["orders"],
                )
            finally:
                engine.close()

            self.assertEqual(result.collection.run_id, "pipeline_test")
            self.assertEqual(result.context.table_names, ["orders"])
            self.assertTrue(store.exists(result.collection.profile_artifact_key))
            self.assertTrue(store.exists(result.description_artifact_key))
            self.assertTrue(store.exists(result.context_graph_artifact_key))
            self.assertTrue(store.exists(result.retrieval_index_artifact_key))
            self.assertTrue(store.exists(result.embedding_manifest_artifact_key))
            self.assertTrue(store.exists(result.context.context_artifact_key))
            context = store.read_json(result.context.context_artifact_key)
            self.assertEqual(context["scope"]["catalog"], "commerce_pod")
            self.assertEqual(context["metadata"]["llm_model"], "claude-sonnet-4-6")
            self.assertEqual(context["joinable_pairs_artifact_key"], result.joinable_pairs_artifact_key)
            self.assertEqual(
                context["context_graph_manifest_artifact_key"],
                result.context_graph_artifact_key,
            )
            self.assertEqual(
                context["retrieval_index_artifact_key"],
                result.retrieval_index_artifact_key,
            )
            self.assertEqual(
                context["embedding_manifest_artifact_key"],
                result.embedding_manifest_artifact_key,
            )
            self.assertTrue(store.exists(result.joinable_pairs_artifact_key))
            active_description_key = (
                "artifacts/learning/commerce_pod/analytics/main/active/"
                "descriptions/metadata_descriptions.json"
            )
            active_joinable_pairs_key = (
                "artifacts/learning/commerce_pod/analytics/main/active/"
                "joins/joinable_pairs.jsonl"
            )
            active_context_key = (
                "artifacts/learning/commerce_pod/analytics/main/active/"
                "contexts/learned_context.json"
            )
            active_context_graph_key = (
                "artifacts/learning/commerce_pod/analytics/main/active/"
                "context_graph/manifest.json"
            )
            active_bm25_key = (
                "artifacts/learning/commerce_pod/analytics/main/active/"
                "retrieval/bm25_plus_index.json"
            )
            active_embedding_key = (
                "artifacts/learning/commerce_pod/analytics/main/active/"
                "embeddings/manifest.json"
            )
            active_manifest_key = (
                "artifacts/learning/commerce_pod/analytics/main/active/manifest.json"
            )
            self.assertTrue(store.exists(active_description_key))
            self.assertTrue(store.exists(active_joinable_pairs_key))
            self.assertTrue(store.exists(active_context_key))
            self.assertTrue(store.exists(active_context_graph_key))
            self.assertTrue(store.exists(active_bm25_key))
            self.assertTrue(store.exists(active_embedding_key))
            self.assertTrue(store.exists(active_manifest_key))
            active_context = store.read_json(active_context_key)
            active_manifest = store.read_json(active_manifest_key)
            self.assertEqual(active_context["run_id"], "pipeline_test")
            self.assertEqual(
                active_context["joinable_pairs_artifact_key"],
                result.joinable_pairs_artifact_key,
            )
            self.assertEqual(active_manifest["active_run_id"], "pipeline_test")
            self.assertEqual(
                active_manifest["immutable_artifacts"]["description_artifact_key"],
                result.description_artifact_key,
            )
            self.assertEqual(
                active_manifest["immutable_artifacts"]["context_graph_manifest_artifact_key"],
                result.context_graph_artifact_key,
            )
            graph_manifest = store.read_json(result.context_graph_artifact_key)
            embedding_manifest = store.read_json(result.embedding_manifest_artifact_key)
            self.assertGreater(graph_manifest["node_count"], 0)
            self.assertEqual(embedding_manifest["status"], "disabled")
            llm_context = store.read_json(result.collection.llm_context_artifact_key)
            self.assertEqual(llm_context["business_context"]["table_descriptions"]["orders"], "Customer purchase facts.")
            self.assertEqual(llm_context["business_context"]["glossary"]["revenue"], "Money collected from customer purchases.")


def _context_from_prompt(prompt: str) -> dict[str, object]:
    start = prompt.rfind("```json")
    end = prompt.rfind("```")
    if start < 0 or end <= start:
        raise AssertionError("prompt does not contain JSON context block")
    return json.loads(prompt[start + len("```json"):end].strip())
