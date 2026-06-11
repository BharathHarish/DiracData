"""Compile a traversal-first schema AST from the schema graph and SQL library."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SchemaASTBuildResult:
    document: dict[str, Any]
    local_path: Path
    object_key: str | None = None


class SchemaASTBuilder:
    """Build a compact hierarchy for agent/tool traversal."""

    def build(
        self,
        *,
        schema_graph: dict[str, Any],
        sql_library: dict[str, Any],
        catalog: str,
        database: str,
        schema: str,
        run_id: str,
        output_dir: Path,
        object_store: Any | None = None,
        object_prefix: str = "v2/learning/artifacts",
    ) -> SchemaASTBuildResult:
        document = build_schema_ast_document(
            schema_graph=schema_graph,
            sql_library=sql_library,
            catalog=catalog,
            database=database,
            schema=schema,
            run_id=run_id,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        local_path = output_dir / "schema_ast.json"
        local_path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")

        object_key = None
        if object_store is not None:
            object_key = f"{object_prefix.strip('/')}/{run_id}/schema_ast.json"
            object_store.write_json(object_key, document)
        return SchemaASTBuildResult(document=document, local_path=local_path, object_key=object_key)


def build_schema_ast_document(
    *,
    schema_graph: dict[str, Any],
    sql_library: dict[str, Any],
    catalog: str,
    database: str,
    schema: str,
    run_id: str,
) -> dict[str, Any]:
    nodes = {str(node["id"]): node for node in schema_graph.get("nodes", [])}
    children = _children_by_node(schema_graph)
    root_id = _schema_root_id(schema_graph, catalog=catalog, database=database, schema=schema)
    library_links = _library_links(sql_library=sql_library, nodes=nodes)

    domains = [
        _build_domain_ast(
            domain_id=domain_id,
            nodes=nodes,
            children=children,
            library_links=library_links,
        )
        for domain_id in children.get(root_id, [])
        if nodes.get(domain_id, {}).get("kind") == "domain"
    ]
    domains = [domain for domain in domains if domain is not None]

    return {
        "version": 1,
        "artifact_type": "schema_ast",
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "scope": {"catalog": catalog, "database": database, "schema": schema},
        "source_artifacts": {
            "schema_graph_run_id": schema_graph.get("run_id"),
            "sql_library_run_id": sql_library.get("run_id"),
        },
        "domains": domains,
        "indexes": _ast_indexes(domains),
    }


def _build_domain_ast(
    *,
    domain_id: str,
    nodes: dict[str, dict[str, Any]],
    children: dict[str, list[str]],
    library_links: dict[str, list[str]],
) -> dict[str, Any] | None:
    domain = nodes.get(domain_id)
    if domain is None:
        return None
    entities = [
        _build_entity_ast(
            entity_id=entity_id,
            nodes=nodes,
            children=children,
            library_links=library_links,
        )
        for entity_id in children.get(domain_id, [])
        if nodes.get(entity_id, {}).get("kind") == "entity"
    ]
    entities = [entity for entity in entities if entity is not None]
    return {
        **_node_summary(domain, library_links),
        "entities": entities,
    }


def _build_entity_ast(
    *,
    entity_id: str,
    nodes: dict[str, dict[str, Any]],
    children: dict[str, list[str]],
    library_links: dict[str, list[str]],
) -> dict[str, Any] | None:
    entity = nodes.get(entity_id)
    if entity is None:
        return None
    tables = [
        _build_table_ast(
            table_id=table_id,
            nodes=nodes,
            children=children,
            library_links=library_links,
        )
        for table_id in children.get(entity_id, [])
        if nodes.get(table_id, {}).get("kind") == "table"
    ]
    tables = [table for table in tables if table is not None]
    return {
        **_node_summary(entity, library_links),
        "tables": tables,
    }


def _build_table_ast(
    *,
    table_id: str,
    nodes: dict[str, dict[str, Any]],
    children: dict[str, list[str]],
    library_links: dict[str, list[str]],
) -> dict[str, Any] | None:
    table = nodes.get(table_id)
    if table is None:
        return None
    columns = [
        _build_column_ast(column_id=column_id, nodes=nodes, library_links=library_links)
        for column_id in children.get(table_id, [])
        if nodes.get(column_id, {}).get("kind") == "column"
    ]
    columns = [column for column in columns if column is not None]
    return {
        **_node_summary(table, library_links),
        "grain": table.get("grain"),
        "columns": columns,
    }


def _build_column_ast(
    *,
    column_id: str,
    nodes: dict[str, dict[str, Any]],
    library_links: dict[str, list[str]],
) -> dict[str, Any] | None:
    column = nodes.get(column_id)
    if column is None:
        return None
    return {
        **_node_summary(column, library_links),
        "role": column.get("metadata", {}).get("role", "unknown"),
        "sql_ref": column.get("sql_ref"),
        "aliases": column.get("aliases", []),
        "null_meaning": column.get("null_meaning"),
        "sql_guidance": column.get("sql_guidance"),
    }


def _node_summary(node: dict[str, Any], library_links: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "id": node["id"],
        "kind": node["kind"],
        "name": node["name"],
        "description": node.get("description", ""),
        "path": node.get("path", []),
        "sql_library_ids": library_links.get(node["id"], []),
    }


def _children_by_node(schema_graph: dict[str, Any]) -> dict[str, list[str]]:
    from_indexes = schema_graph.get("indexes", {}).get("children_by_node")
    if isinstance(from_indexes, dict):
        return {str(key): sorted(map(str, value)) for key, value in from_indexes.items()}
    children: dict[str, list[str]] = {}
    for edge in schema_graph.get("edges", []):
        if edge.get("kind") != "contains":
            continue
        children.setdefault(str(edge["from_node"]), []).append(str(edge["to_node"]))
    return {key: sorted(value) for key, value in children.items()}


def _schema_root_id(
    schema_graph: dict[str, Any],
    *,
    catalog: str,
    database: str,
    schema: str,
) -> str:
    expected = f"schema:{catalog}.{database}.{schema}"
    for node in schema_graph.get("nodes", []):
        if node.get("id") == expected:
            return expected
    for node in schema_graph.get("nodes", []):
        if node.get("metadata", {}).get("node_role") == "schema_root":
            return str(node["id"])
    return expected


def _library_links(
    *,
    sql_library: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    links: dict[str, set[str]] = {}
    sql_ref_to_node = {
        str(node.get("sql_ref")): node_id
        for node_id, node in nodes.items()
        if node.get("sql_ref")
    }
    table_to_node = {
        str(node.get("sql_ref")): node_id
        for node_id, node in nodes.items()
        if node.get("kind") == "table" and node.get("sql_ref")
    }
    parent_by_child = _parent_index_from_paths(nodes)
    for entry_id, entry in sql_library.get("entries", {}).items():
        touched_nodes: set[str] = set()
        for table in entry.get("tables", []):
            node_id = table_to_node.get(str(table))
            if node_id:
                touched_nodes.add(node_id)
        for column_ref in entry.get("columns", []):
            node_id = sql_ref_to_node.get(str(column_ref))
            if node_id:
                touched_nodes.add(node_id)
        for node_id in list(touched_nodes):
            current = node_id
            while current:
                links.setdefault(current, set()).add(str(entry_id))
                current = parent_by_child.get(current)
    return {key: sorted(value) for key, value in links.items()}


def _parent_index_from_paths(nodes: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Infer parent links from node paths when edge indexes are unavailable."""
    by_path = {tuple(node.get("path", [])): node_id for node_id, node in nodes.items()}
    parents: dict[str, str] = {}
    for node_id, node in nodes.items():
        path = tuple(node.get("path", []))
        if len(path) <= 1:
            continue
        parent_id = by_path.get(path[:-1])
        if parent_id:
            parents[node_id] = parent_id
    return parents


def _ast_indexes(domains: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, list[str]] = {}
    path_by_id: dict[str, list[str]] = {}
    library_ids_by_node: dict[str, list[str]] = {}

    def visit(node: dict[str, Any]) -> None:
        node_id = str(node["id"])
        by_kind.setdefault(str(node["kind"]), []).append(node_id)
        path_by_id[node_id] = list(node.get("path", []))
        if node.get("sql_library_ids"):
            library_ids_by_node[node_id] = list(node["sql_library_ids"])
        for child_group in ("entities", "tables", "columns"):
            for child in node.get(child_group, []):
                visit(child)

    for domain in domains:
        visit(domain)
    return {
        "by_kind": {key: sorted(value) for key, value in by_kind.items()},
        "path_by_id": path_by_id,
        "sql_library_ids_by_node": library_ids_by_node,
    }

