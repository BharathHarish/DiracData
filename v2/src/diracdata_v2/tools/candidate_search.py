"""Hybrid table/column candidate search tools."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from diracdata_v2.tools.hybrid import (
    DEFAULT_EMBEDDING_MODEL,
    RetrievalDocument,
    hybrid_search,
    load_jsonl_documents,
    load_vector_rows,
)


class CandidateSearchInput(BaseModel):
    query: str = Field(description="User question or focused entity phrase.")
    search_terms: list[str] | None = Field(
        default=None,
        description="Optional entity/synonym searches to run alongside the full query.",
    )
    top_k: int = Field(default=12, ge=1, le=30)


@dataclass
class CandidateSearchService:
    documents: list[RetrievalDocument]
    vector_rows: list[dict[str, Any]]
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    local_files_only: bool = True

    @classmethod
    def from_files(
        cls,
        *,
        schema_ast_path: Path | None = None,
        metadata_descriptions_path: Path | None = None,
        retrieval_documents_path: Path | None = None,
        column_embeddings_path: Path | None = None,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        local_files_only: bool = True,
    ) -> "CandidateSearchService":
        documents: list[RetrievalDocument] = []
        if retrieval_documents_path is not None and retrieval_documents_path.exists():
            documents.extend(load_jsonl_documents(retrieval_documents_path))
        if not documents and schema_ast_path is not None and schema_ast_path.exists():
            documents.extend(_documents_from_schema_ast(schema_ast_path))
        if not documents and metadata_descriptions_path is not None and metadata_descriptions_path.exists():
            documents.extend(_documents_from_metadata(metadata_descriptions_path))
        vector_rows = (
            load_vector_rows(column_embeddings_path)
            if column_embeddings_path is not None and column_embeddings_path.exists()
            else []
        )
        return cls(
            documents=documents,
            vector_rows=vector_rows,
            embedding_model=embedding_model,
            local_files_only=local_files_only,
        )

    def search(
        self,
        *,
        query: str,
        search_terms: list[str] | None = None,
        top_k: int = 12,
    ) -> dict[str, Any]:
        if not self.documents:
            return {"status": "missing_artifacts", "query": query, "candidate_columns": []}
        result = hybrid_search(
            documents=self.documents,
            query=query,
            search_terms=search_terms,
            top_k=top_k,
            vector_rows=self.vector_rows,
            embedding_model=self.embedding_model,
            local_files_only=self.local_files_only,
        )
        return {
            "status": "ok",
            "query": query,
            "search_queries": result["search_queries"],
            "candidate_columns": [
                _compact_candidate_hit(hit) for hit in result["hits"] if hit.get("column_name")
            ],
            "candidate_tables": [
                _compact_candidate_hit(hit) for hit in result["hits"] if not hit.get("column_name")
            ],
            "candidate_groups": self._candidate_groups(
                search_queries=result["search_queries"],
                top_k=min(5, max(3, top_k // 3)),
            ),
            "retrieval": result["retrieval"],
            "guidance": (
                "Use these as candidates, not proof. Resolve conflicts with schema tools "
                "and verify exact predicate values before SQL authoring."
            ),
        }

    def _candidate_groups(
        self,
        *,
        search_queries: list[str],
        top_k: int,
    ) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        for search_query in search_queries[:6]:
            result = hybrid_search(
                documents=self.documents,
                query=search_query,
                search_terms=None,
                top_k=top_k,
                vector_rows=self.vector_rows,
                embedding_model=self.embedding_model,
                local_files_only=self.local_files_only,
            )
            groups.append(
                {
                    "query": search_query,
                    "columns": [
                        _compact_candidate_hit(hit)
                        for hit in result["hits"]
                        if hit.get("column_name")
                    ],
                    "tables": [
                        _compact_candidate_hit(hit)
                        for hit in result["hits"]
                        if not hit.get("column_name")
                    ],
                }
            )
        return groups


def build_candidate_search_tool(service: CandidateSearchService) -> object:
    from langchain.tools import tool

    @tool("candidate_search_tool", args_schema=CandidateSearchInput)
    def candidate_search_tool(
        query: str,
        search_terms: list[str] | None = None,
        top_k: int = 12,
    ) -> dict[str, Any]:
        """Hybrid BM25+/optional-vector search for compact schema table and column candidates."""
        return service.search(query=query, search_terms=search_terms, top_k=top_k)

    return candidate_search_tool


def _documents_from_metadata(path: Path) -> list[RetrievalDocument]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    documents: list[RetrievalDocument] = []
    for table, table_desc in sorted(payload.get("tables", {}).items()):
        text = _description_text(table_desc)
        documents.append(
            RetrievalDocument(
                id=f"table:{table}",
                text=f"{table}\n{text}",
                source_type="table",
                table_name=table,
                metadata={"table": table},
            )
        )
    for table, columns in sorted(payload.get("columns", {}).items()):
        for column, column_desc in sorted(columns.items()):
            text = _description_text(column_desc)
            documents.append(
                RetrievalDocument(
                    id=f"column:{table}.{column}",
                    text=f"{table}.{column}\n{text}",
                    source_type="column",
                    table_name=table,
                    column_name=column,
                    metadata={"table": table, "column": column},
                )
            )
    return documents


def _documents_from_schema_ast(path: Path) -> list[RetrievalDocument]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    documents: list[RetrievalDocument] = []
    for domain in payload.get("domains", []):
        for entity in domain.get("entities", []):
            for table in entity.get("tables", []):
                table_name = str(table.get("name") or "")
                table_text = " ".join(
                    [
                        table_name,
                        str(table.get("description") or ""),
                        str(table.get("grain") or ""),
                        str(domain.get("description") or ""),
                        str(entity.get("description") or ""),
                    ]
                )
                documents.append(
                    RetrievalDocument(
                        id=str(table.get("id") or f"table:{table_name}"),
                        text=table_text,
                        source_type="table",
                        table_name=table_name,
                        metadata=table,
                    )
                )
                for column in table.get("columns", []):
                    sql_ref = str(column.get("sql_ref") or "")
                    column_name = sql_ref.split(".", 1)[1] if "." in sql_ref else str(column.get("name") or "")
                    column_text = " ".join(
                        [
                            sql_ref,
                            str(column.get("description") or ""),
                            " ".join(map(str, column.get("aliases", []))),
                            str(column.get("role") or ""),
                            str(column.get("sql_guidance") or ""),
                            table_text,
                        ]
                    )
                    documents.append(
                        RetrievalDocument(
                            id=str(column.get("id") or f"column:{sql_ref}"),
                            text=column_text,
                            source_type="column",
                            table_name=table_name,
                            column_name=column_name,
                            metadata=column,
                        )
                    )
    return documents


def _description_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(
            str(value.get(key) or "")
            for key in ("short_description", "long_description", "description")
        )
    return str(value or "")


def _compact_candidate_hit(hit: dict[str, Any]) -> dict[str, Any]:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    table_name = hit.get("table_name")
    column_name = hit.get("column_name")
    output: dict[str, Any] = {
        "id": hit.get("id"),
        "score": hit.get("score"),
        "source_type": hit.get("source_type"),
        "table_name": table_name,
        "column_name": column_name,
        "description_snippet": _snippet(str(hit.get("text") or "")),
    }
    aliases = metadata.get("aliases")
    if aliases:
        output["aliases"] = aliases[:8] if isinstance(aliases, list) else aliases
    role = metadata.get("role")
    if role:
        output["role"] = role
    sql_guidance = metadata.get("sql_guidance")
    if sql_guidance:
        output["sql_guidance"] = _snippet(str(sql_guidance), max_chars=220)
    sample_values = metadata.get("sample_values") or metadata.get("distinct_values")
    if isinstance(sample_values, list):
        output["sample_values"] = sample_values[:12]
    return output


def _snippet(text: str, *, max_chars: int = 320) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."
