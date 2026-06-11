"""SQL pattern search tool backed by learned query-history patterns."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from diracdata_v2.tools.hybrid import RetrievalDocument, hybrid_search


class PatternSearchInput(BaseModel):
    query: str = Field(description="User question or focused analytics intent.")
    search_terms: list[str] | None = Field(
        default=None,
        description="Optional entity/synonym searches to run alongside the full query.",
    )
    top_k: int = Field(default=5, ge=1, le=15)


@dataclass
class SQLPatternSearchService:
    sql_library: dict[str, Any]
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    local_files_only: bool = True
    enable_vector: bool = False

    @classmethod
    def from_file(cls, path: Path) -> "SQLPatternSearchService":
        return cls(sql_library=json.loads(path.read_text(encoding="utf-8")) if path.exists() else {})

    def search(
        self,
        *,
        query: str,
        search_terms: list[str] | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        documents = _pattern_documents(self.sql_library)
        if not documents:
            return {"status": "missing_artifacts", "query": query, "patterns": []}
        result = hybrid_search(
            documents=documents,
            query=query,
            search_terms=search_terms,
            top_k=top_k,
            vector_rows=None,
            embedding_model=self.embedding_model,
            local_files_only=self.local_files_only,
        )
        entries = self.sql_library.get("entries", {})
        patterns = []
        for hit in result["hits"]:
            pattern = dict(hit.get("metadata", {}))
            entry_id = pattern.get("entry_id")
            entry = entries.get(entry_id, {}) if isinstance(entry_id, str) else {}
            sql_template = str(pattern.get("sql_template") or entry.get("sql", ""))
            patterns.append(
                {
                    "id": pattern.get("id"),
                    "score": hit.get("score"),
                    "canonical_question": pattern.get("canonical_question"),
                    "paraphrases": pattern.get("paraphrases", [])[:3],
                    "intent_signature": pattern.get("intent_signature", {}),
                    "tables": pattern.get("tables", []),
                    "columns": pattern.get("columns", []),
                    "sql_template": _truncate(sql_template, max_chars=1800),
                    "template_is_truncated": len(sql_template) > 1800,
                    "review_status": pattern.get("review_status"),
                    "source_entry_ids": pattern.get("source_entry_ids", []),
                }
            )
        return {
            "status": "ok",
            "query": query,
            "search_queries": result["search_queries"],
            "patterns": patterns,
            "retrieval": result["retrieval"],
            "guidance": (
                "Use SQL patterns as close prior examples. Adapt parameters and verify "
                "grain, filters, joins, and measures against the current user intent."
            ),
        }


def build_pattern_search_tool(service: SQLPatternSearchService) -> object:
    from langchain.tools import tool

    @tool("pattern_search_tool", args_schema=PatternSearchInput)
    def pattern_search_tool(
        query: str,
        search_terms: list[str] | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Search compact learned NL-SQL patterns mined from query history."""
        return service.search(query=query, search_terms=search_terms, top_k=top_k)

    return pattern_search_tool


def _pattern_documents(sql_library: dict[str, Any]) -> list[RetrievalDocument]:
    documents = []
    for pattern_id, pattern in sorted(sql_library.get("patterns", {}).items()):
        text = _pattern_text(pattern)
        if not text:
            continue
        documents.append(
            RetrievalDocument(
                id=str(pattern_id),
                text=text,
                source_type="sql_pattern",
                metadata={**pattern, "id": pattern_id},
            )
        )
    return documents


def _pattern_text(pattern: dict[str, Any]) -> str:
    signature = pattern.get("intent_signature", {})
    parts = [
        str(pattern.get("canonical_question") or ""),
        " ".join(map(str, pattern.get("paraphrases", []))),
        str(pattern.get("summary") or ""),
        " ".join(map(str, pattern.get("tables", []))),
        " ".join(map(str, pattern.get("columns", []))),
    ]
    if isinstance(signature, dict):
        parts.extend(
            [
                str(signature.get("grain") or ""),
                str(signature.get("measure") or ""),
                " ".join(map(str, signature.get("filters", []))),
                " ".join(map(str, signature.get("dimensions", []))),
                str(signature.get("time_window") or ""),
            ]
        )
    return " ".join(part for part in parts if part).strip()


def _truncate(text: str, *, max_chars: int) -> str:
    compact = text.strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."
