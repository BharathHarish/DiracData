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
        schema_ast: dict[str, Any],
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
    schema_ast: dict[str, Any],
    sql_library: dict[str, Any],
    catalog: str,
    database: str,
    schema: str,
    run_id: str,
) -> dict[str, Any]:
    table_columns = _table_columns(metadata_descriptions)
    cards = _schema_cards(schema_ast=schema_ast, metadata_descriptions=metadata_descriptions)
    cards.extend(_sql_pattern_cards(sql_library))
    cards.extend(_metric_cards(sql_library))
    cards.extend(_dimension_cards(cards))
    cards.extend(_value_cards(cards))
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
            "schema_ast_run_id": schema_ast.get("run_id"),
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
    schema_ast: dict[str, Any],
    metadata_descriptions: dict[str, Any],
) -> list[CatalogCard]:
    cards: list[CatalogCard] = []
    table_descriptions = _dict(metadata_descriptions.get("tables"))
    column_descriptions = _dict(metadata_descriptions.get("columns"))
    for domain in schema_ast.get("domains", []):
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
                    role = _column_role(table_name=table_name, column_name=column_name, description=column_desc)
                    cards.append(
                        _card_from_ast_node(
                            {
                                **column,
                                "description": column_desc,
                                "role": column.get("role") or role,
                            },
                            CatalogCardKind.COLUMN,
                            parent_ids=(domain["id"], entity["id"], table["id"]),
                            metadata={
                                "table_name": table_name,
                                "column_name": column_name,
                                "role": column.get("role") or role,
                                "table_grain": table.get("grain"),
                                "null_meaning": column.get("null_meaning"),
                                "sql_guidance": column.get("sql_guidance"),
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
        measure = str(intent.get("measure") or "")
        if not measure:
            continue
        for metric_name in _metric_names(measure):
            cards.append(
                CatalogCard(
                    id=f"metric:{_slug(metric_name)}:{_slug(pattern_id)[-10:]}",
                    kind=CatalogCardKind.METRIC,
                    name=metric_name,
                    description=f"Metric used by pattern: {pattern.get('summary') or pattern.get('canonical_question') or pattern_id}",
                    terms=_terms_for(metric_name, measure, pattern.get("canonical_question")),
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


def _dimension_cards(cards: list[CatalogCard]) -> list[CatalogCard]:
    dimensions: list[CatalogCard] = []
    for card in cards:
        if card.kind != CatalogCardKind.COLUMN:
            continue
        role = str(card.metadata.get("role") or "")
        column_name = str(card.metadata.get("column_name") or card.name)
        if role not in {"dimension", "status", "time"}:
            continue
        dimensions.append(
            CatalogCard(
                id=f"dimension:{card.sql_ref}",
                kind=CatalogCardKind.DIMENSION,
                name=column_name,
                description=card.description,
                terms=_terms_for(column_name, card.description, *card.terms),
                sql_ref=card.sql_ref,
                parent_ids=(card.id,),
                source=CatalogSource.DESCRIPTION,
                review_status=CatalogReviewStatus.OBSERVED,
                metadata={
                    "column_card_id": card.id,
                    "table_name": card.metadata.get("table_name"),
                    "column_name": column_name,
                    "role": role,
                },
            )
        )
    return dimensions


def _value_cards(cards: list[CatalogCard]) -> list[CatalogCard]:
    values: list[CatalogCard] = []
    for card in cards:
        if card.kind != CatalogCardKind.COLUMN or not card.sql_ref:
            continue
        for value in _quoted_values(card.description):
            values.append(
                CatalogCard(
                    id=f"value:{card.sql_ref}:{_slug(value)}",
                    kind=CatalogCardKind.VALUE,
                    name=value,
                    description=f"Observed/described value for {card.sql_ref}.",
                    terms=_terms_for(value, card.sql_ref, card.description),
                    sql_ref=card.sql_ref,
                    parent_ids=(card.id,),
                    source=CatalogSource.DESCRIPTION,
                    review_status=CatalogReviewStatus.OBSERVED,
                    metadata={"value": value, "column_card_id": card.id},
                )
            )
    return values


def _join_edges(*, sql_library: dict[str, Any], table_columns: dict[str, list[str]]) -> list[CatalogJoinEdge]:
    edge_state: dict[str, dict[str, Any]] = {}
    for entry_id, entry in sorted(_dict(sql_library.get("entries")).items()):
        sql = str(entry.get("sql") or entry.get("sql_template") or "")
        for left, right in _extract_join_column_pairs(sql=sql, table_columns=table_columns):
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


def _extract_join_column_pairs(*, sql: str, table_columns: dict[str, list[str]]) -> list[tuple[str, str]]:
    aliases = _aliases(sql, table_columns)
    pairs: list[tuple[str, str]] = []
    for left_alias, left_col, right_alias, right_col in re.findall(
        r"\b([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)\s*=\s*([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)\b",
        sql,
        flags=re.IGNORECASE,
    ):
        left_table = aliases.get(left_alias.lower())
        right_table = aliases.get(right_alias.lower())
        if not left_table or not right_table or left_table == right_table:
            continue
        if left_col not in table_columns.get(left_table, []) or right_col not in table_columns.get(right_table, []):
            continue
        pairs.append((f"{left_table}.{left_col}", f"{right_table}.{right_col}"))
    return pairs


def _aliases(sql: str, table_columns: dict[str, list[str]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    table_names = set(table_columns)
    for table, alias in re.findall(
        r"\b(?:from|join)\s+([a-zA-Z_][\w]*)(?:\s+(?:as\s+)?([a-zA-Z_][\w]*))?",
        sql,
        flags=re.IGNORECASE,
    ):
        table_name = table.lower()
        actual = next((item for item in table_names if item.lower() == table_name), None)
        if actual is None:
            continue
        aliases[actual.lower()] = actual
        if alias and alias.lower() not in {"on", "where", "join", "left", "right", "inner", "outer", "full", "cross"}:
            aliases[alias.lower()] = actual
    return aliases


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


def _column_role(*, table_name: str, column_name: str, description: str) -> str:
    text = f"{table_name} {column_name} {description}".lower()
    if re.search(r"(_ref|_record|_id| identifier| unique id| links? )", text):
        return "join_key"
    if re.search(r"(date|day|month|year|time|calendar)", text):
        return "time"
    if re.search(r"(amount|price|cost|paid|profit|revenue|tax|discount|quantity|count|volume)", text):
        return "measure"
    if re.search(r"(status|flag|rating|category|class|type|state|country|city|gender|name|brand|channel)", text):
        return "dimension"
    return "unknown"


def _metric_names(measure_text: str) -> list[str]:
    chunks = re.split(r",|\band\b|/", measure_text)
    output = []
    for chunk in chunks:
        clean = " ".join(chunk.strip().strip("()").split())
        if len(clean) >= 3 and re.search(
            r"\b(count|sum|avg|average|revenue|amount|paid|profit|cost|customers|orders|sales|quantity|volume)\b",
            clean,
            flags=re.IGNORECASE,
        ):
            output.append(clean)
    return output[:8]


def _source(value: Any) -> CatalogSource:
    normalized = str(value or "").strip().lower()
    if normalized == "query_history":
        return CatalogSource.QUERY_HISTORY
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


def _quoted_values(text: str) -> list[str]:
    values = []
    for value in re.findall(r"'([^']{1,60})'", text):
        clean = " ".join(value.split())
        if clean and clean.lower() not in {"unknown", "null"}:
            values.append(clean)
    return sorted(set(values))


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
    stop = {
        "and",
        "are",
        "for",
        "from",
        "into",
        "one",
        "per",
        "the",
        "this",
        "used",
        "with",
    }
    return [
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]+", text.lower().replace("_", " "))
        if len(token) > 1 and token not in stop
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
