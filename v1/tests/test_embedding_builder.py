import json
import importlib.util
import os
import tempfile
import unittest

from diracdata.config.settings import DiracDataSettings
from diracdata.learning import (
    ColumnProfile,
    EmbeddingIndexBuilder,
    LearningCollection,
    LearningScope,
    TableProfile,
)
from diracdata.learning.paths import active_learning_artifact_key, learning_artifact_key
from diracdata.retrieval import VectorIndexStore
from diracdata.storage import LocalObjectStore


class EmbeddingIndexBuilderTest(unittest.TestCase):
    def test_disabled_provider_writes_manifest_and_empty_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
                learning_embedding_provider="none",
            )
            store = LocalObjectStore(tmpdir)
            collection = _collection(settings)
            retrieval_key = _write_retrieval_documents(settings, store, collection.run_id)
            builder = EmbeddingIndexBuilder(settings=settings, object_store=store)

            result = builder.build(
                collection=collection,
                retrieval_documents_artifact_key=retrieval_key,
            )
            manifest = store.read_json(result.manifest_artifact_key)
            vectors = store.read_text(result.vectors_artifact_key)

        self.assertEqual(result.status, "disabled")
        self.assertEqual(result.document_count, 1)
        self.assertEqual(result.vector_count, 0)
        self.assertEqual(manifest["status"], "disabled")
        self.assertEqual(manifest["vector_index"]["status"], "empty")
        self.assertEqual(vectors, "")

    def test_unsupported_provider_does_not_fail_graph_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
                learning_embedding_provider="unknown_provider",
            )
            store = LocalObjectStore(tmpdir)
            collection = _collection(settings)
            retrieval_key = _write_retrieval_documents(settings, store, collection.run_id)
            active_manifest_key = active_learning_artifact_key(settings, relative_path="manifest.json")
            store.write_json(active_manifest_key, {"active_run_id": collection.run_id})
            builder = EmbeddingIndexBuilder(settings=settings, object_store=store)

            result = builder.build(
                collection=collection,
                retrieval_documents_artifact_key=retrieval_key,
            )
            manifest = store.read_json(result.manifest_artifact_key)
            active_manifest = store.read_json(active_manifest_key)

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(manifest["vector_count"], 0)
        self.assertIn("Unsupported embedding provider", manifest["notes"][0])
        self.assertEqual(manifest["vector_index"]["status"], "empty")
        self.assertEqual(
            active_manifest["immutable_artifacts"]["embedding_manifest_artifact_key"],
            result.manifest_artifact_key,
        )
        self.assertEqual(active_manifest["embeddings"]["status"], "unavailable")

    def test_vector_search_falls_back_to_jsonl_without_faiss_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
                learning_vector_index_backend="none",
            )
            store = LocalObjectStore(tmpdir)
            vectors_key = learning_artifact_key(
                settings,
                run_id="embedding_test",
                relative_path="embeddings/column_embeddings.jsonl",
            )
            store.write_text(
                vectors_key,
                "\n".join(
                    json.dumps(row)
                    for row in [
                        {
                            "document_id": "retrieval:column:payments.amount",
                            "table_name": "payments",
                            "column_name": "amount",
                            "embedding": [1.0, 0.0, 0.0],
                        },
                        {
                            "document_id": "retrieval:column:users.signup_time",
                            "table_name": "users",
                            "column_name": "signup_time",
                            "embedding": [0.0, 1.0, 0.0],
                        },
                    ]
                )
                + "\n",
            )

            result = VectorIndexStore(settings=settings, object_store=store).search_by_vector(
                query_embedding=[0.9, 0.1, 0.0],
                vectors_artifact_key=vectors_key,
                top_k=1,
            )

        self.assertEqual(result.backend, "bruteforce_jsonl")
        self.assertEqual(result.hits[0].table_name, "payments")
        self.assertEqual(result.hits[0].column_name, "amount")

    @unittest.skipUnless(
        os.environ.get("DIRACDATA_RUN_FAISS_TESTS") == "1" and importlib.util.find_spec("faiss"),
        "set DIRACDATA_RUN_FAISS_TESTS=1 with faiss-cpu installed",
    )
    def test_faiss_hnsw_index_is_persisted_and_searchable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
                learning_vector_index_backend="faiss",
                learning_vector_index_algorithm="hnsw_flat",
                learning_vector_index_metric="inner_product",
                learning_faiss_hnsw_m=8,
                learning_faiss_ef_construction=32,
            )
            store = LocalObjectStore(tmpdir)
            index_key = learning_artifact_key(
                settings,
                run_id="embedding_test",
                relative_path="embeddings/faiss_hnsw.index",
            )
            metadata_key = learning_artifact_key(
                settings,
                run_id="embedding_test",
                relative_path="embeddings/faiss_hnsw_metadata.json",
            )
            vectors_key = learning_artifact_key(
                settings,
                run_id="embedding_test",
                relative_path="embeddings/column_embeddings.jsonl",
            )
            rows = [
                {
                    "document_id": "retrieval:column:payments.amount",
                    "table_name": "payments",
                    "column_name": "amount",
                    "embedding": [1.0, 0.0, 0.0],
                },
                {
                    "document_id": "retrieval:column:user_attributes.state",
                    "table_name": "user_attributes",
                    "column_name": "state",
                    "embedding": [0.0, 1.0, 0.0],
                },
            ]
            store.write_text(vectors_key, "\n".join(json.dumps(row) for row in rows) + "\n")
            vector_store = VectorIndexStore(settings=settings, object_store=store)

            manifest = vector_store.build(
                rows=rows,
                index_artifact_key=index_key,
                metadata_artifact_key=metadata_key,
            )
            result = vector_store.search_by_vector(
                query_embedding=[0.0, 0.95, 0.05],
                vectors_artifact_key=vectors_key,
                vector_index=manifest,
                top_k=1,
            )
            index_exists = store.exists(index_key)
            metadata_exists = store.exists(metadata_key)

        self.assertEqual(manifest["status"], "ok")
        self.assertTrue(index_exists)
        self.assertTrue(metadata_exists)
        self.assertEqual(result.backend, "faiss_hnsw")
        self.assertEqual(result.hits[0].table_name, "user_attributes")
        self.assertEqual(result.hits[0].column_name, "state")


def _collection(settings: DiracDataSettings) -> LearningCollection:
    run_id = "embedding_test"
    return LearningCollection(
        run_id=run_id,
        scope=LearningScope(settings.catalog, settings.database, settings.schema),
        table_profiles=[
            TableProfile(
                table_name="online_purchases",
                row_count=2,
                sample_artifact_key="samples/online_purchases.csv",
                columns=[
                    ColumnProfile(
                        "online_purchases",
                        "billing_client_ref",
                        "INTEGER",
                        0,
                        0.0,
                        2,
                    )
                ],
            )
        ],
        profile_artifact_key=learning_artifact_key(
            settings,
            run_id=run_id,
            relative_path="profiles/table_profiles.json",
        ),
        llm_context_artifact_key=learning_artifact_key(
            settings,
            run_id=run_id,
            relative_path="profiles/llm_context.json",
        ),
    )


def _write_retrieval_documents(
    settings: DiracDataSettings,
    store: LocalObjectStore,
    run_id: str,
) -> str:
    rows = [
        {
            "id": "retrieval:column:online_purchases.billing_client_ref",
            "retrieval_type": "column",
            "source_type": "column",
            "table_name": "online_purchases",
            "column_name": "billing_client_ref",
            "text_for_bm25": "online purchases billing client",
            "text_for_embedding": "billing client used as online buyer identity",
            "metadata": {},
        },
        {
            "id": "retrieval:table:online_purchases",
            "retrieval_type": "table_container",
            "source_type": "table",
            "table_name": "online_purchases",
            "column_name": None,
            "text_for_bm25": "online purchases",
            "text_for_embedding": None,
            "metadata": {},
        },
    ]
    key = learning_artifact_key(settings, run_id=run_id, relative_path="retrieval/documents.jsonl")
    store.write_text(key, "\n".join(json.dumps(row) for row in rows) + "\n")
    return key


if __name__ == "__main__":
    unittest.main()
