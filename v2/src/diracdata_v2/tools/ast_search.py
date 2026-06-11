"""Schema AST search tool for the lean v2 agent."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class SchemaSearchInput(BaseModel):
    query: str = Field(description="Natural-language question or focused search phrase.")
    max_columns: int = Field(default=6, description="Maximum matched columns to return.")
    max_sql_library_entries: int = Field(default=3, description="Maximum SQL library snippets to return.")


@dataclass
class ASTSearchService:
    schema_ast: dict[str, Any]
    sql_library: dict[str, Any]

    @classmethod
    def from_files(cls, *, schema_ast_path: Path, sql_library_path: Path) -> "ASTSearchService":
        return cls(
            schema_ast=json.loads(schema_ast_path.read_text(encoding="utf-8")),
            sql_library=json.loads(sql_library_path.read_text(encoding="utf-8")),
        )

    def search(
        self,
        *,
        query: str,
        max_columns: int = 6,
        max_sql_library_entries: int = 3,
    ) -> dict[str, Any]:
        terms = _terms(query)
        columns = _rank_columns(self.schema_ast, terms)[: max(1, max_columns)]
        column_ids = {item["id"] for item in columns}
        domains, tables = _ancestors_for_columns(self.schema_ast, column_ids)
        library_ids = _rank_library_ids(
            sql_library=self.sql_library,
            query_terms=terms,
            linked_ids={lib_id for item in columns for lib_id in item.get("sql_library_ids", [])},
            limit=max(1, max_sql_library_entries),
        )
        return {
            "status": "ok",
            "query": query,
            "matched_domains": domains,
            "matched_tables": tables,
            "matched_columns": columns,
            "sql_library": [_library_summary(lib_id, self.sql_library) for lib_id in library_ids],
            "guidance": (
                "Use only table and column names returned here unless you call schema_search_ast again. "
                "Use SQL library snippets as patterns, not as mandatory full queries."
            ),
        }


def build_schema_search_ast_tool(service: ASTSearchService) -> object:
    from langchain.tools import tool

    @tool("schema_search_ast", args_schema=SchemaSearchInput)
    def schema_search_ast(
        query: str,
        max_columns: int = 12,
        max_sql_library_entries: int = 6,
    ) -> dict[str, Any]:
        """Search the schema AST and SQL library for SQL-authoring context."""
        return service.search(
            query=query,
            max_columns=max_columns,
            max_sql_library_entries=max_sql_library_entries,
        )

    return schema_search_ast


def _rank_columns(schema_ast: dict[str, Any], terms: set[str]) -> list[dict[str, Any]]:
    ranked: list[tuple[int, dict[str, Any]]] = []
    for domain in schema_ast.get("domains", []):
        for entity in domain.get("entities", []):
            for table in entity.get("tables", []):
                for column in table.get("columns", []):
                    score = _column_score(domain, entity, table, column, terms)
                    if score:
                        ranked.append((score, _column_payload(domain, entity, table, column)))
    ranked.sort(key=lambda item: (-item[0], item[1]["sql_ref"]))
    return [item for _, item in ranked]


def _column_score(
    domain: dict[str, Any],
    entity: dict[str, Any],
    table: dict[str, Any],
    column: dict[str, Any],
    terms: set[str],
) -> int:
    name_terms = _terms(str(column.get("name", "")))
    alias_terms = _terms(" ".join(map(str, column.get("aliases", []))))
    guidance_terms = _terms(str(column.get("sql_guidance", "")))
    column_terms = _node_text(column)
    table_terms = _node_text(table)
    ancestor_terms = _node_text(domain, entity)
    score = 0
    for term in terms:
        if term in name_terms:
            score += 8
        if term in alias_terms:
            score += 6
        if term in guidance_terms:
            score += 4
        if term in column_terms:
            score += 3
        if term in table_terms:
            score += 1
        if term in ancestor_terms:
            score += 1
    return score


def _ancestors_for_columns(
    schema_ast: dict[str, Any],
    column_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    domains: dict[str, dict[str, Any]] = {}
    tables: dict[str, dict[str, Any]] = {}
    for domain in schema_ast.get("domains", []):
        for entity in domain.get("entities", []):
            for table in entity.get("tables", []):
                if any(column.get("id") in column_ids for column in table.get("columns", [])):
                    domains[domain["id"]] = _summary(domain)
                    tables[table["id"]] = {
                        **_summary(table),
                        "grain": table.get("grain"),
                        "domain_id": domain["id"],
                        "entity_id": entity["id"],
                    }
    return list(domains.values()), list(tables.values())


def _rank_library_ids(
    *,
    sql_library: dict[str, Any],
    query_terms: set[str],
    linked_ids: set[str],
    limit: int,
) -> list[str]:
    scored: list[tuple[int, str]] = []
    for lib_id, entry in sql_library.get("entries", {}).items():
        text = " ".join(
            [
                str(lib_id),
                str(entry.get("template", "")),
                " ".join(map(str, entry.get("tables", []))),
                " ".join(map(str, entry.get("columns", []))),
            ]
        ).lower()
        score = (5 if lib_id in linked_ids else 0) + sum(1 for term in query_terms if term in text)
        if score:
            scored.append((score, str(lib_id)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [lib_id for _, lib_id in scored[:limit]]


def _library_summary(lib_id: str, sql_library: dict[str, Any]) -> dict[str, Any]:
    entry = sql_library.get("entries", {}).get(lib_id, {})
    sql = str(entry.get("sql") or "")
    return {
        "id": lib_id,
        "template": entry.get("template"),
        "source": entry.get("source"),
        "review_status": entry.get("review_status"),
        "tables": entry.get("tables", []),
        "columns": entry.get("columns", []),
        "sql_excerpt": sql[:700],
    }


def _column_payload(
    domain: dict[str, Any],
    entity: dict[str, Any],
    table: dict[str, Any],
    column: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": column["id"],
        "sql_ref": column.get("sql_ref"),
        "role": column.get("role"),
        "description": column.get("description"),
        "aliases": column.get("aliases", []),
        "sql_guidance": column.get("sql_guidance"),
        "domain_id": domain["id"],
        "entity_id": entity["id"],
        "table_id": table["id"],
        "table_grain": table.get("grain"),
        "sql_library_ids": column.get("sql_library_ids", [])[:5],
    }


def _summary(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node["id"],
        "name": node["name"],
        "description": node.get("description", ""),
    }


def _node_text(*nodes: dict[str, Any]) -> set[str]:
    text = " ".join(
        " ".join(
            [
                str(node.get("id", "")),
                str(node.get("name", "")),
                str(node.get("description", "")),
                str(node.get("sql_ref", "")),
                " ".join(map(str, node.get("aliases", []))),
                str(node.get("sql_guidance", "")),
            ]
        )
        for node in nodes
    )
    return _terms(text)


def _terms(text: str) -> set[str]:
    stop_terms = {
        "all",
        "and",
        "are",
        "for",
        "from",
        "how",
        "many",
        "the",
        "there",
        "with",
    }
    return {
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_]+", text.lower().replace("-", "_"))
        if len(token) > 1 and token not in stop_terms
    }
