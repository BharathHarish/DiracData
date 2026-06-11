"""Vector index artifact storage and search."""

from __future__ import annotations

import json
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from diracdata.config.settings import DiracDataSettings
from diracdata.storage.object_store import ObjectStore


@dataclass(frozen=True)
class VectorSearchHit:
    """One vector search result from learned embedding artifacts."""

    document_id: str
    score: float
    table_name: str | None = None
    column_name: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class VectorIndexSearchResult:
    """Search result with the backend actually used."""

    backend: str
    hits: list[VectorSearchHit]


class VectorIndexStore:
    """Build and query vector indexes backed by object-store artifacts."""

    def __init__(
        self,
        *,
        settings: DiracDataSettings,
        object_store: ObjectStore,
    ) -> None:
        self.settings = settings
        self.object_store = object_store

    def build(
        self,
        *,
        rows: list[dict[str, Any]],
        index_artifact_key: str,
        metadata_artifact_key: str,
    ) -> dict[str, Any]:
        """Persist a configured vector index from embedding rows.

        The JSONL embedding rows remain the canonical, auditable vector source. FAISS
        is a rebuildable acceleration artifact.
        """
        backend = self.settings.learning_vector_index_backend.strip().lower()
        algorithm = self.settings.learning_vector_index_algorithm.strip().lower()
        metric = self.settings.learning_vector_index_metric.strip().lower()
        manifest: dict[str, Any] = {
            "status": "disabled",
            "backend": backend,
            "algorithm": algorithm,
            "metric": metric,
            "index_artifact_key": None,
            "metadata_artifact_key": None,
            "vector_count": len(rows),
            "vector_dimensions": _vector_dimensions(rows),
            "notes": [],
        }
        if backend in {"", "none", "disabled"}:
            manifest["notes"].append("Vector index generation disabled by settings.")
            return manifest
        if not rows:
            manifest["status"] = "empty"
            manifest["notes"].append("No embedding vectors were available to index.")
            return manifest
        if backend != "faiss" or algorithm != "hnsw_flat":
            manifest["status"] = "unavailable"
            manifest["notes"].append(f"Unsupported vector index: {backend}/{algorithm}.")
            return manifest
        if metric != "inner_product":
            manifest["status"] = "unavailable"
            manifest["notes"].append(f"Unsupported FAISS HNSW metric: {metric}.")
            return manifest

        try:
            import faiss  # type: ignore[import-not-found]
            import numpy as np
        except ImportError as exc:
            manifest["status"] = "unavailable"
            manifest["notes"].append("faiss-cpu is not installed; install retrieval extras.")
            manifest["error"] = str(exc)
            return manifest

        try:
            vectors = _embedding_matrix(rows, np=np)
            vector_dimensions = int(vectors.shape[1])
            index = faiss.IndexHNSWFlat(
                vector_dimensions,
                self.settings.learning_faiss_hnsw_m,
                faiss.METRIC_INNER_PRODUCT,
            )
            index.hnsw.efConstruction = self.settings.learning_faiss_ef_construction
            index.add(vectors)

            metadata = _metadata(rows=rows, settings=self.settings, vector_dimensions=vector_dimensions)
            with tempfile.TemporaryDirectory() as tmpdir:
                index_path = Path(tmpdir) / "faiss_hnsw.index"
                faiss.write_index(index, str(index_path))
                self.object_store.write_bytes(
                    index_artifact_key,
                    index_path.read_bytes(),
                    content_type="application/octet-stream",
                )
            self.object_store.write_json(metadata_artifact_key, metadata)
        except Exception as exc:
            manifest["status"] = "unavailable"
            manifest["notes"].append("FAISS HNSW index build failed.")
            manifest["error"] = str(exc)
            return manifest

        manifest["status"] = "ok"
        manifest["index_artifact_key"] = index_artifact_key
        manifest["metadata_artifact_key"] = metadata_artifact_key
        manifest["vector_dimensions"] = vector_dimensions
        return manifest

    def search_by_vector(
        self,
        *,
        query_embedding: list[float],
        vectors_artifact_key: str,
        top_k: int,
        vector_index: dict[str, Any] | None = None,
    ) -> VectorIndexSearchResult:
        """Search vector artifacts with FAISS when available, else brute force JSONL."""
        if top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        if vector_index and vector_index.get("status") == "ok":
            faiss_result = self._search_faiss(
                query_embedding=query_embedding,
                top_k=top_k,
                vector_index=vector_index,
            )
            if faiss_result is not None:
                return faiss_result
        return self._search_bruteforce(
            query_embedding=query_embedding,
            vectors_artifact_key=vectors_artifact_key,
            top_k=top_k,
        )

    def search_text(
        self,
        *,
        query: str,
        vectors_artifact_key: str,
        top_k: int,
        vector_index: dict[str, Any] | None = None,
    ) -> VectorIndexSearchResult:
        """Encode text with the configured embedding model, then search artifacts."""
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is required for text vector search."
            ) from exc
        model = SentenceTransformer(
            self.settings.learning_embedding_model,
            local_files_only=self.settings.learning_embedding_local_files_only,
        )
        encoded = model.encode([query], normalize_embeddings=True)
        vector = encoded.tolist()[0] if hasattr(encoded, "tolist") else list(encoded[0])
        return self.search_by_vector(
            query_embedding=[float(item) for item in vector],
            vectors_artifact_key=vectors_artifact_key,
            top_k=top_k,
            vector_index=vector_index,
        )

    def _search_faiss(
        self,
        *,
        query_embedding: list[float],
        top_k: int,
        vector_index: dict[str, Any],
    ) -> VectorIndexSearchResult | None:
        index_key = vector_index.get("index_artifact_key")
        metadata_key = vector_index.get("metadata_artifact_key")
        if not isinstance(index_key, str) or not isinstance(metadata_key, str):
            return None
        if not self.object_store.exists(index_key) or not self.object_store.exists(metadata_key):
            return None
        try:
            import faiss  # type: ignore[import-not-found]
            import numpy as np
        except ImportError:
            return None

        metadata = self.object_store.read_json(metadata_key)
        if not isinstance(metadata, dict):
            return None
        documents = _documents_from_metadata(metadata)
        if not documents:
            return None
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "faiss_hnsw.index"
            index_path.write_bytes(self.object_store.read_bytes(index_key))
            index = faiss.read_index(str(index_path))
        query = _normalized_query(query_embedding, np=np)
        scores, indexes = index.search(query, min(top_k, len(documents)))
        hits = []
        for raw_score, raw_index in zip(scores[0], indexes[0], strict=False):
            position = int(raw_index)
            if position < 0 or position >= len(documents):
                continue
            document = documents[position]
            hits.append(_hit(document=document, score=float(raw_score)))
        return VectorIndexSearchResult(backend="faiss_hnsw", hits=hits)

    def _search_bruteforce(
        self,
        *,
        query_embedding: list[float],
        vectors_artifact_key: str,
        top_k: int,
    ) -> VectorIndexSearchResult:
        rows = _read_jsonl(self.object_store.read_text(vectors_artifact_key))
        query = _normalize(query_embedding)
        scored = [
            (
                _dot(query, _normalize([float(value) for value in row["embedding"]])),
                row,
            )
            for row in rows
            if isinstance(row.get("embedding"), list)
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        return VectorIndexSearchResult(
            backend="bruteforce_jsonl",
            hits=[
                VectorSearchHit(
                    document_id=str(row["document_id"]),
                    score=float(score),
                    table_name=_optional_str(row.get("table_name")),
                    column_name=_optional_str(row.get("column_name")),
                    metadata={},
                )
                for score, row in scored[:top_k]
            ],
        )


def _metadata(
    *,
    rows: list[dict[str, Any]],
    settings: DiracDataSettings,
    vector_dimensions: int,
) -> dict[str, Any]:
    return {
        "artifact_type": "vector_index_metadata",
        "backend": settings.learning_vector_index_backend,
        "algorithm": settings.learning_vector_index_algorithm,
        "metric": settings.learning_vector_index_metric,
        "normalized_vectors": True,
        "vector_count": len(rows),
        "vector_dimensions": vector_dimensions,
        "documents": [
            {
                "position": index,
                "document_id": row["document_id"],
                "table_name": row.get("table_name"),
                "column_name": row.get("column_name"),
            }
            for index, row in enumerate(rows)
        ],
    }


def _documents_from_metadata(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    documents = metadata.get("documents")
    if not isinstance(documents, list):
        return []
    return [document for document in documents if isinstance(document, dict)]


def _embedding_matrix(rows: list[dict[str, Any]], *, np: Any) -> Any:
    vectors = [
        [float(value) for value in row["embedding"]]
        for row in rows
        if isinstance(row.get("embedding"), list)
    ]
    if not vectors:
        raise ValueError("No valid embedding vectors found")
    matrix = np.asarray(vectors, dtype="float32")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.maximum(norms, 1e-12)
    return matrix


def _normalized_query(query_embedding: list[float], *, np: Any) -> Any:
    query = np.asarray([query_embedding], dtype="float32")
    norms = np.linalg.norm(query, axis=1, keepdims=True)
    return query / np.maximum(norms, 1e-12)


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [value / norm for value in vector]


def _dot(left: list[float], right: list[float]) -> float:
    return sum(left_value * right_value for left_value, right_value in zip(left, right, strict=False))


def _vector_dimensions(rows: list[dict[str, Any]]) -> int | None:
    if not rows:
        return None
    embedding = rows[0].get("embedding")
    if not isinstance(embedding, list):
        return None
    return len(embedding)


def _hit(*, document: dict[str, Any], score: float) -> VectorSearchHit:
    return VectorSearchHit(
        document_id=str(document["document_id"]),
        score=score,
        table_name=_optional_str(document.get("table_name")),
        column_name=_optional_str(document.get("column_name")),
        metadata={
            key: value
            for key, value in document.items()
            if key not in {"document_id", "table_name", "column_name"}
        },
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _read_jsonl(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]
