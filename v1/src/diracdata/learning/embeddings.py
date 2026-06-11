"""Build optional vector embedding artifacts from retrieval documents."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from diracdata.config.settings import DiracDataSettings
from diracdata.learning.models import LearningCollection, to_jsonable
from diracdata.learning.paths import active_learning_artifact_key, learning_artifact_key
from diracdata.retrieval.vector_index import VectorIndexStore
from diracdata.storage.object_store import ObjectStore


@dataclass(frozen=True)
class EmbeddingBuildResult:
    """Artifact keys produced by the embedding learning step."""

    run_id: str
    manifest_artifact_key: str
    active_manifest_artifact_key: str
    vectors_artifact_key: str
    active_vectors_artifact_key: str
    vector_index_artifact_key: str | None
    active_vector_index_artifact_key: str | None
    vector_index_metadata_artifact_key: str | None
    active_vector_index_metadata_artifact_key: str | None
    document_count: int
    vector_count: int
    status: str
    vector_index_status: str


class EmbeddingIndexBuilder:
    """Create vector artifacts from retrieval documents when configured."""

    def __init__(
        self,
        *,
        settings: DiracDataSettings,
        object_store: ObjectStore,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.object_store = object_store
        self.progress_callback = progress_callback

    def build(
        self,
        *,
        collection: LearningCollection,
        retrieval_documents_artifact_key: str,
    ) -> EmbeddingBuildResult:
        """Build embeddings from active retrieval documents as a separate learning phase."""
        self._emit("embeddings: load retrieval documents")
        retrieval_documents = _read_jsonl(self.object_store.read_text(retrieval_documents_artifact_key))
        embedding_documents = [
            document
            for document in retrieval_documents
            if document.get("retrieval_type") == "column" and document.get("text_for_embedding")
        ]
        keys = _artifact_keys(self.settings, collection.run_id)
        manifest = {
            "artifact_type": "embedding_manifest",
            "catalog": collection.scope.catalog,
            "database": collection.scope.database,
            "schema": collection.scope.schema,
            "run_id": collection.run_id,
            "built_at": datetime.now(UTC).isoformat(),
            "provider": self.settings.learning_embedding_provider,
            "model": self.settings.learning_embedding_model,
            "local_files_only": self.settings.learning_embedding_local_files_only,
            "source_retrieval_documents_artifact_key": retrieval_documents_artifact_key,
            "document_count": len(embedding_documents),
            "vector_count": 0,
            "vector_dimensions": None,
            "vectors_artifact_key": keys["vectors"],
            "active_vectors_artifact_key": keys["active_vectors"],
            "vector_index": {
                "status": "disabled",
                "backend": self.settings.learning_vector_index_backend,
                "algorithm": self.settings.learning_vector_index_algorithm,
                "metric": self.settings.learning_vector_index_metric,
                "index_artifact_key": None,
                "metadata_artifact_key": None,
                "vector_count": 0,
                "vector_dimensions": None,
                "notes": ["Vector index is built only when embedding vectors are available."],
            },
            "status": "disabled",
            "notes": [],
        }
        rows = self._embedding_rows(embedding_documents=embedding_documents, manifest=manifest)
        manifest["vector_index"] = VectorIndexStore(
            settings=self.settings,
            object_store=self.object_store,
        ).build(
            rows=rows,
            index_artifact_key=keys["vector_index"],
            metadata_artifact_key=keys["vector_index_metadata"],
        )
        active_vector_index = dict(manifest["vector_index"])
        if active_vector_index.get("status") == "ok":
            active_vector_index["index_artifact_key"] = keys["active_vector_index"]
            active_vector_index["metadata_artifact_key"] = keys["active_vector_index_metadata"]
            VectorIndexStore(settings=self.settings, object_store=self.object_store).build(
                rows=rows,
                index_artifact_key=keys["active_vector_index"],
                metadata_artifact_key=keys["active_vector_index_metadata"],
            )
        manifest["active_vector_index"] = active_vector_index

        self.object_store.write_json(keys["manifest"], manifest)
        self.object_store.write_json(keys["active_manifest"], manifest)
        payload = _jsonl(rows)
        self.object_store.write_text(keys["vectors"], payload, content_type="application/jsonl")
        self.object_store.write_text(keys["active_vectors"], payload, content_type="application/jsonl")
        self._update_active_manifest(keys=keys, manifest=manifest)

        return EmbeddingBuildResult(
            run_id=collection.run_id,
            manifest_artifact_key=keys["manifest"],
            active_manifest_artifact_key=keys["active_manifest"],
            vectors_artifact_key=keys["vectors"],
            active_vectors_artifact_key=keys["active_vectors"],
            vector_index_artifact_key=_optional_key(manifest["vector_index"], "index_artifact_key"),
            active_vector_index_artifact_key=_optional_key(
                manifest["active_vector_index"],
                "index_artifact_key",
            ),
            vector_index_metadata_artifact_key=_optional_key(
                manifest["vector_index"],
                "metadata_artifact_key",
            ),
            active_vector_index_metadata_artifact_key=_optional_key(
                manifest["active_vector_index"],
                "metadata_artifact_key",
            ),
            document_count=int(manifest["document_count"]),
            vector_count=int(manifest["vector_count"]),
            status=str(manifest["status"]),
            vector_index_status=str(manifest["vector_index"]["status"]),
        )

    def _embedding_rows(
        self,
        *,
        embedding_documents: list[dict[str, Any]],
        manifest: dict[str, Any],
    ) -> list[dict[str, Any]]:
        provider = self.settings.learning_embedding_provider.strip().lower()
        if provider in {"", "none", "disabled"}:
            manifest["status"] = "disabled"
            manifest["notes"].append("Embedding generation disabled by settings.")
            return []
        if provider not in {"bge", "sentence_transformers", "sentence-transformer"}:
            manifest["status"] = "unavailable"
            manifest["notes"].append(
                f"Unsupported embedding provider: {self.settings.learning_embedding_provider}"
            )
            return []

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as exc:
            manifest["status"] = "unavailable"
            manifest["notes"].append(
                "sentence-transformers is not installed; install retrieval extras to generate BGE embeddings."
            )
            manifest["error"] = str(exc)
            return []

        self._emit("embeddings: encode column retrieval documents")
        try:
            model = SentenceTransformer(
                self.settings.learning_embedding_model,
                local_files_only=self.settings.learning_embedding_local_files_only,
            )
        except Exception as exc:
            if self.settings.learning_embedding_local_files_only:
                manifest["status"] = "unavailable"
                manifest["notes"].append("Embedding model could not be loaded from local cache.")
                manifest["error"] = str(exc)
                return []
            manifest["notes"].append(
                "Embedding model load failed with online lookup; retrying from local cache."
            )
            try:
                model = SentenceTransformer(
                    self.settings.learning_embedding_model,
                    local_files_only=True,
                )
            except Exception as fallback_exc:
                manifest["status"] = "unavailable"
                manifest["notes"].append("Embedding model could not be loaded online or from local cache.")
                manifest["error"] = str(fallback_exc)
                return []
        texts = [str(document["text_for_embedding"]) for document in embedding_documents]
        try:
            encoded = model.encode(texts, normalize_embeddings=True)
        except Exception as exc:
            manifest["status"] = "unavailable"
            manifest["notes"].append("Embedding encoding failed.")
            manifest["error"] = str(exc)
            return []
        vectors = encoded.tolist() if hasattr(encoded, "tolist") else encoded
        rows = [
            {
                "document_id": document["id"],
                "table_name": document["table_name"],
                "column_name": document["column_name"],
                "embedding": vector,
            }
            for document, vector in zip(embedding_documents, vectors, strict=True)
        ]
        manifest["status"] = "ok"
        manifest["vector_count"] = len(rows)
        manifest["vector_dimensions"] = len(rows[0]["embedding"]) if rows else 0
        return rows

    def _update_active_manifest(self, *, keys: dict[str, str], manifest: dict[str, Any]) -> None:
        active_manifest_key = active_learning_artifact_key(self.settings, relative_path="manifest.json")
        if not self.object_store.exists(active_manifest_key):
            return
        active_manifest = self.object_store.read_json(active_manifest_key)
        if not isinstance(active_manifest, dict):
            return
        active_manifest.setdefault("immutable_artifacts", {})["embedding_manifest_artifact_key"] = (
            keys["manifest"]
        )
        active_manifest.setdefault("active_artifacts", {})["embedding_manifest_artifact_key"] = (
            keys["active_manifest"]
        )
        active_manifest["embeddings"] = {
            "status": manifest["status"],
            "document_count": manifest["document_count"],
            "vector_count": manifest["vector_count"],
            "provider": manifest["provider"],
            "model": manifest["model"],
            "vector_index": manifest.get("active_vector_index") or manifest.get("vector_index"),
        }
        self.object_store.write_json(active_manifest_key, active_manifest)

    def _emit(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)


def _artifact_keys(settings: DiracDataSettings, run_id: str) -> dict[str, str]:
    relative_paths = {
        "manifest": "embeddings/manifest.json",
        "vectors": "embeddings/column_embeddings.jsonl",
        "vector_index": "embeddings/faiss_hnsw.index",
        "vector_index_metadata": "embeddings/faiss_hnsw_metadata.json",
    }
    keys = {
        name: learning_artifact_key(settings, run_id=run_id, relative_path=path)
        for name, path in relative_paths.items()
    }
    keys.update(
        {
            f"active_{name}": active_learning_artifact_key(settings, relative_path=path)
            for name, path in relative_paths.items()
        }
    )
    return keys


def _read_jsonl(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(to_jsonable(row), sort_keys=True) for row in rows) + (
        "\n" if rows else ""
    )


def _optional_key(payload: dict[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    return value if isinstance(value, str) else None
