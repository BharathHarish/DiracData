"""Column-card representation used for schema-aware retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from diracdata_v2.semantic_catalog.contracts import CatalogCardKind
from diracdata_v2.tools.hybrid import RetrievalDocument


@dataclass(frozen=True)
class ColumnCard:
    """Compact semantic document for one table.column candidate."""

    card_id: str
    sql_ref: str
    table_name: str
    column_name: str
    text: str
    metadata: dict[str, Any]

    def to_document(self) -> RetrievalDocument:
        return RetrievalDocument(
            id=self.card_id,
            text=self.text,
            source_type="column",
            table_name=self.table_name,
            column_name=self.column_name,
            metadata={
                "sql_ref": self.sql_ref,
                "table_name": self.table_name,
                "column_name": self.column_name,
                **self.metadata,
            },
        )


def column_cards_from_catalog(catalog: dict[str, Any]) -> list[ColumnCard]:
    """Build column retrieval cards from the semantic catalog.

    The stable label is always ``table.column``. The text is the evidence a
    reranker sees while deciding whether that stable column id matches a
    natural-language query.
    """

    raw_cards = _dict(catalog.get("cards"))
    table_descriptions = _table_descriptions(raw_cards)
    output: list[ColumnCard] = []
    for card_id, card in sorted(raw_cards.items()):
        if card.get("kind") != CatalogCardKind.COLUMN.value:
            continue
        sql_ref = str(card.get("sql_ref") or "")
        if "." not in sql_ref:
            continue
        table_name, column_name = sql_ref.split(".", 1)
        metadata = _dict(card.get("metadata"))
        terms = " ".join(str(item) for item in card.get("terms", []) if str(item).strip())
        text = _compact_text(
            [
                f"table: {table_name}",
                f"column: {column_name}",
                f"sql_ref: {sql_ref}",
                f"column_description: {card.get('description') or ''}",
                f"table_description: {table_descriptions.get(table_name, '')}",
                f"terms: {terms}",
                f"role: {metadata.get('role') or ''}",
                f"table_grain: {metadata.get('table_grain') or metadata.get('grain') or ''}",
                f"null_meaning: {metadata.get('null_meaning') or ''}",
                f"sql_guidance: {metadata.get('sql_guidance') or ''}",
            ]
        )
        output.append(
            ColumnCard(
                card_id=str(card_id),
                sql_ref=sql_ref,
                table_name=table_name,
                column_name=column_name,
                text=text,
                metadata={
                    "source_card_id": str(card_id),
                    "description": str(card.get("description") or ""),
                    "terms": list(card.get("terms", [])),
                    **metadata,
                },
            )
        )
    return output


def _table_descriptions(cards: dict[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    for card in cards.values():
        if card.get("kind") != CatalogCardKind.TABLE.value:
            continue
        sql_ref = str(card.get("sql_ref") or card.get("name") or "")
        if not sql_ref:
            continue
        output[sql_ref] = str(card.get("description") or "")
    return output


def _compact_text(parts: list[str]) -> str:
    return " ".join(" ".join(part.split()) for part in parts if str(part).strip())


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
