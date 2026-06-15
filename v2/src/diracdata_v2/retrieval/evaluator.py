"""Offline column retrieval evaluator."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Protocol

from diracdata_v2.evals.schema_benchmark import BenchmarkCase, load_benchmark_cases
from diracdata_v2.retrieval.column_cards import ColumnCard, column_cards_from_catalog
from diracdata_v2.tools.hybrid import hybrid_search


class ColumnReranker(Protocol):
    def score(self, *, query: str, candidates: list[ColumnCard]) -> list[float]: ...


@dataclass(frozen=True)
class ColumnRetrievalReport:
    questions_path: str
    semantic_catalog_path: str
    case_count: int
    aggregate_scores: dict[str, float]
    cases: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "questions_path": self.questions_path,
            "semantic_catalog_path": self.semantic_catalog_path,
            "case_count": self.case_count,
            "aggregate_scores": self.aggregate_scores,
            "cases": list(self.cases),
        }


def evaluate_column_retrieval_from_files(
    *,
    questions_path: Path,
    semantic_catalog_path: Path,
    top_ks: tuple[int, ...] = (10, 20, 40),
    candidate_pool_size: int = 100,
    reranker: ColumnReranker | None = None,
) -> ColumnRetrievalReport:
    cases = load_benchmark_cases(questions_path)
    catalog = json.loads(semantic_catalog_path.read_text(encoding="utf-8"))
    cards = column_cards_from_catalog(catalog)
    return evaluate_column_retrieval(
        cases=cases,
        column_cards=cards,
        questions_path=questions_path,
        semantic_catalog_path=semantic_catalog_path,
        top_ks=top_ks,
        candidate_pool_size=candidate_pool_size,
        reranker=reranker,
    )


def evaluate_column_retrieval(
    *,
    cases: list[BenchmarkCase],
    column_cards: list[ColumnCard],
    questions_path: Path | None = None,
    semantic_catalog_path: Path | None = None,
    top_ks: tuple[int, ...] = (10, 20, 40),
    candidate_pool_size: int = 100,
    reranker: ColumnReranker | None = None,
) -> ColumnRetrievalReport:
    documents = [card.to_document() for card in column_cards]
    by_id = {card.card_id: card for card in column_cards}
    case_results: list[dict[str, Any]] = []
    for case in cases:
        search = hybrid_search(documents=documents, query=case.question, top_k=candidate_pool_size)
        candidates = [
            by_id[hit["id"]]
            for hit in search["hits"]
            if hit["id"] in by_id
        ]
        if reranker is not None and candidates:
            scores = reranker.score(query=case.question, candidates=candidates)
            candidates = [
                card
                for card, _ in sorted(
                    zip(candidates, scores, strict=False),
                    key=lambda item: (-float(item[1]), item[0].sql_ref),
                )
            ]
        case_results.append(_score_case(case=case, candidates=candidates, top_ks=top_ks))
    return ColumnRetrievalReport(
        questions_path=str(questions_path or ""),
        semantic_catalog_path=str(semantic_catalog_path or ""),
        case_count=len(cases),
        aggregate_scores=_aggregate(case_results, top_ks=top_ks),
        cases=tuple(case_results),
    )


def write_column_retrieval_report(report: ColumnRetrievalReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "column_retrieval_report.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def _score_case(*, case: BenchmarkCase, candidates: list[ColumnCard], top_ks: tuple[int, ...]) -> dict[str, Any]:
    expected = set(case.expected_columns)
    ranked_refs = [card.sql_ref for card in candidates]
    scores = {}
    missing = {}
    for k in sorted({int(item) for item in top_ks if int(item) > 0}):
        retrieved = set(ranked_refs[:k])
        scores[f"column_recall@{k}"] = _recall(expected, retrieved)
        scores[f"column_precision@{k}"] = _precision(expected, retrieved)
        missing[f"missing_columns@{k}"] = sorted(expected - retrieved)
    return {
        "case": case.to_dict(),
        "scores": scores,
        "missing": missing,
        "top_columns": ranked_refs[: max(top_ks, default=20)],
    }


def _aggregate(case_results: list[dict[str, Any]], *, top_ks: tuple[int, ...]) -> dict[str, float]:
    if not case_results:
        return {}
    output: dict[str, float] = {}
    for k in sorted({int(item) for item in top_ks if int(item) > 0}):
        for name in (f"column_recall@{k}", f"column_precision@{k}"):
            output[name] = mean(float(case["scores"][name]) for case in case_results)
    return output


def _recall(expected: set[str], retrieved: set[str]) -> float:
    if not expected:
        return 1.0
    return len(expected & retrieved) / len(expected)


def _precision(expected: set[str], retrieved: set[str]) -> float:
    if not retrieved:
        return 0.0
    return len(expected & retrieved) / len(retrieved)
