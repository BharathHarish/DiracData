"""Build NL-to-column relevance data from labeled NL-SQL examples."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from diracdata_v2.evals.schema_benchmark import BenchmarkCase, load_benchmark_cases
from diracdata_v2.retrieval.column_cards import ColumnCard, column_cards_from_catalog
from diracdata_v2.tools.hybrid import hybrid_search, tokenize


@dataclass(frozen=True)
class ColumnRetrievalPair:
    pair_id: str
    case_id: str
    query: str
    candidate_id: str
    sql_ref: str
    table_name: str
    column_name: str
    candidate_text: str
    label: int
    example_type: str
    source_category: str = ""
    source_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_column_retrieval_pairs(
    *,
    cases: list[BenchmarkCase],
    column_cards: list[ColumnCard],
    negatives_per_positive: int = 4,
    bm25_pool_size: int = 80,
) -> list[ColumnRetrievalPair]:
    """Create query-card relevance rows with deterministic hard negatives."""

    by_ref = {card.sql_ref: card for card in column_cards}
    documents = [card.to_document() for card in column_cards]
    rows: list[ColumnRetrievalPair] = []
    for case in cases:
        positives = [by_ref[ref] for ref in case.expected_columns if ref in by_ref]
        if not positives:
            continue
        positive_refs = {card.sql_ref for card in positives}
        for card in positives:
            rows.append(_pair(case=case, card=card, label=1, example_type="positive"))
        needed = max(1, len(positives) * max(0, negatives_per_positive))
        negatives = _hard_negatives(
            case=case,
            column_cards=column_cards,
            positive_refs=positive_refs,
            documents=documents,
            bm25_pool_size=bm25_pool_size,
            limit=needed,
        )
        rows.extend(
            _pair(case=case, card=card, label=0, example_type=example_type)
            for card, example_type in negatives
        )
    return rows


def build_pairs_from_files(
    *,
    questions_path: Path,
    semantic_catalog_path: Path,
    negatives_per_positive: int = 4,
    bm25_pool_size: int = 80,
) -> list[ColumnRetrievalPair]:
    cases = load_benchmark_cases(questions_path)
    catalog = json.loads(semantic_catalog_path.read_text(encoding="utf-8"))
    cards = column_cards_from_catalog(catalog)
    return build_column_retrieval_pairs(
        cases=cases,
        column_cards=cards,
        negatives_per_positive=negatives_per_positive,
        bm25_pool_size=bm25_pool_size,
    )


def write_column_retrieval_pairs(rows: list[ColumnRetrievalPair], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "pair_id",
        "case_id",
        "query",
        "candidate_id",
        "sql_ref",
        "table_name",
        "column_name",
        "candidate_text",
        "label",
        "example_type",
        "source_category",
        "source_notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_dict())
    return path


def _hard_negatives(
    *,
    case: BenchmarkCase,
    column_cards: list[ColumnCard],
    positive_refs: set[str],
    documents: list[Any],
    bm25_pool_size: int,
    limit: int,
) -> list[tuple[ColumnCard, str]]:
    chosen: dict[str, tuple[ColumnCard, str]] = {}
    by_ref = {card.sql_ref: card for card in column_cards}
    positive_cards = [by_ref[ref] for ref in positive_refs if ref in by_ref]

    search = hybrid_search(documents=documents, query=case.question, top_k=bm25_pool_size)
    for hit in search["hits"]:
        sql_ref = str(_dict(hit.get("metadata")).get("sql_ref") or "")
        card = by_ref.get(sql_ref)
        if card and card.sql_ref not in positive_refs:
            chosen.setdefault(card.sql_ref, (card, "hard_bm25"))
        if len(chosen) >= limit:
            return list(chosen.values())

    positive_tables = {card.table_name for card in positive_cards}
    for card in column_cards:
        if card.sql_ref in positive_refs or card.sql_ref in chosen:
            continue
        if card.table_name in positive_tables:
            chosen[card.sql_ref] = (card, "hard_same_table")
        if len(chosen) >= limit:
            return list(chosen.values())

    positive_column_tokens = set()
    for card in positive_cards:
        positive_column_tokens.update(tokenize(card.column_name.replace("_", " ")))
    for card in column_cards:
        if card.sql_ref in positive_refs or card.sql_ref in chosen:
            continue
        if positive_column_tokens & set(tokenize(card.column_name.replace("_", " "))):
            chosen[card.sql_ref] = (card, "hard_name_overlap")
        if len(chosen) >= limit:
            return list(chosen.values())

    for card in _stable_shuffle(column_cards, seed=case.case_id):
        if card.sql_ref in positive_refs or card.sql_ref in chosen:
            continue
        chosen[card.sql_ref] = (card, "negative_fill")
        if len(chosen) >= limit:
            break
    return list(chosen.values())


def _pair(*, case: BenchmarkCase, card: ColumnCard, label: int, example_type: str) -> ColumnRetrievalPair:
    raw_id = f"{case.case_id}|{card.sql_ref}|{label}|{example_type}"
    pair_id = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:16]
    return ColumnRetrievalPair(
        pair_id=pair_id,
        case_id=case.case_id,
        query=case.question,
        candidate_id=card.card_id,
        sql_ref=card.sql_ref,
        table_name=card.table_name,
        column_name=card.column_name,
        candidate_text=card.text,
        label=label,
        example_type=example_type,
        source_category=case.category,
        source_notes=case.notes,
    )


def _stable_shuffle(cards: list[ColumnCard], *, seed: str) -> list[ColumnCard]:
    return sorted(
        cards,
        key=lambda card: hashlib.sha1(f"{seed}|{card.sql_ref}".encode("utf-8")).hexdigest(),
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
