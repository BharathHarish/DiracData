"""Build the first v2 schema graph document from metadata descriptions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from diracdata_v2.context import EdgeKind, GraphEdge, GraphNode, NodeKind, TrustLevel


class TextGenerator(Protocol):
    """Small protocol for an LLM client used by v2 learning."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


@dataclass(frozen=True)
class SchemaGraphBuildResult:
    document: dict[str, Any]
    local_path: Path
    object_key: str | None = None


class SchemaGraphBuilder:
    """Create a compact schema graph document.

    The LLM proposes domain/entity semantics. Code validates table/column coverage
    and builds the graph/indexes so the artifact stays complete and traversable.
    """

    def __init__(
        self,
        *,
        generator: TextGenerator,
        prompt: str,
        hierarchy_prompt: str | None = None,
        full_prompt_column_limit: int = 120,
    ) -> None:
        self._generator = generator
        self._prompt = prompt
        self._hierarchy_prompt = hierarchy_prompt or load_hierarchy_prompt()
        self._full_prompt_column_limit = full_prompt_column_limit

    def build(
        self,
        *,
        metadata_descriptions: dict[str, Any],
        catalog: str,
        database: str,
        schema: str,
        run_id: str,
        output_dir: Path,
        object_store: Any | None = None,
        object_prefix: str = "v2/learning/artifacts",
    ) -> SchemaGraphBuildResult:
        llm_payload = self._generate(metadata_descriptions)
        document = build_schema_graph_document(
            metadata_descriptions=metadata_descriptions,
            llm_payload=llm_payload,
            catalog=catalog,
            database=database,
            schema=schema,
            run_id=run_id,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        local_path = output_dir / "schema_graph.json"
        local_path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")

        object_key = None
        if object_store is not None:
            object_key = f"{object_prefix.strip('/')}/{run_id}/schema_graph.json"
            object_store.write_json(object_key, document)
        return SchemaGraphBuildResult(document=document, local_path=local_path, object_key=object_key)

    def _generate(self, metadata_descriptions: dict[str, Any]) -> dict[str, Any]:
        if _column_count(metadata_descriptions) > self._full_prompt_column_limit:
            return self._generate_hierarchy(metadata_descriptions)
        rendered = self._prompt.replace(
            "{{metadata_descriptions_json}}",
            json.dumps(metadata_descriptions, indent=2, sort_keys=True),
        )
        text = self._generator.complete(
            [
                {
                    "role": "system",
                    "content": "You produce compact, valid JSON schema graph documents.",
                },
                {"role": "user", "content": rendered},
            ]
        )
        return _loads_json_object(text)

    def _generate_hierarchy(self, metadata_descriptions: dict[str, Any]) -> dict[str, Any]:
        rendered = self._hierarchy_prompt.replace(
            "{{table_descriptions_json}}",
            json.dumps(_table_descriptions(metadata_descriptions), indent=2, sort_keys=True),
        )
        text = self._generator.complete(
            [
                {
                    "role": "system",
                    "content": "You produce compact, valid JSON schema hierarchy documents.",
                },
                {"role": "user", "content": rendered},
            ]
        )
        payload = _loads_json_object(text)
        payload.setdefault("columns", {})
        return payload


def build_schema_graph_document(
    *,
    metadata_descriptions: dict[str, Any],
    llm_payload: dict[str, Any],
    catalog: str,
    database: str,
    schema: str,
    run_id: str,
) -> dict[str, Any]:
    tables = _dict(metadata_descriptions.get("tables"))
    columns = _dict(metadata_descriptions.get("columns"))
    payload_domains = _list(llm_payload.get("domains"))
    payload_entities = _list(llm_payload.get("entities"))
    payload_tables = _dict(llm_payload.get("tables"))
    payload_columns = _dict(llm_payload.get("columns"))

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    root_id = f"schema:{catalog}.{database}.{schema}"
    nodes.append(
        GraphNode(
            id=root_id,
            kind=NodeKind.DOMAIN,
            name=f"{catalog}.{database}.{schema}",
            path=(catalog, database, schema),
            description="Scoped analytics schema.",
            metadata={"node_role": "schema_root"},
        )
    )

    domain_ids: set[str] = set()
    for domain in payload_domains:
        domain_id = _safe_id(str(domain.get("id") or f"domain:{domain.get('name', 'domain')}"), "domain")
        if domain_id in domain_ids:
            continue
        domain_ids.add(domain_id)
        nodes.append(
            GraphNode(
                id=domain_id,
                kind=NodeKind.DOMAIN,
                name=str(domain.get("name") or domain_id.removeprefix("domain:")),
                path=(catalog, database, schema, domain_id.removeprefix("domain:")),
                description=_one_line(domain.get("description")),
                aliases=tuple(_strings(domain.get("aliases"))),
            )
        )
        edges.append(_contains(root_id, domain_id))

    if not domain_ids:
        domain_id = "domain:analytics"
        domain_ids.add(domain_id)
        nodes.append(
            GraphNode(
                id=domain_id,
                kind=NodeKind.DOMAIN,
                name="Analytics",
                path=(catalog, database, schema, "analytics"),
                description="Analytics-ready tables and columns.",
            )
        )
        edges.append(_contains(root_id, domain_id))

    entity_ids: set[str] = set()
    entity_domain: dict[str, str] = {}
    fallback_domain = sorted(domain_ids)[0]
    for entity in payload_entities:
        entity_id = _safe_id(str(entity.get("id") or f"entity:{entity.get('name', 'entity')}"), "entity")
        if entity_id in entity_ids:
            continue
        domain_id = str(entity.get("domain_id") or fallback_domain)
        if domain_id not in domain_ids:
            domain_id = fallback_domain
        entity_ids.add(entity_id)
        entity_domain[entity_id] = domain_id
        nodes.append(
            GraphNode(
                id=entity_id,
                kind=NodeKind.ENTITY,
                name=str(entity.get("name") or entity_id.removeprefix("entity:")),
                path=_node_path(nodes, domain_id) + (entity_id.removeprefix("entity:"),),
                description=_one_line(entity.get("description")),
                aliases=tuple(_strings(entity.get("aliases"))),
            )
        )
        edges.append(_contains(domain_id, entity_id))

    if not entity_ids:
        entity_id = "entity:analytics"
        entity_ids.add(entity_id)
        entity_domain[entity_id] = fallback_domain
        nodes.append(
            GraphNode(
                id=entity_id,
                kind=NodeKind.ENTITY,
                name="Analytics",
                path=_node_path(nodes, fallback_domain) + ("analytics",),
                description="Analytics entity containing scoped schema tables.",
            )
        )
        edges.append(_contains(fallback_domain, entity_id))

    fallback_entity = sorted(entity_ids)[0]
    table_ids: dict[str, str] = {}
    for table_name, table_description in tables.items():
        proposed = _dict(payload_tables.get(table_name))
        entity_id = str(proposed.get("entity_id") or fallback_entity)
        if entity_id not in entity_ids:
            entity_id = fallback_entity
        table_id = f"table:{table_name}"
        table_ids[table_name] = table_id
        short_description = _one_line(proposed.get("description")) or _one_line(
            _dict(table_description).get("short_description")
        )
        nodes.append(
            GraphNode(
                id=table_id,
                kind=NodeKind.TABLE,
                name=table_name,
                path=_node_path(nodes, entity_id) + (table_name,),
                description=short_description,
                sql_ref=table_name,
                grain=_one_line(proposed.get("grain")) or None,
            )
        )
        edges.append(_contains(entity_id, table_id))

    for table_name, table_columns in columns.items():
        table_id = table_ids.get(table_name)
        if table_id is None:
            continue
        proposed_by_column = _dict(payload_columns.get(table_name))
        for column_name, column_description in _dict(table_columns).items():
            proposed = _dict(proposed_by_column.get(column_name))
            column_id = f"column:{table_name}.{column_name}"
            short_description = _one_line(proposed.get("description")) or _one_line(
                _dict(column_description).get("short_description")
            )
            nodes.append(
                GraphNode(
                    id=column_id,
                    kind=NodeKind.COLUMN,
                    name=column_name,
                    path=_node_path(nodes, table_id) + (column_name,),
                    description=short_description,
                    sql_ref=f"{table_name}.{column_name}",
                    aliases=tuple(_strings(proposed.get("aliases"))),
                    sql_guidance=_one_line(proposed.get("sql_guidance")) or None,
                    metadata={"role": str(proposed.get("role") or "unknown")},
                )
            )
            edges.append(_contains(table_id, column_id))

    node_payloads = [node.to_dict() for node in nodes]
    edge_payloads = [edge.to_dict() for edge in edges]
    return {
        "version": 1,
        "artifact_type": "schema_graph",
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "scope": {"catalog": catalog, "database": database, "schema": schema},
        "nodes": node_payloads,
        "edges": edge_payloads,
        "indexes": _indexes(node_payloads, edge_payloads),
    }


def load_prompt(path: Path | None = None) -> str:
    prompt_path = path or Path(__file__).with_name("prompts") / "schema_graph_document.md"
    return prompt_path.read_text(encoding="utf-8")


def load_hierarchy_prompt(path: Path | None = None) -> str:
    prompt_path = path or Path(__file__).with_name("prompts") / "schema_graph_hierarchy.md"
    return prompt_path.read_text(encoding="utf-8")


def _indexes(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, list[str]] = {}
    children_by_node: dict[str, list[str]] = {}
    columns_by_table: dict[str, list[str]] = {}
    for node in nodes:
        node_id = str(node["id"])
        by_kind.setdefault(str(node["kind"]), []).append(node_id)
    for edge in edges:
        if edge.get("kind") != EdgeKind.CONTAINS.value:
            continue
        parent = str(edge["from_node"])
        child = str(edge["to_node"])
        children_by_node.setdefault(parent, []).append(child)
        if parent.startswith("table:") and child.startswith("column:"):
            columns_by_table.setdefault(parent, []).append(child)
    return {
        "by_kind": {key: sorted(value) for key, value in by_kind.items()},
        "children_by_node": {key: sorted(value) for key, value in children_by_node.items()},
        "columns_by_table": {key: sorted(value) for key, value in columns_by_table.items()},
    }


def _column_count(metadata_descriptions: dict[str, Any]) -> int:
    return sum(len(_dict(table_columns)) for table_columns in _dict(metadata_descriptions.get("columns")).values())


def _table_descriptions(metadata_descriptions: dict[str, Any]) -> dict[str, Any]:
    return {"tables": _dict(metadata_descriptions.get("tables"))}


def _contains(parent: str, child: str) -> GraphEdge:
    return GraphEdge(
        id=f"contains:{parent}:{child}",
        kind=EdgeKind.CONTAINS,
        from_node=parent,
        to_node=child,
        source=TrustLevel.USER_PROVIDED,
        confidence="high",
    )


def _node_path(nodes: list[GraphNode], node_id: str) -> tuple[str, ...]:
    for node in nodes:
        if node.id == node_id:
            return node.path
    return ()


def _loads_json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match is None:
            raise
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("schema graph LLM response must be a JSON object")
    return value


def _safe_id(raw: str, prefix: str) -> str:
    value = raw.strip()
    if value.startswith(f"{prefix}:"):
        suffix = value.split(":", 1)[1]
    else:
        suffix = value
    suffix = re.sub(r"[^a-zA-Z0-9_]+", "_", suffix).strip("_").lower() or prefix
    return f"{prefix}:{suffix}"


def _one_line(value: Any) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return text[:1].upper() + text[1:] if text else ""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
