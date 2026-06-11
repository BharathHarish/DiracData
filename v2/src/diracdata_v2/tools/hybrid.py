"""Small local hybrid retrieval helpers for v2 tools."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


@dataclass(frozen=True)
class RetrievalDocument:
    id: str
    text: str
    source_type: str
    table_name: str | None = None
    column_name: str | None = None
    metadata: dict[str, Any] | None = None


def hybrid_search(
    *,
    documents: list[RetrievalDocument],
    query: str,
    search_terms: list[str] | None = None,
    top_k: int = 20,
    vector_rows: list[dict[str, Any]] | None = None,
    embedding_model: str = "BAAI/bge-small-en-v1.5",
    local_files_only: bool = True,
    rrf_k: int = 60,
) -> dict[str, Any]:
    queries = _search_queries(query=query, search_terms=search_terms)
    bm25_runs = [_bm25_rank(documents=documents, query=item) for item in queries]
    vector_runs: list[list[tuple[str, float]]] = []
    vector_notes: list[str] = []
    if vector_rows:
        for item in queries:
            vector_result = _vector_rank(
                query=item,
                vector_rows=vector_rows,
                embedding_model=embedding_model,
                local_files_only=local_files_only,
            )
            vector_runs.append(vector_result["hits"])
            vector_notes.extend(vector_result["notes"])
    merged = _rrf_merge(
        runs=bm25_runs + vector_runs,
        documents={document.id: document for document in documents},
        rrf_k=rrf_k,
        limit=top_k,
    )
    return {
        "query": query,
        "search_queries": queries,
        "hits": merged,
        "retrieval": {
            "bm25_runs": len(bm25_runs),
            "vector_runs": len(vector_runs),
            "vector_notes": sorted(set(vector_notes)),
            "rrf_k": rrf_k,
        },
    }


def load_jsonl_documents(path: Path) -> list[RetrievalDocument]:
    documents: list[RetrievalDocument] = []
    if not path.exists():
        return documents
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        text = (
            row.get("text_for_bm25")
            or row.get("text")
            or row.get("description")
            or row.get("content")
            or ""
        )
        document_id = str(row.get("id") or row.get("document_id") or "")
        if not document_id or not text:
            continue
        documents.append(
            RetrievalDocument(
                id=document_id,
                text=str(text),
                source_type=str(row.get("source_type") or row.get("retrieval_type") or "document"),
                table_name=row.get("table_name"),
                column_name=row.get("column_name"),
                metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else row,
            )
        )
    return documents


def load_vector_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row.get("embedding"), list) and row.get("document_id"):
            rows.append(row)
    return rows


def tokenize(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_]+", text.lower().replace("-", "_"))
        if len(token) > 1 and token not in STOPWORDS
    ]


def _search_queries(*, query: str, search_terms: list[str] | None) -> list[str]:
    seen: set[str] = set()
    queries = [query]
    queries.extend(search_terms or [])
    output = []
    for item in queries:
        clean = " ".join(str(item).split())
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            output.append(clean)
    return output


def _bm25_rank(*, documents: list[RetrievalDocument], query: str) -> list[tuple[str, float]]:
    query_terms = tokenize(query)
    if not query_terms:
        return []
    tokenized = [(document, tokenize(document.text)) for document in documents]
    lengths = {document.id: len(tokens) for document, tokens in tokenized}
    avgdl = sum(lengths.values()) / len(lengths) if lengths else 0.0
    df: dict[str, int] = {}
    for _, tokens in tokenized:
        for token in set(tokens):
            df[token] = df.get(token, 0) + 1
    scores: list[tuple[str, float]] = []
    n_docs = max(1, len(documents))
    k1 = 1.2
    b = 0.75
    delta = 1.0
    for document, tokens in tokenized:
        if not tokens:
            continue
        frequencies: dict[str, int] = {}
        for token in tokens:
            frequencies[token] = frequencies.get(token, 0) + 1
        score = 0.0
        dl = max(1, lengths[document.id])
        for term in query_terms:
            tf = frequencies.get(term, 0)
            if not tf:
                continue
            idf = math.log((n_docs + 1) / (df.get(term, 0) + 0.5))
            denom = tf + k1 * (1 - b + b * dl / max(avgdl, 1.0))
            score += idf * ((tf * (k1 + 1)) / denom + delta)
        if score:
            scores.append((document.id, score))
    scores.sort(key=lambda item: (-item[1], item[0]))
    return scores


def _vector_rank(
    *,
    query: str,
    vector_rows: list[dict[str, Any]],
    embedding_model: str,
    local_files_only: bool,
) -> dict[str, Any]:
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
    except ImportError as exc:
        return {"hits": [], "notes": [f"sentence-transformers unavailable: {exc}"]}
    try:
        model = _sentence_transformer(embedding_model, local_files_only)
        encoded = model.encode([query], normalize_embeddings=True)
        query_vector = encoded.tolist()[0] if hasattr(encoded, "tolist") else list(encoded[0])
    except Exception as exc:
        return {"hits": [], "notes": [f"vector search unavailable: {exc}"]}
    hits: list[tuple[str, float]] = []
    for row in vector_rows:
        vector = row.get("embedding")
        document_id = row.get("document_id")
        if not isinstance(vector, list) or not isinstance(document_id, str):
            continue
        score = sum(float(a) * float(b) for a, b in zip(query_vector, vector, strict=False))
        hits.append((document_id, score))
    hits.sort(key=lambda item: (-item[1], item[0]))
    return {"hits": hits, "notes": []}


@lru_cache(maxsize=4)
def _sentence_transformer(embedding_model: str, local_files_only: bool) -> Any:
    from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

    return SentenceTransformer(embedding_model, local_files_only=local_files_only)


def _rrf_merge(
    *,
    runs: list[list[tuple[str, float]]],
    documents: dict[str, RetrievalDocument],
    rrf_k: int,
    limit: int,
) -> list[dict[str, Any]]:
    scores: dict[str, float] = {}
    evidence: dict[str, list[dict[str, Any]]] = {}
    for run_index, run in enumerate(runs):
        for rank, (document_id, score) in enumerate(run, start=1):
            if document_id not in documents:
                continue
            scores[document_id] = scores.get(document_id, 0.0) + 1.0 / (rrf_k + rank)
            evidence.setdefault(document_id, []).append(
                {"run_index": run_index, "rank": rank, "raw_score": score}
            )
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[: max(1, limit)]
    hits = []
    for document_id, score in ranked:
        document = documents[document_id]
        hits.append(
            {
                "id": document.id,
                "score": score,
                "source_type": document.source_type,
                "table_name": document.table_name,
                "column_name": document.column_name,
                "text": document.text,
                "metadata": document.metadata or {},
                "rank_evidence": evidence.get(document_id, []),
            }
        )
    return hits
