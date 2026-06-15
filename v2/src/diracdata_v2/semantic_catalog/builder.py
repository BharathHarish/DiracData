"""Build an agent-facing semantic catalog from learned schema artifacts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from diracdata_v2.semantic_catalog.contracts import (
    CatalogCard,
    CatalogCardKind,
    CatalogJoinEdge,
    CatalogReviewStatus,
    CatalogSource,
)
from diracdata_v2.semantic_catalog.sql_analysis import analyze_sql_references


class CatalogTextGenerator(Protocol):
    """Optional LLM adapter for future agentic catalog enrichment."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


@dataclass(frozen=True)
class SemanticCatalogBuildResult:
    document: dict[str, Any]
    local_path: Path
    object_key: str | None = None


class SemanticCatalogBuilder:
    """Create the compact semantic catalog consumed by runtime compilers.

    The first version is deterministic and agent-ready. It stores lossless
    schema cards, compact SQL pattern cards, observed join edges, and indexes.
    A generator can be supplied later to enrich business terms, but all
    referenced SQL objects are still validated by code.
    """

    def __init__(self, *, generator: CatalogTextGenerator | None = None) -> None:
        self._generator = generator

    def build(
        self,
        *,
        metadata_descriptions: dict[str, Any],
        schema_ast: dict[str, Any] | None = None,
        sql_library: dict[str, Any],
        catalog: str,
        database: str,
        schema: str,
        run_id: str,
        output_dir: Path,
        object_store: Any | None = None,
        object_prefix: str = "v2/learning/artifacts",
    ) -> SemanticCatalogBuildResult:
        document = build_semantic_catalog_document(
            metadata_descriptions=metadata_descriptions,
            schema_ast=schema_ast,
            sql_library=sql_library,
            catalog=catalog,
            database=database,
            schema=schema,
            run_id=run_id,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        local_path = output_dir / "semantic_catalog.json"
        local_path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")

        object_key = None
        if object_store is not None:
            object_key = f"{object_prefix.strip('/')}/{run_id}/semantic_catalog.json"
            object_store.write_json(object_key, document)
        return SemanticCatalogBuildResult(document=document, local_path=local_path, object_key=object_key)


def build_semantic_catalog_document(
    *,
    metadata_descriptions: dict[str, Any],
    schema_ast: dict[str, Any] | None = None,
    sql_library: dict[str, Any],
    catalog: str,
    database: str,
    schema: str,
    run_id: str,
) -> dict[str, Any]:
    table_columns = _table_columns(metadata_descriptions)
    cards = _schema_cards(
        schema_ast=schema_ast,
        metadata_descriptions=metadata_descriptions,
        schema=schema,
    )
    cards.extend(_sql_pattern_cards(sql_library))
    cards.extend(_metric_cards(sql_library))
    cards = _dedupe_cards(cards)
    join_edges = _join_edges(sql_library=sql_library, table_columns=table_columns)
    validation = _validate(cards=cards, join_edges=join_edges, table_columns=table_columns)

    document = {
        "version": 1,
        "artifact_type": "semantic_catalog",
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "scope": {"catalog": catalog, "database": database, "schema": schema},
        "source_artifacts": {
            "schema_ast_run_id": _dict(schema_ast).get("run_id"),
            "sql_library_run_id": sql_library.get("run_id"),
        },
        "cards": {card.id: card.to_dict() for card in cards},
        "join_edges": {edge.id: edge.to_dict() for edge in join_edges},
        "indexes": _indexes(cards=cards, join_edges=join_edges),
        "validation": validation,
    }
    return document


def _schema_cards(
    *,
    schema_ast: dict[str, Any] | None,
    metadata_descriptions: dict[str, Any],
    schema: str,
) -> list[CatalogCard]:
    if not _dict(schema_ast).get("domains"):
        return _schema_cards_from_metadata(metadata_descriptions=metadata_descriptions, schema=schema)

    cards: list[CatalogCard] = []
    table_descriptions = _dict(metadata_descriptions.get("tables"))
    column_descriptions = _dict(metadata_descriptions.get("columns"))
    for domain in _dict(schema_ast).get("domains", []):
        cards.append(_card_from_ast_node(domain, CatalogCardKind.DOMAIN, parent_ids=()))
        for entity in domain.get("entities", []):
            cards.append(_card_from_ast_node(entity, CatalogCardKind.ENTITY, parent_ids=(domain["id"],)))
            for table in entity.get("tables", []):
                table_name = str(table.get("name") or "")
                table_meta = _dict(table_descriptions.get(table_name))
                table_desc = _description(table_meta) or str(table.get("description") or "")
                cards.append(
                    _card_from_ast_node(
                        {**table, "description": table_desc},
                        CatalogCardKind.TABLE,
                        parent_ids=(domain["id"], entity["id"]),
                        metadata={"grain": table.get("grain")},
                    )
                )
                for column in table.get("columns", []):
                    sql_ref = str(column.get("sql_ref") or "")
                    column_name = sql_ref.split(".", 1)[1] if "." in sql_ref else str(column.get("name") or "")
                    column_meta = _dict(_dict(column_descriptions.get(table_name)).get(column_name))
                    column_desc = _description(column_meta) or str(column.get("description") or "")
                    role = _one_line(column.get("role"))
                    cards.append(
                        _card_from_ast_node(
                            {
                                **column,
                                "description": column_desc,
                                "role": role or column.get("role"),
                            },
                            CatalogCardKind.COLUMN,
                            parent_ids=(domain["id"], entity["id"], table["id"]),
                            metadata={
                                "table_name": table_name,
                                "column_name": column_name,
                                **({"role": role} if role else {}),
                                "table_grain": table.get("grain"),
                                "null_meaning": column.get("null_meaning"),
                                "sql_guidance": column.get("sql_guidance"),
                            },
                        )
                    )
    return cards


def _schema_cards_from_metadata(*, metadata_descriptions: dict[str, Any], schema: str) -> list[CatalogCard]:
    domain_id = f"domain:{_slug(schema)}"
    entity_id = f"entity:{_slug(schema)}"
    cards = [
        CatalogCard(
            id=domain_id,
            kind=CatalogCardKind.DOMAIN,
            name=schema,
            description="Schema scope from metadata descriptions.",
            terms=_terms_for(schema),
            source=CatalogSource.DESCRIPTION,
            review_status=CatalogReviewStatus.OBSERVED,
            metadata={"path": [schema]},
        ),
        CatalogCard(
            id=entity_id,
            kind=CatalogCardKind.ENTITY,
            name=schema,
            description="Tables grouped by the provided schema scope.",
            terms=_terms_for(schema),
            parent_ids=(domain_id,),
            source=CatalogSource.DESCRIPTION,
            review_status=CatalogReviewStatus.OBSERVED,
            metadata={"path": [schema]},
        ),
    ]
    table_descriptions = _dict(metadata_descriptions.get("tables"))
    column_descriptions = _dict(metadata_descriptions.get("columns"))
    for table_name, table_meta in sorted(table_descriptions.items()):
        table_desc = _description(_dict(table_meta))
        table_id = f"table:{table_name}"
        cards.append(
            CatalogCard(
                id=table_id,
                kind=CatalogCardKind.TABLE,
                name=str(table_name),
                description=table_desc,
                terms=_terms_for(table_name, table_desc),
                sql_ref=str(table_name),
                parent_ids=(domain_id, entity_id),
                source=CatalogSource.DESCRIPTION,
                review_status=CatalogReviewStatus.OBSERVED,
                metadata={"path": [schema, table_name]},
            )
        )
        for column_name, column_meta in sorted(_dict(column_descriptions.get(table_name)).items()):
            column_desc = _description(_dict(column_meta))
            sql_ref = f"{table_name}.{column_name}"
            cards.append(
                CatalogCard(
                    id=f"column:{sql_ref}",
                    kind=CatalogCardKind.COLUMN,
                    name=str(column_name),
                    description=column_desc,
                    terms=_terms_for(column_name, column_desc, sql_ref),
                    sql_ref=sql_ref,
                    parent_ids=(domain_id, entity_id, table_id),
                    source=CatalogSource.DESCRIPTION,
                    review_status=CatalogReviewStatus.OBSERVED,
                    metadata={
                        "table_name": table_name,
                        "column_name": column_name,
                        "path": [schema, table_name, column_name],
                    },
                )
            )
    return cards


def _card_from_ast_node(
    node: dict[str, Any],
    kind: CatalogCardKind,
    *,
    parent_ids: tuple[str, ...],
    metadata: dict[str, Any] | None = None,
) -> CatalogCard:
    name = str(node.get("name") or node.get("id") or "")
    description = str(node.get("description") or "")
    sql_ref = node.get("sql_ref")
    aliases = tuple(str(item) for item in node.get("aliases", []) if str(item).strip())
    terms = _terms_for(
        node.get("id"),
        name,
        description,
        sql_ref,
        *aliases,
        node.get("role"),
        node.get("sql_guidance"),
    )
    return CatalogCard(
        id=str(node["id"]),
        kind=kind,
        name=name,
        description=description,
        terms=terms,
        sql_ref=str(sql_ref) if sql_ref else None,
        parent_ids=parent_ids,
        source=CatalogSource.DESCRIPTION,
        review_status=CatalogReviewStatus.OBSERVED,
        metadata={**(metadata or {}), "path": node.get("path", []), "sql_library_ids": node.get("sql_library_ids", [])},
    )


def _sql_pattern_cards(sql_library: dict[str, Any]) -> list[CatalogCard]:
    cards: list[CatalogCard] = []
    patterns = _dict(sql_library.get("patterns"))
    if patterns:
        for pattern_id, pattern in sorted(patterns.items()):
            canonical = str(pattern.get("canonical_question") or pattern.get("summary") or pattern_id)
            summary = str(pattern.get("summary") or canonical)
            intent = _dict(pattern.get("intent_signature"))
            text = " ".join(
                [
                    canonical,
                    summary,
                    " ".join(map(str, pattern.get("paraphrases", []))),
                    " ".join(map(str, intent.values())),
                    " ".join(map(str, pattern.get("tables", []))),
                    " ".join(map(str, pattern.get("columns", []))),
                ]
            )
            cards.append(
                CatalogCard(
                    id=str(pattern.get("id") or pattern_id),
                    kind=CatalogCardKind.SQL_PATTERN,
                    name=canonical,
                    description=summary,
                    terms=_terms_for(text),
                    source=_source(pattern.get("source")),
                    review_status=_review_status(pattern.get("review_status")),
                    metadata={
                        "entry_id": pattern.get("entry_id"),
                        "tables": pattern.get("tables", []),
                        "columns": pattern.get("columns", []),
                        "intent_signature": intent,
                        "assumptions": pattern.get("assumptions", []),
                        "sql_template": pattern.get("sql_template"),
                    },
                )
            )
        return cards

    for entry_id, entry in sorted(_dict(sql_library.get("entries")).items()):
        name = str(entry.get("template") or entry_id)
        cards.append(
            CatalogCard(
                id=f"pattern:{entry_id}",
                kind=CatalogCardKind.SQL_PATTERN,
                name=name,
                description=name,
                terms=_terms_for(name, " ".join(map(str, entry.get("tables", []))), " ".join(map(str, entry.get("columns", [])))),
                source=_source(entry.get("source")),
                review_status=_review_status(entry.get("review_status")),
                metadata={
                    "entry_id": entry_id,
                    "tables": entry.get("tables", []),
                    "columns": entry.get("columns", []),
                    "sql_template": entry.get("sql") or entry.get("sql_template"),
                },
            )
        )
    return cards


def _metric_cards(sql_library: dict[str, Any]) -> list[CatalogCard]:
    cards: list[CatalogCard] = []
    for pattern_id, pattern in sorted(_dict(sql_library.get("patterns")).items()):
        intent = _dict(pattern.get("intent_signature"))
        measure = _one_line(intent.get("measure"))
        if not measure:
            continue
        cards.append(
            CatalogCard(
                id=f"metric:{_slug(measure)}:{_slug(pattern_id)[-10:]}",
                kind=CatalogCardKind.METRIC,
                name=measure,
                description=f"Metric used by pattern: {pattern.get('summary') or pattern.get('canonical_question') or pattern_id}",
                terms=_terms_for(measure, pattern.get("canonical_question")),
                source=_source(pattern.get("source")),
                review_status=_review_status(pattern.get("review_status")),
                metadata={
                    "pattern_id": pattern.get("id") or pattern_id,
                    "tables": pattern.get("tables", []),
                    "columns": pattern.get("columns", []),
                    "sql_template": pattern.get("sql_template"),
                },
            )
        )
    return cards


def _join_edges(*, sql_library: dict[str, Any], table_columns: dict[str, list[str]]) -> list[CatalogJoinEdge]:
    edge_state: dict[str, dict[str, Any]] = {}
    for entry_id, entry in sorted(_dict(sql_library.get("entries")).items()):
        for left, right in _join_pairs_from_entry(entry=entry, table_columns=table_columns):
            ordered = tuple(sorted([left, right]))
            edge_id = f"join:{ordered[0]}:{ordered[1]}"
            state = edge_state.setdefault(
                edge_id,
                {
                    "left": ordered[0],
                    "right": ordered[1],
                    "count": 0,
                    "entry_ids": [],
                },
            )
            state["count"] += 1
            state["entry_ids"].append(entry_id)
    edges = []
    for edge_id, state in sorted(edge_state.items()):
        left_table = str(state["left"]).split(".", 1)[0]
        right_table = str(state["right"]).split(".", 1)[0]
        edges.append(
            CatalogJoinEdge(
                id=edge_id,
                left_column=state["left"],
                right_column=state["right"],
                sql_condition=f"{state['left']} = {state['right']}",
                tables=(left_table, right_table),
                observed_count=int(state["count"]),
                source_entry_ids=tuple(sorted(set(state["entry_ids"]))[:20]),
            )
        )
    return edges


def _join_pairs_from_entry(*, entry: dict[str, Any], table_columns: dict[str, list[str]]) -> list[tuple[str, str]]:
    explicit_pairs = []
    for edge in entry.get("join_edges", []) if isinstance(entry.get("join_edges"), list) else []:
        if not isinstance(edge, dict):
            continue
        left = str(edge.get("left_column") or "")
        right = str(edge.get("right_column") or "")
        if _valid_column_ref(left, table_columns) and _valid_column_ref(right, table_columns):
            explicit_pairs.append((left, right))
    if explicit_pairs:
        return explicit_pairs

    sql = str(entry.get("sql") or entry.get("sql_template") or "")
    analysis = analyze_sql_references(sql, table_columns)
    return [(pair.left_column, pair.right_column) for pair in analysis.join_pairs]


def _valid_column_ref(ref: str, table_columns: dict[str, list[str]]) -> bool:
    if "." not in ref:
        return False
    table, column = ref.split(".", 1)
    return column in table_columns.get(table, [])


def _indexes(*, cards: list[CatalogCard], join_edges: list[CatalogJoinEdge]) -> dict[str, Any]:
    cards_by_kind: dict[str, list[str]] = {}
    cards_by_sql_ref: dict[str, list[str]] = {}
    cards_by_term: dict[str, list[str]] = {}
    sql_patterns_by_table: dict[str, list[str]] = {}
    sql_patterns_by_column: dict[str, list[str]] = {}
    join_edges_by_table: dict[str, list[str]] = {}
    join_edges_by_column: dict[str, list[str]] = {}

    for card in cards:
        cards_by_kind.setdefault(card.kind.value, []).append(card.id)
        if card.sql_ref:
            cards_by_sql_ref.setdefault(card.sql_ref, []).append(card.id)
        for term in card.terms:
            cards_by_term.setdefault(term, []).append(card.id)
        if card.kind == CatalogCardKind.SQL_PATTERN:
            for table in card.metadata.get("tables", []):
                sql_patterns_by_table.setdefault(str(table), []).append(card.id)
            for column in card.metadata.get("columns", []):
                sql_patterns_by_column.setdefault(str(column), []).append(card.id)

    for edge in join_edges:
        for table in edge.tables:
            join_edges_by_table.setdefault(table, []).append(edge.id)
        join_edges_by_column.setdefault(edge.left_column, []).append(edge.id)
        join_edges_by_column.setdefault(edge.right_column, []).append(edge.id)

    return {
        "cards_by_kind": _sorted_index(cards_by_kind),
        "cards_by_sql_ref": _sorted_index(cards_by_sql_ref),
        "cards_by_term": _sorted_index(cards_by_term),
        "sql_patterns_by_table": _sorted_index(sql_patterns_by_table),
        "sql_patterns_by_column": _sorted_index(sql_patterns_by_column),
        "join_edges_by_table": _sorted_index(join_edges_by_table),
        "join_edges_by_column": _sorted_index(join_edges_by_column),
    }


def _validate(
    *,
    cards: list[CatalogCard],
    join_edges: list[CatalogJoinEdge],
    table_columns: dict[str, list[str]],
) -> dict[str, Any]:
    tables = set(table_columns)
    columns = {f"{table}.{column}" for table, column_names in table_columns.items() for column in column_names}
    unknown_sql_refs = sorted(
        {
            str(card.sql_ref)
            for card in cards
            if card.sql_ref and card.kind in {CatalogCardKind.TABLE, CatalogCardKind.COLUMN, CatalogCardKind.DIMENSION, CatalogCardKind.VALUE}
            and card.sql_ref not in tables
            and card.sql_ref not in columns
        }
    )
    unknown_join_refs = sorted(
        {
            ref
            for edge in join_edges
            for ref in (edge.left_column, edge.right_column)
            if ref not in columns
        }
    )
    return {
        "status": "ok" if not unknown_sql_refs and not unknown_join_refs else "error",
        "table_count": len(tables),
        "column_count": len(columns),
        "card_count": len(cards),
        "join_edge_count": len(join_edges),
        "unknown_sql_refs": unknown_sql_refs,
        "unknown_join_refs": unknown_join_refs,
    }


def _table_columns(metadata_descriptions: dict[str, Any]) -> dict[str, list[str]]:
    return {
        str(table): sorted(map(str, _dict(columns).keys()))
        for table, columns in _dict(metadata_descriptions.get("columns")).items()
    }


def _source(value: Any) -> CatalogSource:
    normalized = str(value or "").strip().lower()
    if normalized == "query_history":
        return CatalogSource.QUERY_HISTORY
    if normalized == "nl_sql_pair":
        return CatalogSource.NL_SQL_PAIR
    if normalized == "self_play":
        return CatalogSource.SELF_PLAY
    if normalized == "description":
        return CatalogSource.DESCRIPTION
    if normalized == "agentic_learning":
        return CatalogSource.AGENTIC_LEARNING
    return CatalogSource.INFERRED


def _review_status(value: Any) -> CatalogReviewStatus:
    normalized = str(value or "").strip().lower()
    if normalized == "approved":
        return CatalogReviewStatus.APPROVED
    if normalized == "observed":
        return CatalogReviewStatus.OBSERVED
    return CatalogReviewStatus.NEEDS_REVIEW


def _description(payload: dict[str, Any]) -> str:
    return str(payload.get("long_description") or payload.get("short_description") or "")


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _terms_for(*values: Any) -> tuple[str, ...]:
    terms: set[str] = set()
    for value in values:
        if value is None:
            continue
        text = str(value)
        clean = " ".join(text.lower().replace("_", " ").replace("-", " ").split())
        if clean:
            terms.add(clean)
        for token in _tokens(text):
            terms.add(token)
    return tuple(sorted(terms))


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]+", text.lower().replace("_", " "))
        if len(token) > 1
    ]


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(value).lower()).strip("_")
    return text or "item"


def _dedupe_cards(cards: list[CatalogCard]) -> list[CatalogCard]:
    by_id: dict[str, CatalogCard] = {}
    for card in cards:
        if card.id not in by_id:
            by_id[card.id] = card
    return [by_id[key] for key in sorted(by_id)]


def _sorted_index(index: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: sorted(set(values)) for key, values in sorted(index.items())}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
