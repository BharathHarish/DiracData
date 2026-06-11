"""Hybrid candidate binding search for answer-time SQL agents."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import replace
from typing import Any

from diracdata.config.settings import DiracDataSettings
from diracdata.learning.paths import active_learning_artifact_key
from diracdata.retrieval.vector_index import VectorIndexStore
from diracdata.storage.object_store import ObjectStore


FILTER_ROLES = {"filter", "entity", "time", "threshold", "unknown"}
METRIC_TERMS = {
    "metric",
    "rate",
    "ratio",
    "volume",
    "revenue",
    "sales",
    "count",
    "average",
    "sum",
}
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
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


class CandidateBindingSearchService:
    """Resolve likely schema candidates and confounders from a user question."""

    def __init__(
        self,
        *,
        settings: DiracDataSettings,
        object_store: ObjectStore,
        extractor_model: object | None = None,
    ) -> None:
        self.settings = settings
        self.object_store = object_store
        self._extractor_model = extractor_model
        self._cache: dict[str, dict[str, Any]] = {}

    def search(self, nl_query: str) -> dict[str, Any]:
        """Run hybrid retrieval and return a compact candidate binding packet."""
        query = nl_query.strip()
        if not query:
            return {"status": "empty", "query": nl_query}
        if not self.settings.agent_candidate_search_enabled:
            return {"status": "disabled", "query": query}
        cached = self._cache.get(query)
        if cached is not None:
            return cached

        artifacts = self._load_artifacts()
        if not artifacts["documents"]:
            result = {
                "status": "missing_artifacts",
                "query": query,
                "notes": ["Active retrieval documents are missing."],
            }
            self._cache[query] = result
            return result

        extraction = self._extract_intent(query)
        search_queries = _bounded_search_queries(
            query=query,
            extraction=extraction,
            max_queries=self.settings.agent_candidate_search_max_queries,
        )
        bm25_rankings = _bm25_rankings(
            search_queries=search_queries,
            bm25_index=artifacts["bm25_index"],
            documents_by_id=artifacts["documents_by_id"],
            top_k=self.settings.agent_candidate_search_per_query_limit,
        )
        vector_rankings, vector_notes = self._vector_rankings(
            search_queries=search_queries,
            embedding_manifest=artifacts["embedding_manifest"],
            top_k=self.settings.agent_candidate_search_per_query_limit,
        )
        merged = _rrf_merge(
            search_queries=search_queries,
            bm25_rankings=bm25_rankings,
            vector_rankings=vector_rankings,
            documents_by_id=artifacts["documents_by_id"],
            rrf_k=self.settings.learning_rrf_k,
        )
        column_candidates = _column_candidates(
            merged=merged,
            descriptions=artifacts["descriptions"],
            limit=self.settings.agent_candidate_search_limit,
        )
        profile_value_candidates = _profile_value_candidates(
            query=query,
            profile=artifacts["profile"],
        )
        bindings = _resolve_predicate_bindings(
            query=query,
            extraction=extraction,
            profile_value_candidates=profile_value_candidates,
            merged=merged,
            descriptions=artifacts["descriptions"],
        )

        result = {
            "status": "ok",
            "query": query,
            "extraction": extraction,
            "search_queries": search_queries,
            "candidate_columns": column_candidates,
            "predicate_bindings": bindings["predicate_bindings"],
            "rejected_confounders": bindings["rejected_confounders"],
            "profile_value_candidates": profile_value_candidates[: self.settings.agent_candidate_search_limit],
            "retrieval": {
                "bm25_available": bool(artifacts["bm25_index"]),
                "vector_available": bool(artifacts["embedding_manifest"]),
                "vector_notes": vector_notes,
                "rrf_k": self.settings.learning_rrf_k,
            },
        }
        self._cache[query] = result
        return result

    def _load_artifacts(self) -> dict[str, Any]:
        documents = _read_jsonl_if_exists(
            self.object_store,
            _active_key(self.settings, "retrieval/documents.jsonl"),
        )
        return {
            "documents": documents,
            "documents_by_id": {
                str(document.get("id")): document
                for document in documents
                if isinstance(document.get("id"), str)
            },
            "bm25_index": _read_json_if_exists(
                self.object_store,
                _active_key(self.settings, "retrieval/bm25_plus_index.json"),
            ),
            "embedding_manifest": _read_json_if_exists(
                self.object_store,
                _active_key(self.settings, "embeddings/manifest.json"),
            ),
            "descriptions": _read_json_if_exists(
                self.object_store,
                _active_key(self.settings, "descriptions/metadata_descriptions.json"),
            ),
            "profile": self._load_profile(),
        }

    def _load_profile(self) -> dict[str, Any]:
        context = _read_json_if_exists(
            self.object_store,
            _active_key(self.settings, "contexts/learned_context.json"),
        )
        profile_key = context.get("profile_artifact_key") if isinstance(context, dict) else None
        if isinstance(profile_key, str) and self.object_store.exists(profile_key):
            payload = self.object_store.read_json(profile_key)
            return payload if isinstance(payload, dict) else {}
        profile = _read_json_if_exists(
            self.object_store,
            _active_key(self.settings, "profiles/table_profiles.json"),
        )
        return profile if isinstance(profile, dict) else {}

    def _extract_intent(self, query: str) -> dict[str, Any]:
        if self.settings.agent_candidate_search_llm_enabled:
            extracted = self._extract_with_llm(query)
            if extracted is not None:
                return _normalize_extraction(extracted, query=query)
        return _heuristic_extraction(query)

    def _extract_with_llm(self, query: str) -> dict[str, Any] | None:
        try:
            model = self._extractor_model or self._create_extractor_model()
            from diracdata.agents.prompt_loader import load_candidate_intent_prompt_v1

            messages = [
                {"role": "system", "content": load_candidate_intent_prompt_v1()},
                {"role": "user", "content": query},
            ]
            try:
                response = model.invoke(
                    messages,
                    config={"callbacks": [], "metadata": {"dirac_internal": "candidate_search"}},
                )
            except TypeError:
                response = model.invoke(messages)
            payload = _extract_json_object(_content_text(getattr(response, "content", response)))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _create_extractor_model(self) -> object:
        from diracdata.llms import agent_chat_model_from_settings

        settings = replace(
            self.settings,
            agent_model_profile=(
                self.settings.agent_candidate_search_model_profile
                if self.settings.agent_candidate_search_model_profile is not None
                else self.settings.agent_model_profile
            ),
            agent_llm_provider=(
                self.settings.agent_candidate_search_llm_provider
                if self.settings.agent_candidate_search_llm_provider is not None
                else self.settings.agent_llm_provider
            ),
            agent_llm_model=(
                self.settings.agent_candidate_search_llm_model
                if self.settings.agent_candidate_search_llm_model is not None
                else self.settings.agent_llm_model
            ),
            agent_llm_max_tokens=self.settings.agent_candidate_search_llm_max_tokens,
            agent_llm_temperature=self.settings.agent_candidate_search_llm_temperature,
        )
        self._extractor_model = agent_chat_model_from_settings(settings)
        return self._extractor_model

    def _vector_rankings(
        self,
        *,
        search_queries: list[dict[str, str]],
        embedding_manifest: dict[str, Any],
        top_k: int,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
        if not isinstance(embedding_manifest, dict) or embedding_manifest.get("status") != "ok":
            return {}, ["Vector embeddings are unavailable."]
        vectors_key = embedding_manifest.get("active_vectors_artifact_key")
        if not isinstance(vectors_key, str) or not self.object_store.exists(vectors_key):
            return {}, ["Active column embedding vectors are missing."]
        vector_index = embedding_manifest.get("active_vector_index")
        if not isinstance(vector_index, dict):
            vector_index = None

        vector_store = VectorIndexStore(settings=self.settings, object_store=self.object_store)
        rankings: dict[str, list[dict[str, Any]]] = {}
        notes: list[str] = []
        query_texts = [row["query"] for row in search_queries if row.get("query")]
        try:
            encoded_vectors = _encode_embedding_queries(
                settings=self.settings,
                query_texts=query_texts,
            )
        except Exception as exc:
            return {}, [f"Vector search failed to encode search queries: {exc}"]

        for query_text, vector in zip(query_texts, encoded_vectors, strict=False):
            try:
                result = vector_store.search_by_vector(
                    query_embedding=vector,
                    vectors_artifact_key=vectors_key,
                    vector_index=vector_index,
                    top_k=top_k,
                )
            except Exception as exc:
                notes.append(f"Vector search failed for {query_text!r}: {exc}")
                continue
            rankings[query_text] = [
                {
                    "document_id": hit.document_id,
                    "rank": rank,
                    "score": hit.score,
                    "table_name": hit.table_name,
                    "column_name": hit.column_name,
                }
                for rank, hit in enumerate(result.hits, start=1)
            ]
        return rankings, notes


def compact_candidate_binding_context(result: dict[str, Any]) -> dict[str, Any]:
    """Return the prompt-safe subset of a candidate search result."""
    if result.get("status") != "ok":
        return {"status": result.get("status"), "notes": result.get("notes", [])}
    return {
        "status": "ok",
        "predicate_bindings": result.get("predicate_bindings", []),
        "rejected_confounders": result.get("rejected_confounders", []),
        "candidate_columns": result.get("candidate_columns", [])[:20],
        "search_queries": [
            {
                "query": row.get("query"),
                "source_phrase": row.get("source_phrase"),
                "purpose": row.get("purpose"),
            }
            for row in result.get("search_queries", [])[:12]
        ],
    }


def _bounded_search_queries(
    *,
    query: str,
    extraction: dict[str, Any],
    max_queries: int,
) -> list[dict[str, str]]:
    rows = [
        {"query": query, "source_phrase": "full_query", "purpose": "full_query"},
    ]
    for item in _as_list(extraction.get("search_queries")):
        if not isinstance(item, dict):
            continue
        text = str(item.get("query") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "query": text,
                "source_phrase": str(item.get("source_phrase") or text),
                "purpose": str(item.get("purpose") or "unknown"),
            }
        )
    for phrase in _as_list(extraction.get("phrases")):
        if not isinstance(phrase, dict):
            continue
        text = str(phrase.get("text") or "").strip()
        if text:
            rows.append(
                {
                    "query": text,
                    "source_phrase": text,
                    "purpose": str(phrase.get("role") or "unknown"),
                }
            )
        entity_hint = str(phrase.get("entity_hint") or "").strip()
        if entity_hint and text and entity_hint.lower() not in text.lower():
            rows.append(
                {
                    "query": f"{text} {entity_hint}",
                    "source_phrase": text,
                    "purpose": str(phrase.get("role") or "unknown"),
                }
            )
    return _dedupe_query_rows(rows)[: max(1, max_queries)]


def _normalize_extraction(payload: dict[str, Any], *, query: str) -> dict[str, Any]:
    phrases = []
    for row in _as_list(payload.get("phrases")):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        literals = [str(item).strip() for item in _as_list(row.get("literals")) if str(item).strip()]
        phrases.append(
            {
                "text": text,
                "role": _normalized_role(str(row.get("role") or "unknown")),
                "entity_hint": str(row.get("entity_hint") or "").strip(),
                "literals": literals,
            }
        )
    search_queries = []
    for row in _as_list(payload.get("search_queries")):
        if not isinstance(row, dict):
            continue
        text = str(row.get("query") or "").strip()
        if not text:
            continue
        search_queries.append(
            {
                "query": text,
                "source_phrase": str(row.get("source_phrase") or text).strip(),
                "purpose": _normalized_role(str(row.get("purpose") or "unknown")),
            }
        )
    if not phrases and not search_queries:
        return _heuristic_extraction(query)
    return {"phrases": phrases, "search_queries": search_queries}


def _heuristic_extraction(query: str) -> dict[str, Any]:
    clean = _clean_phrase(query)
    phrases: list[dict[str, Any]] = []
    for text in _heuristic_phrases(clean):
        role = _heuristic_role(text)
        phrases.append(
            {
                "text": text,
                "role": role,
                "entity_hint": _entity_hint(text),
                "literals": _heuristic_literals(text),
            }
        )
    search_queries = [
        {"query": clean, "source_phrase": "full_query", "purpose": "full_query"},
        *[
            {
                "query": phrase["text"],
                "source_phrase": phrase["text"],
                "purpose": phrase["role"],
            }
            for phrase in phrases
        ],
    ]
    return {"phrases": phrases, "search_queries": _dedupe_query_rows(search_queries)}


def _heuristic_phrases(query: str) -> list[str]:
    phrases: list[str] = []
    patterns = [
        r"\b(?:total|sum|average|avg|count|rate|ratio|volume|revenue|sales)\s+(?:[a-z0-9-]+\s+){0,4}[a-z0-9-]+\b",
        r"\b[a-z0-9-]+\s+(?:risk|status|state|type|mode|method|category|segment|band|tier)\s+[a-z0-9-]+\b",
        r"\b[a-z0-9-]+-risk\s+[a-z0-9-]+\b",
        r"\b(?:verified|active|inactive|successful|failed|approved|rejected|pending)\s+(?:[a-z0-9-]+\s+){0,3}[a-z0-9-]+\b",
        r"\b[a-z0-9-]+\s+(?:surface|mode|method|channel|type|category|segment|state|status|band|tier)\b",
        r"\b(?:at least|greater than|less than|more than|fewer than)\s+\d+(?:\.\d+)?\s+(?:[a-z0-9-]+\s*){0,4}\b",
    ]
    for pattern in patterns:
        phrases.extend(match.group(0) for match in re.finditer(pattern, query, re.IGNORECASE))
    for match in re.finditer(
        r"\b(?:from|for|on|in|by)\s+([^,.?]+?)(?=\b(?:from|for|on|by|only|and compare|compare)\b|[,.?]|$)",
        query,
        re.IGNORECASE,
    ):
        phrases.append(match.group(1))
    if " by " in f" {query.lower()} ":
        tail = re.split(r"\bby\b", query, maxsplit=1, flags=re.IGNORECASE)[-1]
        tail = re.split(r"\b(?:only|where|for|from)\b", tail, maxsplit=1, flags=re.IGNORECASE)[0]
        phrases.extend(part.strip() for part in re.split(r"\band\b|,", tail) if part.strip())
    phrases.append(query)
    return _dedupe_strings([_clean_phrase(phrase) for phrase in phrases if _clean_phrase(phrase)])


def _heuristic_role(text: str) -> str:
    normalized = f" {text.lower()} "
    if _mentions_any(normalized, {" rate", " ratio", " volume", " revenue", " sales"}):
        return "metric"
    if _mentions_any(normalized, {" by ", "surface", "mode", "type", "category", "segment"}):
        return "dimension"
    if re.search(r"\b(19|20)\d{2}\b", normalized) or _mentions_any(
        normalized,
        {"month", "day", "week", "quarter", "year"},
    ):
        return "time"
    if _mentions_any(normalized, {"at least", "greater than", "less than", "only include"}):
        return "threshold"
    return "filter"


def _entity_hint(text: str) -> str:
    tokens = [token for token in _tokens(text) if token not in STOPWORDS and token not in METRIC_TERMS]
    non_values = [token for token in tokens if not token.isdigit()]
    return " ".join(non_values[-3:])


def _heuristic_literals(text: str) -> list[str]:
    values = []
    values.extend(re.findall(r"\b(?:19|20)\d{2}\b", text))
    for single_quoted, double_quoted in re.findall(r"'([^']+)'|\"([^\"]+)\"", text):
        values.append(single_quoted or double_quoted)
    values.extend(re.findall(r"\b\d+(?:\.\d+)?\b", text))
    return _dedupe_strings([value for value in values if isinstance(value, str) and value])


def _bm25_rankings(
    *,
    search_queries: list[dict[str, str]],
    bm25_index: dict[str, Any],
    documents_by_id: dict[str, dict[str, Any]],
    top_k: int,
) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(bm25_index, dict) or bm25_index.get("algorithm") != "bm25_plus":
        return {}
    parameters = bm25_index.get("parameters")
    if not isinstance(parameters, dict):
        return {}
    rankings = {}
    for row in search_queries:
        query = row["query"]
        tokens = _tokens(query)
        if not tokens:
            continue
        scored = []
        for document in _as_list(bm25_index.get("documents")):
            if not isinstance(document, dict):
                continue
            score = _bm25_score(tokens=tokens, document=document, index=bm25_index)
            if score <= 0:
                continue
            doc_id = str(document.get("id") or "")
            source_doc = documents_by_id.get(doc_id, {})
            scored.append(
                {
                    "document_id": doc_id,
                    "score": score,
                    "retrieval_type": source_doc.get("retrieval_type"),
                    "table_name": source_doc.get("table_name"),
                    "column_name": source_doc.get("column_name"),
                }
            )
        scored.sort(key=lambda item: (-float(item["score"]), str(item["document_id"])))
        rankings[query] = [
            {**item, "rank": rank}
            for rank, item in enumerate(scored[: max(1, top_k)], start=1)
        ]
    return rankings


def _encode_embedding_queries(
    *,
    settings: DiracDataSettings,
    query_texts: list[str],
) -> list[list[float]]:
    if not query_texts:
        return []
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required for text vector search."
        ) from exc
    model = SentenceTransformer(
        settings.learning_embedding_model,
        local_files_only=settings.learning_embedding_local_files_only,
    )
    encoded = model.encode(query_texts, normalize_embeddings=True)
    rows = encoded.tolist() if hasattr(encoded, "tolist") else encoded
    return [[float(value) for value in row] for row in rows]


def _bm25_score(*, tokens: list[str], document: dict[str, Any], index: dict[str, Any]) -> float:
    parameters = index.get("parameters") if isinstance(index.get("parameters"), dict) else {}
    k1 = float(parameters.get("k1", 1.2))
    b = float(parameters.get("b", 0.75))
    delta = float(parameters.get("delta", 1.0))
    avgdl = float(index.get("average_document_length") or 1.0)
    dl = float(document.get("length") or 1.0)
    term_frequencies = document.get("term_frequencies")
    idf = index.get("idf")
    if not isinstance(term_frequencies, dict) or not isinstance(idf, dict):
        return 0.0
    score = 0.0
    for token in tokens:
        frequency = int(term_frequencies.get(token, 0) or 0)
        if frequency <= 0:
            continue
        token_idf = float(idf.get(token, 0.0) or 0.0)
        denominator = frequency + k1 * (1 - b + b * dl / max(avgdl, 1.0))
        score += token_idf * (((frequency * (k1 + 1)) / denominator) + delta)
    return score


def _rrf_merge(
    *,
    search_queries: list[dict[str, str]],
    bm25_rankings: dict[str, list[dict[str, Any]]],
    vector_rankings: dict[str, list[dict[str, Any]]],
    documents_by_id: dict[str, dict[str, Any]],
    rrf_k: int,
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    query_purpose = {row["query"]: row.get("purpose", "unknown") for row in search_queries}
    for source_name, rankings_by_query in [
        ("bm25", bm25_rankings),
        ("vector", vector_rankings),
    ]:
        for query, ranking in rankings_by_query.items():
            for hit in ranking:
                doc_id = str(hit.get("document_id") or "")
                if not doc_id:
                    continue
                source_doc = documents_by_id.get(doc_id, {})
                row = merged.setdefault(
                    doc_id,
                    {
                        "document_id": doc_id,
                        "table_name": hit.get("table_name") or source_doc.get("table_name"),
                        "column_name": hit.get("column_name") or source_doc.get("column_name"),
                        "retrieval_type": source_doc.get("retrieval_type") or hit.get("retrieval_type"),
                        "rrf_score": 0.0,
                        "bm25_rank": None,
                        "bm25_score": None,
                        "vector_rank": None,
                        "vector_score": None,
                        "matched_queries": [],
                    },
                )
                rank = int(hit.get("rank") or 0)
                row["rrf_score"] = float(row["rrf_score"]) + (1.0 / (max(1, rrf_k) + rank))
                if source_name == "bm25":
                    row["bm25_rank"] = _min_optional_rank(row.get("bm25_rank"), rank)
                    row["bm25_score"] = max(float(row.get("bm25_score") or 0.0), float(hit.get("score") or 0.0))
                else:
                    row["vector_rank"] = _min_optional_rank(row.get("vector_rank"), rank)
                    row["vector_score"] = max(float(row.get("vector_score") or 0.0), float(hit.get("score") or 0.0))
                row["matched_queries"].append(
                    {
                        "query": query,
                        "purpose": query_purpose.get(query, "unknown"),
                        "source": source_name,
                        "rank": rank,
                        "score": round(float(hit.get("score") or 0.0), 6),
                    }
                )
    return merged


def _column_candidates(
    *,
    merged: dict[str, dict[str, Any]],
    descriptions: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    rows = [
        row
        for row in merged.values()
        if row.get("retrieval_type") == "column" and row.get("table_name") and row.get("column_name")
    ]
    rows.sort(
        key=lambda row: (
            -float(row.get("rrf_score") or 0.0),
            str(row.get("table_name")),
            str(row.get("column_name")),
        )
    )
    return [
        _candidate_payload(row=row, descriptions=descriptions)
        for row in rows[: max(1, limit)]
    ]


def _candidate_payload(*, row: dict[str, Any], descriptions: dict[str, Any]) -> dict[str, Any]:
    table_name = str(row.get("table_name") or "")
    column_name = str(row.get("column_name") or "")
    return {
        "table_name": table_name,
        "column_name": column_name,
        "column_ref": f"{table_name}.{column_name}",
        "confidence_score": round(float(row.get("rrf_score") or 0.0), 6),
        "rrf_score": round(float(row.get("rrf_score") or 0.0), 6),
        "vector_rank": row.get("vector_rank"),
        "vector_score": _round_optional(row.get("vector_score")),
        "bm25_rank": row.get("bm25_rank"),
        "bm25_score": _round_optional(row.get("bm25_score")),
        "short_description": _column_description(
            descriptions=descriptions,
            table_name=table_name,
            column_name=column_name,
        ),
        "matched_queries": row.get("matched_queries", [])[:6],
    }


def _profile_value_candidates(*, query: str, profile: dict[str, Any]) -> list[dict[str, str]]:
    normalized_query = _normalize_value_text(query)
    rows = []
    for table in _as_list(profile.get("tables")):
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("table_name") or "")
        for column in _as_list(table.get("columns")):
            if not isinstance(column, dict):
                continue
            column_name = str(column.get("column_name") or "")
            values = []
            for item in _as_list(column.get("top_values")):
                if isinstance(item, dict):
                    values.append(item.get("value"))
            values.extend(_as_list(column.get("distinct_values")))
            for value in values:
                if not isinstance(value, str):
                    continue
                clean = value.strip()
                if not _profile_value_is_actionable(clean):
                    continue
                if _normalized_value_in_text(clean, normalized_query):
                    rows.append(
                        {
                            "table_name": table_name,
                            "column_name": column_name,
                            "column_ref": f"{table_name}.{column_name}",
                            "value": clean,
                        }
                    )
    return _dedupe_value_candidates(rows)


def _resolve_predicate_bindings(
    *,
    query: str,
    extraction: dict[str, Any],
    profile_value_candidates: list[dict[str, str]],
    merged: dict[str, dict[str, Any]],
    descriptions: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    predicate_bindings = []
    rejected_confounders = []
    if not profile_value_candidates:
        return {"predicate_bindings": [], "rejected_confounders": []}

    phrases = _binding_phrases(query=query, extraction=extraction)
    for phrase in phrases:
        phrase_text = phrase["text"]
        if phrase["role"] not in FILTER_ROLES:
            continue
        value_groups: dict[str, list[dict[str, str]]] = defaultdict(list)
        normalized_phrase = _normalize_value_text(phrase_text)
        for candidate in profile_value_candidates:
            if _normalized_value_in_text(candidate["value"], normalized_phrase):
                value_groups[_normalize_lookup(candidate["value"])].append(candidate)
        for candidates in value_groups.values():
            if len(candidates) < 2:
                selected = candidates[0]
                predicate_bindings.append(
                    _binding_payload(
                        phrase=phrase,
                        selected=selected,
                        candidates=candidates,
                        merged=merged,
                        descriptions=descriptions,
                        rejected=[],
                    )
                )
                continue

            scored = [
                (
                    _binding_score(
                        phrase=phrase,
                        candidate=candidate,
                        merged=merged,
                        descriptions=descriptions,
                    ),
                    candidate,
                )
                for candidate in candidates
            ]
            scored.sort(key=lambda item: (-item[0], item[1]["column_ref"]))
            selected_score, selected = scored[0]
            rejected = [candidate for score, candidate in scored[1:] if selected_score > score]
            predicate_bindings.append(
                _binding_payload(
                    phrase=phrase,
                    selected=selected,
                    candidates=candidates,
                    merged=merged,
                    descriptions=descriptions,
                    rejected=rejected,
                )
            )
            for candidate in rejected:
                rejected_confounders.append(
                    {
                        "user_phrase": phrase_text,
                        "column_ref": candidate["column_ref"],
                        "table_name": candidate["table_name"],
                        "column_name": candidate["column_name"],
                        "value": candidate["value"],
                        "reason": _rejection_reason(
                            phrase=phrase,
                            selected=selected,
                            rejected=candidate,
                            descriptions=descriptions,
                        ),
                    }
                )
    return {
        "predicate_bindings": _dedupe_bindings(predicate_bindings),
        "rejected_confounders": _dedupe_rejected(rejected_confounders),
    }


def _binding_phrases(*, query: str, extraction: dict[str, Any]) -> list[dict[str, Any]]:
    phrases = []
    for row in _as_list(extraction.get("phrases")):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        phrases.append(
            {
                "text": text,
                "role": _normalized_role(str(row.get("role") or "unknown")),
                "entity_hint": str(row.get("entity_hint") or "").strip(),
                "literals": [str(item) for item in _as_list(row.get("literals"))],
            }
        )
    if not phrases:
        phrases = _heuristic_extraction(query)["phrases"]
    return phrases


def _binding_score(
    *,
    phrase: dict[str, Any],
    candidate: dict[str, str],
    merged: dict[str, dict[str, Any]],
    descriptions: dict[str, Any],
) -> float:
    phrase_tokens = _semantic_tokens(
        f"{phrase.get('text', '')} {phrase.get('entity_hint', '')}"
    )
    table_name = candidate["table_name"]
    column_name = candidate["column_name"]
    evidence_text = " ".join(
        [
            table_name.replace("_", " "),
            column_name.replace("_", " "),
            _table_description(descriptions=descriptions, table_name=table_name),
            _column_description(
                descriptions=descriptions,
                table_name=table_name,
                column_name=column_name,
            ),
        ]
    )
    evidence_tokens = _semantic_tokens(evidence_text)
    lexical = len(phrase_tokens & evidence_tokens)
    doc_id = f"retrieval:column:{table_name}.{column_name}"
    retrieval = float(merged.get(doc_id, {}).get("rrf_score") or 0.0) * 100.0
    table_doc = merged.get(f"retrieval:table:{table_name}", {})
    table_retrieval = float(table_doc.get("rrf_score") or 0.0) * 30.0
    entity_hint = str(phrase.get("entity_hint") or "")
    entity_bonus = len(_semantic_tokens(entity_hint) & evidence_tokens) if entity_hint else 0
    return lexical + retrieval + table_retrieval + entity_bonus


def _binding_payload(
    *,
    phrase: dict[str, Any],
    selected: dict[str, str],
    candidates: list[dict[str, str]],
    merged: dict[str, dict[str, Any]],
    descriptions: dict[str, Any],
    rejected: list[dict[str, str]],
) -> dict[str, Any]:
    doc_id = f"retrieval:column:{selected['table_name']}.{selected['column_name']}"
    row = merged.get(doc_id, {})
    rejected_refs = [candidate["column_ref"] for candidate in rejected]
    return {
        "user_phrase": phrase["text"],
        "role": phrase["role"],
        "entity_hint": phrase.get("entity_hint", ""),
        "selected_column": selected["column_ref"],
        "table_name": selected["table_name"],
        "column_name": selected["column_name"],
        "value": selected["value"],
        "operator": "=",
        "confidence": _binding_confidence(len(candidates), len(rejected), row),
        "confidence_score": round(float(row.get("rrf_score") or 0.0), 6),
        "search_scores": {
            "rrf_score": round(float(row.get("rrf_score") or 0.0), 6),
            "vector_rank": row.get("vector_rank"),
            "vector_score": _round_optional(row.get("vector_score")),
            "bm25_rank": row.get("bm25_rank"),
            "bm25_score": _round_optional(row.get("bm25_score")),
        },
        "candidate_columns": [candidate["column_ref"] for candidate in candidates],
        "rejected_confounders": rejected_refs,
        "reason": _selection_reason(phrase=phrase, selected=selected, descriptions=descriptions),
    }


def _binding_confidence(
    candidate_count: int,
    rejected_count: int,
    retrieval_row: dict[str, Any],
) -> str:
    if candidate_count == 1:
        return "high"
    if rejected_count and retrieval_row.get("vector_rank") is not None and retrieval_row.get("bm25_rank") is not None:
        return "high"
    if rejected_count:
        return "medium"
    return "low"


def _selection_reason(
    *,
    phrase: dict[str, Any],
    selected: dict[str, str],
    descriptions: dict[str, Any],
) -> str:
    table_desc = _table_description(descriptions=descriptions, table_name=selected["table_name"])
    column_desc = _column_description(
        descriptions=descriptions,
        table_name=selected["table_name"],
        column_name=selected["column_name"],
    )
    entity_hint = phrase.get("entity_hint") or phrase["text"]
    return (
        f"Selected because the phrase {phrase['text']!r} refers to {entity_hint!r}, "
        f"and {selected['column_ref']} is described as: "
        f"{column_desc or table_desc or 'a matching profiled value column'}"
    )


def _rejection_reason(
    *,
    phrase: dict[str, Any],
    selected: dict[str, str],
    rejected: dict[str, str],
    descriptions: dict[str, Any],
) -> str:
    selected_desc = _column_description(
        descriptions=descriptions,
        table_name=selected["table_name"],
        column_name=selected["column_name"],
    )
    rejected_desc = _column_description(
        descriptions=descriptions,
        table_name=rejected["table_name"],
        column_name=rejected["column_name"],
    )
    return (
        f"The phrase {phrase['text']!r} aligns better with {selected['column_ref']} "
        f"({selected_desc or 'selected column'}) than {rejected['column_ref']} "
        f"({rejected_desc or 'rejected column'})."
    )


def _table_description(*, descriptions: dict[str, Any], table_name: str) -> str:
    tables = descriptions.get("tables") if isinstance(descriptions, dict) else {}
    if not isinstance(tables, dict):
        return ""
    value = tables.get(table_name)
    if isinstance(value, dict):
        return " ".join(
            str(value.get(key, "")).strip()
            for key in ["short_description", "long_description"]
            if str(value.get(key, "")).strip()
        )
    return ""


def _column_description(
    *,
    descriptions: dict[str, Any],
    table_name: str,
    column_name: str,
) -> str:
    columns = descriptions.get("columns") if isinstance(descriptions, dict) else {}
    if not isinstance(columns, dict):
        return ""
    table_columns = columns.get(table_name)
    if not isinstance(table_columns, dict):
        return ""
    value = table_columns.get(column_name)
    if isinstance(value, dict):
        return str(value.get("short_description") or value.get("long_description") or "").strip()
    return ""


def _read_json_if_exists(store: ObjectStore, key: str) -> dict[str, Any]:
    if not store.exists(key):
        return {}
    payload = store.read_json(key)
    return payload if isinstance(payload, dict) else {}


def _read_jsonl_if_exists(store: ObjectStore, key: str) -> list[dict[str, Any]]:
    if not store.exists(key):
        return []
    rows = []
    for line in store.read_text(key).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _active_key(settings: DiracDataSettings, relative_path: str) -> str:
    return active_learning_artifact_key(settings, relative_path=relative_path)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _semantic_tokens(text: str) -> set[str]:
    tokens = set()
    for token in _tokens(text):
        if token in STOPWORDS:
            continue
        tokens.add(token)
        if token.endswith("s") and len(token) > 3:
            tokens.add(token[:-1])
    return tokens


def _normalized_role(value: str) -> str:
    role = re.sub(r"[^a-z_]+", "_", value.lower()).strip("_")
    return role if role in {"metric", "dimension", "filter", "time", "entity", "threshold"} else "unknown"


def _normalize_lookup(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _normalize_value_text(value: str) -> str:
    return f" {_normalize_lookup(value)} "


def _normalized_value_in_text(value: str, normalized_text: str) -> bool:
    normalized = _normalize_lookup(value)
    return bool(normalized) and f" {normalized} " in normalized_text


def _profile_value_is_actionable(value: str) -> bool:
    clean = value.strip()
    if len(clean) < 2:
        return False
    if clean.startswith("{{") and clean.endswith("}}"):
        return False
    return True


def _clean_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" ,.;:\n\t")).strip()


def _mentions_any(text: str, needles: set[str]) -> bool:
    return any(needle in text for needle in needles)


def _as_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _dedupe_query_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    deduped = []
    for row in rows:
        key = _normalize_lookup(row.get("query", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _dedupe_strings(values: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for value in values:
        clean = _clean_phrase(value)
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        deduped.append(clean)
    return deduped


def _dedupe_value_candidates(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    deduped = []
    for row in rows:
        key = (row["column_ref"], row["value"].lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _dedupe_bindings(bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_column_value: dict[tuple[object, str], dict[str, Any]] = {}
    for binding in bindings:
        key = (
            binding.get("selected_column"),
            str(binding.get("value", "")).lower(),
        )
        existing = by_column_value.get(key)
        if existing is not None and len(str(existing.get("user_phrase", ""))) >= len(
            str(binding.get("user_phrase", ""))
        ):
            continue
        by_column_value[key] = binding
    return list(by_column_value.values())


def _dedupe_rejected(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_column_value: dict[tuple[object, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            row.get("column_ref"),
            str(row.get("value", "")).lower(),
        )
        existing = by_column_value.get(key)
        if existing is not None and len(str(existing.get("user_phrase", ""))) >= len(
            str(row.get("user_phrase", ""))
        ):
            continue
        by_column_value[key] = row
    return list(by_column_value.values())


def _min_optional_rank(current: object, rank: int) -> int:
    if current is None:
        return rank
    return min(int(current), rank)


def _round_optional(value: object) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(clean[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    return str(content)
