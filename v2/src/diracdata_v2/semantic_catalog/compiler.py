"""Runtime context compiler over the semantic catalog."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from diracdata_v2.semantic_catalog.contracts import CatalogCardKind, CompiledContext
from diracdata_v2.tools.hybrid import RetrievalDocument, hybrid_search, tokenize


QUERY_STOPWORDS = {
    "all",
    "also",
    "any",
    "but",
    "did",
    "does",
    "give",
    "have",
    "how",
    "many",
    "not",
    "number",
    "show",
    "the",
    "then",
    "were",
    "what",
    "which",
    "who",
}

DEFINITION_REQUIRED_TERMS = {
    "active",
    "activated",
    "churn",
    "churned",
    "engaged",
    "inactive",
    "new",
    "retained",
    "retention",
    "returning",
    "repeat",
}


@dataclass(frozen=True)
class SemanticCatalogCompiler:
    semantic_catalog: dict[str, Any]

    @classmethod
    def from_file(cls, path: Path) -> "SemanticCatalogCompiler":
        return cls(semantic_catalog=json.loads(path.read_text(encoding="utf-8")))

    def compile(
        self,
        question: str,
        *,
        max_cards: int = 24,
        max_patterns: int = 6,
    ) -> CompiledContext:
        documents = _documents(self.semantic_catalog)
        recall = hybrid_search(
            documents=documents,
            query=question,
            search_terms=_focused_search_terms(question),
            top_k=max(80, (max_cards + max_patterns) * 4),
        )
        cards_by_id = self.semantic_catalog.get("cards", {})
        recalled_cards = [cards_by_id[hit["id"]] for hit in recall["hits"] if hit["id"] in cards_by_id]
        ranked_cards = _rank_cards_by_intent(
            documents=documents,
            cards_by_id=cards_by_id,
            question=question,
            recalled_card_ids={str(card.get("id")) for card in recalled_cards},
        )
        selected_cards = [card for card, _ in ranked_cards[: max(80, (max_cards + max_patterns) * 4)]]
        direct_cards = [
            card
            for card, _ in ranked_cards
            if card.get("kind") != CatalogCardKind.SQL_PATTERN.value
        ][: max_cards * 3]
        pattern_cards = _select_patterns(
            ranked_cards=ranked_cards,
            question=question,
            direct_cards=direct_cards,
            limit=max_patterns,
        )
        expanded_cards = _cards_from_pattern_columns(
            catalog=self.semantic_catalog,
            pattern_cards=pattern_cards,
        )
        non_pattern_cards = _select_context_cards(
            pattern_expanded_cards=expanded_cards,
            direct_cards=direct_cards,
            question=question,
            limit=max_cards,
        )
        required_tables = _required_tables(pattern_cards=pattern_cards, cards=non_pattern_cards)
        join_edges = _join_edges_for_tables(self.semantic_catalog, required_tables)
        unresolved = _unresolved_terms(question=question, selected_cards=selected_cards)
        resolved = _resolved_terms(question=question, selected_cards=selected_cards)
        assertions = _assertions(pattern_cards=pattern_cards, cards=non_pattern_cards, join_edges=join_edges)
        return CompiledContext(
            question=question,
            needs_clarification=bool(unresolved),
            resolved_terms=tuple(resolved),
            unresolved_terms=tuple(unresolved),
            candidate_cards=tuple(_compact_card(card) for card in non_pattern_cards),
            sql_patterns=tuple(_compact_pattern(card) for card in pattern_cards),
            join_edges=tuple(join_edges),
            assertions=tuple(assertions),
            retrieval={
                "search_queries": recall["search_queries"],
                "hit_count": len(recall["hits"]),
                "reranked_card_count": len(ranked_cards),
                "required_tables": sorted(required_tables),
                **recall["retrieval"],
            },
        )


def _documents(catalog: dict[str, Any]) -> list[RetrievalDocument]:
    documents = []
    for card_id, card in sorted(catalog.get("cards", {}).items()):
        if card.get("kind") == CatalogCardKind.VALUE.value:
            continue
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                " ".join(map(str, card.get("terms", []))),
                str(card.get("sql_ref") or ""),
                " ".join(map(str, card.get("metadata", {}).get("tables", []))),
                " ".join(map(str, card.get("metadata", {}).get("columns", []))),
            ]
        )
        documents.append(
            RetrievalDocument(
                id=card_id,
                text=text,
                source_type=str(card.get("kind") or "card"),
                table_name=card.get("metadata", {}).get("table_name"),
                column_name=card.get("metadata", {}).get("column_name"),
                metadata=card,
            )
        )
    return documents


def _focused_search_terms(question: str) -> list[str]:
    tokens = _meaningful_tokens(question)
    terms = []
    for size in (3, 2):
        for index in range(0, max(0, len(tokens) - size + 1)):
            terms.append(" ".join(tokens[index : index + size]))
    terms.extend(tokens)
    return terms[:24]


def _rank_cards_by_intent(
    *,
    documents: list[RetrievalDocument],
    cards_by_id: dict[str, Any],
    question: str,
    recalled_card_ids: set[str],
) -> list[tuple[dict[str, Any], float]]:
    query_tokens = set(_meaningful_tokens(question))
    if not query_tokens:
        return []
    tokenized_documents = {
        document.id: set(_meaningful_tokens(document.text))
        for document in documents
    }
    idf = _idf(tokenized_documents)
    phrases = _focused_search_terms(question)
    ranked: list[tuple[dict[str, Any], float]] = []
    for document in documents:
        card = cards_by_id.get(document.id)
        if not isinstance(card, dict):
            continue
        tokens = tokenized_documents.get(document.id, set())
        overlap = query_tokens & tokens
        score = sum(idf.get(token, 0.0) for token in overlap)
        text_lower = document.text.lower()
        for phrase in phrases:
            if " " in phrase and phrase.lower() in text_lower:
                score += 2.0 + min(2.0, len(phrase.split()) * 0.5)
        score += _kind_bias(card)
        if document.id in recalled_card_ids:
            score += 0.25
        if score > 0:
            ranked.append((card, score))
    ranked.sort(key=lambda item: (-item[1], _kind_order(item[0]), str(item[0].get("id"))))
    return ranked


def _select_patterns(
    *,
    ranked_cards: list[tuple[dict[str, Any], float]],
    question: str,
    direct_cards: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    query_tokens = set(_meaningful_tokens(question))
    direct_sql_refs = {
        str(card.get("sql_ref"))
        for card in direct_cards
        if card.get("sql_ref")
    }
    direct_tables = {
        ref.split(".", 1)[0]
        for ref in direct_sql_refs
        if "." in ref
    }
    patterns: list[tuple[float, dict[str, Any]]] = []
    for card, base_score in ranked_cards:
        if card.get("kind") != CatalogCardKind.SQL_PATTERN.value:
            continue
        metadata = card.get("metadata", {})
        intent = metadata.get("intent_signature") if isinstance(metadata.get("intent_signature"), dict) else {}
        intent_text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                " ".join(map(str, intent.values())),
            ]
        )
        intent_tokens = set(_meaningful_tokens(intent_text))
        overlap = query_tokens & intent_tokens
        if not overlap:
            continue
        pattern_columns = set(map(str, metadata.get("columns", [])))
        pattern_tables = set(map(str, metadata.get("tables", [])))
        column_support = len(pattern_columns & direct_sql_refs)
        table_support = len(pattern_tables & direct_tables)
        specificity = len(overlap) / max(1, len(query_tokens))
        observed_bonus = 0.4 if card.get("review_status") in {"observed", "approved"} else 0.0
        support_bonus = min(12.0, column_support * 2.0 + table_support * 0.5)
        patterns.append((base_score + specificity * 6.0 + support_bonus + observed_bonus, card))
    patterns.sort(key=lambda item: (-item[0], str(item[1].get("id"))))
    return [card for _, card in patterns[: max(0, limit)]]


def _idf(tokenized_documents: dict[str, set[str]]) -> dict[str, float]:
    import math

    doc_count = max(1, len(tokenized_documents))
    df: dict[str, int] = {}
    for tokens in tokenized_documents.values():
        for token in tokens:
            df[token] = df.get(token, 0) + 1
    return {
        token: math.log((doc_count + 1) / (count + 0.5))
        for token, count in df.items()
    }


def _kind_bias(card: dict[str, Any]) -> float:
    kind = card.get("kind")
    if kind == CatalogCardKind.COLUMN.value:
        return 0.5
    if kind == CatalogCardKind.DIMENSION.value:
        return 0.45
    if kind == CatalogCardKind.SQL_PATTERN.value:
        return 0.35
    if kind == CatalogCardKind.METRIC.value:
        return 0.3
    if kind == CatalogCardKind.TABLE.value:
        return 0.2
    return 0.0


def _required_tables(*, pattern_cards: list[dict[str, Any]], cards: list[dict[str, Any]]) -> set[str]:
    tables: set[str] = set()
    for pattern in pattern_cards:
        tables.update(map(str, pattern.get("metadata", {}).get("tables", [])))
    for card in cards:
        metadata = card.get("metadata", {})
        table_name = metadata.get("table_name")
        if table_name:
            tables.add(str(table_name))
        sql_ref = card.get("sql_ref")
        if card.get("kind") == CatalogCardKind.TABLE.value and sql_ref:
            tables.add(str(sql_ref))
        elif isinstance(sql_ref, str) and "." in sql_ref:
            tables.add(sql_ref.split(".", 1)[0])
    return tables


def _cards_from_pattern_columns(
    *,
    catalog: dict[str, Any],
    pattern_cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    cards = catalog.get("cards", {})
    by_sql_ref = catalog.get("indexes", {}).get("cards_by_sql_ref", {})
    expanded: list[dict[str, Any]] = []
    for pattern in pattern_cards:
        for column_ref in pattern.get("metadata", {}).get("columns", []):
            for card_id in by_sql_ref.get(str(column_ref), []):
                card = cards.get(card_id)
                if card:
                    expanded.append(card)
        for table_name in pattern.get("metadata", {}).get("tables", []):
            for card_id in by_sql_ref.get(str(table_name), []):
                card = cards.get(card_id)
                if card:
                    expanded.append(card)
    return expanded


def _select_context_cards(
    *,
    pattern_expanded_cards: list[dict[str, Any]],
    direct_cards: list[dict[str, Any]],
    question: str,
    limit: int,
) -> list[dict[str, Any]]:
    pattern_columns = [
        card
        for card in pattern_expanded_cards
        if card.get("kind") == CatalogCardKind.COLUMN.value
    ]
    pattern_tables = [
        card
        for card in pattern_expanded_cards
        if card.get("kind") == CatalogCardKind.TABLE.value
    ]
    direct_non_patterns = [
        card
        for card in direct_cards
        if card.get("kind") != CatalogCardKind.SQL_PATTERN.value
    ]
    ordered = _dedupe_cards(pattern_columns + direct_non_patterns + pattern_tables)
    return ordered[:limit]


def _query_overlap_count(card: dict[str, Any], query_tokens: set[str]) -> int:
    text = " ".join(
        [
            str(card.get("name") or ""),
            str(card.get("description") or ""),
            str(card.get("sql_ref") or ""),
            " ".join(map(str, card.get("terms", []))),
        ]
    )
    return len(query_tokens & set(_meaningful_tokens(text)))


def _kind_order(card: dict[str, Any]) -> int:
    order = {
        CatalogCardKind.COLUMN.value: 0,
        CatalogCardKind.DIMENSION.value: 1,
        CatalogCardKind.SQL_PATTERN.value: 2,
        CatalogCardKind.METRIC.value: 3,
        CatalogCardKind.TABLE.value: 4,
        CatalogCardKind.BUSINESS_TERM.value: 5,
        CatalogCardKind.DOMAIN.value: 6,
        CatalogCardKind.ENTITY.value: 7,
    }
    return order.get(str(card.get("kind")), 99)


def _dedupe_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output = []
    for card in cards:
        card_id = str(card.get("id") or "")
        if not card_id or card_id in seen:
            continue
        seen.add(card_id)
        output.append(card)
    return output


def _join_edges_for_tables(catalog: dict[str, Any], tables: set[str]) -> list[dict[str, Any]]:
    if len(tables) < 2:
        return []
    edges = []
    for edge in catalog.get("join_edges", {}).values():
        edge_tables = set(map(str, edge.get("tables", [])))
        if len(edge_tables & tables) >= 2:
            edges.append(edge)
    edges.sort(key=lambda item: (-int(item.get("observed_count") or 0), item.get("id", "")))
    return edges[:16]


def _unresolved_terms(*, question: str, selected_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    terms = set(_meaningful_tokens(question))
    unresolved = []
    for term in sorted(terms & DEFINITION_REQUIRED_TERMS):
        if not _has_explicit_definition(term, selected_cards):
            unresolved.append(
                {
                    "term": term,
                    "reason": (
                        "SQL-affecting business term requires an explicit metric, business-term, "
                        "or approved semantic definition before SQL execution."
                    ),
                }
            )
    return unresolved


def _has_explicit_definition(term: str, selected_cards: list[dict[str, Any]]) -> bool:
    for card in selected_cards:
        kind = card.get("kind")
        if kind not in {CatalogCardKind.BUSINESS_TERM.value, CatalogCardKind.METRIC.value}:
            continue
        searchable = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                " ".join(map(str, card.get("terms", []))),
            ]
        ).lower()
        if re.search(rf"\b{re.escape(term)}\b", searchable):
            return True
    return False


def _resolved_terms(*, question: str, selected_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query_terms = set(_meaningful_tokens(question))
    resolved = []
    seen: set[tuple[str, str]] = set()
    for card in selected_cards:
        terms = set(_meaningful_tokens(" ".join(map(str, card.get("terms", [])))))
        overlap = sorted(query_terms & terms)
        if not overlap:
            continue
        binding = _binding_for_resolved_term(card)
        item_key = (str(overlap[0]), str(binding))
        if item_key in seen:
            continue
        seen.add(item_key)
        resolved.append(
            {
                "terms": overlap[:5],
                "card_id": card.get("id"),
                "kind": card.get("kind"),
                "binding": binding,
                "confidence": "candidate",
            }
        )
        if len(resolved) >= 16:
            break
    return resolved


def _meaningful_tokens(text: str) -> list[str]:
    return [token for token in tokenize(text) if token not in QUERY_STOPWORDS]


def _binding_for_resolved_term(card: dict[str, Any]) -> str:
    if card.get("sql_ref"):
        return str(card["sql_ref"])
    if card.get("kind") == CatalogCardKind.SQL_PATTERN.value:
        return str(card.get("id"))
    metadata = card.get("metadata", {})
    if metadata.get("value") and card.get("sql_ref"):
        return f"{card['sql_ref']} = {metadata['value']}"
    return str(card.get("id"))


def _assertions(
    *,
    pattern_cards: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    join_edges: list[dict[str, Any]],
) -> list[str]:
    assertions: list[str] = []
    for pattern in pattern_cards:
        assertions.extend(map(str, pattern.get("metadata", {}).get("assumptions", [])))
        intent = pattern.get("metadata", {}).get("intent_signature", {})
        grain = intent.get("grain") if isinstance(intent, dict) else None
        if grain:
            assertions.append(f"Preserve pattern grain: {grain}")
    if join_edges:
        assertions.append("Use observed join edges unless the question requires a different relationship.")
    if any(card.get("kind") == CatalogCardKind.COLUMN.value and card.get("metadata", {}).get("role") == "measure" for card in cards):
        assertions.append("Do not aggregate measures across a lower grain without checking duplicate inflation.")
    return _dedupe(assertions)[:20]


def _compact_card(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": card.get("id"),
        "kind": card.get("kind"),
        "name": card.get("name"),
        "description": card.get("description"),
        "sql_ref": card.get("sql_ref"),
        "metadata": {
            key: value
            for key, value in card.get("metadata", {}).items()
            if key in {"table_name", "column_name", "role", "table_grain", "grain", "value"}
        },
    }


def _compact_pattern(card: dict[str, Any]) -> dict[str, Any]:
    metadata = card.get("metadata", {})
    return {
        "id": card.get("id"),
        "name": card.get("name"),
        "description": card.get("description"),
        "tables": metadata.get("tables", []),
        "columns": metadata.get("columns", []),
        "intent_signature": metadata.get("intent_signature", {}),
        "sql_template": metadata.get("sql_template"),
        "assumptions": metadata.get("assumptions", []),
        "source": card.get("source"),
        "review_status": card.get("review_status"),
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output = []
    for value in values:
        clean = " ".join(str(value).split())
        if clean and clean not in seen:
            seen.add(clean)
            output.append(clean)
    return output
