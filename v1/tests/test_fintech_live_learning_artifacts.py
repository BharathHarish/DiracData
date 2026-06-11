import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import settings_from_env
from diracdata.learning.paths import active_learning_artifact_key
from diracdata.retrieval import VectorIndexStore
from diracdata.storage import object_store_from_settings


class FintechLiveLearningArtifactsTest(unittest.TestCase):
    """Gated live checks for the fintech_schema learning artifacts in object storage."""

    def setUp(self) -> None:
        if os.environ.get("DIRACDATA_RUN_LIVE_FINTECH_ARTIFACTS") != "1":
            raise unittest.SkipTest("set DIRACDATA_RUN_LIVE_FINTECH_ARTIFACTS=1 to run")

    def test_active_fintech_artifact_shape_and_vector_queries(self) -> None:
        settings = settings_from_env(".env")
        self.assertEqual(settings.catalog, "fintech_pod")
        self.assertEqual(settings.database, "analytics")
        self.assertEqual(settings.schema, "fintech_schema")

        store = object_store_from_settings(settings)
        active_manifest_key = active_learning_artifact_key(settings, relative_path="manifest.json")
        active_description_key = active_learning_artifact_key(
            settings,
            relative_path="descriptions/metadata_descriptions.json",
        )
        active_join_key = active_learning_artifact_key(
            settings,
            relative_path="joins/joinable_pairs.jsonl",
        )
        active_graph_key = active_learning_artifact_key(
            settings,
            relative_path="context_graph/manifest.json",
        )
        active_embedding_key = active_learning_artifact_key(
            settings,
            relative_path="embeddings/manifest.json",
        )

        manifest = _dict(store.read_json(active_manifest_key))
        descriptions = _dict(store.read_json(active_description_key))
        graph_manifest = _dict(store.read_json(active_graph_key))
        embedding_manifest = _dict(store.read_json(active_embedding_key))
        join_pairs = _jsonl(store.read_text(active_join_key))

        expected_run_id = os.environ.get("DIRACDATA_EXPECTED_LEARNING_RUN_ID")
        if expected_run_id:
            self.assertEqual(manifest["active_run_id"], expected_run_id)

        self.assertEqual(set(descriptions["tables"]), _expected_tables())
        self.assertEqual(set(descriptions["columns"]), _expected_tables())
        self.assertEqual(manifest["active_run_id"], embedding_manifest["run_id"])
        self.assertEqual(embedding_manifest["status"], "ok")
        self.assertEqual(embedding_manifest["vector_count"], 35)
        self.assertEqual(embedding_manifest["vector_dimensions"], 384)
        self.assertEqual(embedding_manifest["active_vector_index"]["status"], "ok")
        self.assertGreaterEqual(len(join_pairs), 5)
        self.assertEqual(graph_manifest["query_pattern_count"], 60)
        self.assertGreaterEqual(graph_manifest["retrieval_document_count"], 120)

        vector_store = VectorIndexStore(settings=settings, object_store=store)
        vector_index = _dict(embedding_manifest["active_vector_index"])
        vectors_key = str(embedding_manifest["active_vectors_artifact_key"])
        search_cases = [
            ("total payment volume successful transaction amount", {("payments", "amount")}),
            (
                "payment rail method UPI credit card NEFT IMPS route",
                {("payment_attributes", "rail_type"), ("payments", "rail_ref")},
            ),
            (
                "merchant geography state location segmentation",
                {("user_attributes", "state")},
            ),
        ]
        for query, expected_candidates in search_cases:
            result = vector_store.search_text(
                query=query,
                vectors_artifact_key=vectors_key,
                vector_index=vector_index,
                top_k=5,
            )
            actual = {(hit.table_name, hit.column_name) for hit in result.hits}
            self.assertEqual(result.backend, "faiss_hnsw")
            self.assertTrue(
                actual & expected_candidates,
                f"{query!r} returned {actual}, expected one of {expected_candidates}",
            )


def _expected_tables() -> set[str]:
    return {"orders", "payment_attributes", "payments", "user_attributes", "users"}


def _dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssertionError("expected JSON object")
    return value


def _jsonl(text: str) -> list[dict[str, object]]:
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise AssertionError("expected JSONL object rows")
    return rows


if __name__ == "__main__":
    unittest.main()
