import json
from pathlib import Path
import random
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config.settings import DiracDataSettings
from diracdata.grounding import publish_business_grounding
from diracdata.learning import BusinessContext, LearningPipeline, load_query_history_csv
from diracdata.llms import ChatModelMessage
from diracdata.query_engines import query_engine_from_settings
from diracdata.storage import LocalObjectStore
from generate_fintech_query_history import generate_records, write_records  # noqa: E402
from generate_fintech_schema_parquet import (  # noqa: E402
    generate_frames,
    write_business_context,
    write_business_grounding,
    write_catalog,
    write_parquet,
)


class FakeFintechLearningLLMClient:
    model = "fake-learning-model"

    def complete(self, messages: list[ChatModelMessage]) -> str:
        prompt = messages[0].content
        if "successful_queries" in prompt and "join_candidates" in prompt:
            return json.dumps(
                {
                    "join_candidates": [
                        {
                            "left_table": "orders",
                            "left_column": "order_ref",
                            "right_table": "payments",
                            "right_column": "order_ref",
                        },
                        {
                            "left_table": "orders",
                            "left_column": "user_ref",
                            "right_table": "users",
                            "right_column": "user_ref",
                        },
                        {
                            "left_table": "payments",
                            "left_column": "user_ref",
                            "right_table": "users",
                            "right_column": "user_ref",
                        },
                        {
                            "left_table": "payments",
                            "left_column": "user_ref",
                            "right_table": "user_attributes",
                            "right_column": "user_ref",
                        },
                        {
                            "left_table": "users",
                            "left_column": "user_ref",
                            "right_table": "user_attributes",
                            "right_column": "user_ref",
                        },
                        {
                            "left_table": "payments",
                            "left_column": "rail_ref",
                            "right_table": "payment_attributes",
                            "right_column": "rail_ref",
                        },
                    ]
                }
            )

        context = _context_from_prompt(prompt)
        tables = {}
        columns = {}
        for table in context["tables"]:
            table_name = table["table_name"]
            tables[table_name] = {
                "short_description": f"{table_name} supports Razorpay fintech analytics.",
                "long_description": (
                    f"{table_name} is part of the Razorpay fintech context and is described "
                    "using supplied schema, profiles, and business grounding evidence."
                ),
            }
            columns[table_name] = {}
            for column in table["columns"]:
                column_name = column["column_name"]
                columns[table_name][column_name] = {
                    "short_description": f"{column_name} is a business field in {table_name}.",
                    "long_description": (
                        f"{column_name} is interpreted from the supplied profile evidence, "
                        "business context, and fintech grounding."
                    ),
                }
        return json.dumps({"tables": tables, "columns": columns})


class FintechLearningPipelineArtifactsTest(unittest.TestCase):
    def test_fintech_pipeline_builds_expected_artifact_shape_without_live_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            parquet_dir = tmp_path / "parquet"
            catalog_path = tmp_path / "catalog.json"
            context_path = tmp_path / "business_context.json"
            grounding_path = tmp_path / "business_grounding.yaml"
            history_path = tmp_path / "query_history.csv"
            artifact_root = tmp_path / "artifacts"

            frames = generate_frames(
                rng=random.Random(17),
                user_count=50,
                order_count=200,
                payment_count=250,
                payment_attribute_count=40,
            )
            generated = write_parquet(frames=frames, output_dir=parquet_dir)
            write_catalog(
                output_path=catalog_path,
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
                table_paths={table.name: str(table.path) for table in generated},
            )
            write_business_context(context_path)
            write_business_grounding(
                output_path=grounding_path,
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
            )
            write_records(
                generate_records(count=80, unique_success_sql=25, seed=23),
                history_path,
            )
            business_grounding = yaml.safe_load(grounding_path.read_text(encoding="utf-8"))

            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
                catalog_config=catalog_path,
                learning_sample_limit=25,
                learning_distinct_limit=100,
                learning_description_column_batch_size=15,
                learning_embedding_provider="none",
                join_history_llm_batch_size=50,
            )
            engine = query_engine_from_settings(settings)
            store = LocalObjectStore(artifact_root)
            publish_business_grounding(
                settings=settings,
                object_store=store,
                source_path=grounding_path,
                query_engine=engine,
            )
            pipeline = LearningPipeline(
                settings=settings,
                query_engine=engine,
                object_store=store,
                llm_client=FakeFintechLearningLLMClient(),
            )
            try:
                result = pipeline.run(
                    business_context=BusinessContext.from_json_file(context_path),
                    run_id="fintech_pipeline_test",
                    query_history_records=load_query_history_csv(history_path),
                    business_grounding=business_grounding,
                )
            finally:
                engine.close()

            profile = store.read_json(result.collection.profile_artifact_key)
            descriptions = store.read_json(result.description_artifact_key)
            graph_manifest = store.read_json(result.context_graph_artifact_key)
            query_library_manifest = store.read_json(result.query_libraries_artifact_key or "")
            nuance_manifest = store.read_json(result.nuance_artifact_key or "")
            embedding_manifest = store.read_json(result.embedding_manifest_artifact_key)
            join_pairs = [
                json.loads(line)
                for line in store.read_text(result.joinable_pairs_artifact_key).splitlines()
                if line.strip()
            ]
            sample_keys = store.list_keys(
                "artifacts/learning/fintech_pod/analytics/fintech_schema/fintech_pipeline_test/samples"
            )

        expected_tables = {"orders", "payment_attributes", "payments", "user_attributes", "users"}
        self.assertEqual({table["table_name"] for table in profile["tables"]}, expected_tables)
        self.assertEqual(set(descriptions["tables"]), expected_tables)
        self.assertEqual(set(descriptions["columns"]), expected_tables)
        self.assertEqual(len(sample_keys), 5)
        self.assertGreaterEqual(len(join_pairs), 4)
        self.assertGreater(graph_manifest["node_count"], 0)
        self.assertGreater(graph_manifest["query_pattern_count"], 0)
        self.assertGreater(graph_manifest["retrieval_document_count"], len(expected_tables))
        self.assertGreater(query_library_manifest["query_pattern_count"], 0)
        self.assertGreater(query_library_manifest["sql_template_count"], 0)
        self.assertGreater(nuance_manifest["confounder_count"], 0)
        self.assertGreater(nuance_manifest["invariant_count"], 0)
        self.assertGreater(nuance_manifest["analyst_question_count"], 0)
        self.assertEqual(embedding_manifest["status"], "disabled")
        self.assertEqual(embedding_manifest["vector_index"]["status"], "empty")


def _context_from_prompt(prompt: str) -> dict[str, object]:
    start = prompt.rfind("```json")
    end = prompt.rfind("```")
    if start < 0 or end <= start:
        raise AssertionError("prompt does not contain JSON context block")
    return json.loads(prompt[start + len("```json") : end].strip())


if __name__ == "__main__":
    unittest.main()
