"""Benchmark utilities for schema-aware context compilation.

The benchmark evaluates whether compiled context contains the schema objects
and join paths required to answer a labeled natural-language question. It is
model-independent and intentionally runs before SQL authoring.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

from diracdata_v2.semantic_catalog import SemanticCatalogCompiler


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    question: str
    category: str
    expected_tables: tuple[str, ...] = ()
    expected_columns: tuple[str, ...] = ()
    expected_join_edges: tuple[str, ...] = ()
    expected_ambiguities: tuple[str, ...] = ()
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "question": self.question,
            "category": self.category,
            "expected_tables": list(self.expected_tables),
            "expected_columns": list(self.expected_columns),
            "expected_join_edges": list(self.expected_join_edges),
            "expected_ambiguities": list(self.expected_ambiguities),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class BenchmarkScores:
    table_recall: float
    column_recall: float
    join_recall: float
    ambiguity_recall: float
    direct_column_recall_at_k: dict[str, float] = field(default_factory=dict)
    expanded_column_recall_at_k: dict[str, float] = field(default_factory=dict)
    missing_tables: tuple[str, ...] = ()
    missing_columns: tuple[str, ...] = ()
    missing_join_edges: tuple[str, ...] = ()
    missing_ambiguities: tuple[str, ...] = ()
    missing_direct_columns_at_k: dict[str, tuple[str, ...]] = field(default_factory=dict)
    missing_expanded_columns_at_k: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_recall": self.table_recall,
            "column_recall": self.column_recall,
            "join_recall": self.join_recall,
            "ambiguity_recall": self.ambiguity_recall,
            "direct_column_recall_at_k": self.direct_column_recall_at_k,
            "expanded_column_recall_at_k": self.expanded_column_recall_at_k,
            "missing_tables": list(self.missing_tables),
            "missing_columns": list(self.missing_columns),
            "missing_join_edges": list(self.missing_join_edges),
            "missing_ambiguities": list(self.missing_ambiguities),
            "missing_direct_columns_at_k": {
                str(key): list(value)
                for key, value in self.missing_direct_columns_at_k.items()
            },
            "missing_expanded_columns_at_k": {
                str(key): list(value)
                for key, value in self.missing_expanded_columns_at_k.items()
            },
        }


@dataclass(frozen=True)
class BenchmarkReport:
    benchmark_path: str
    semantic_catalog_path: str
    case_count: int
    aggregate_scores: dict[str, float]
    cases: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_path": self.benchmark_path,
            "semantic_catalog_path": self.semantic_catalog_path,
            "case_count": self.case_count,
            "aggregate_scores": self.aggregate_scores,
            "cases": list(self.cases),
        }


def load_benchmark_cases(path: Path) -> list[BenchmarkCase]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(handle)
        cases = []
        for row in rows:
            case_id = str(row.get("case_id") or row.get("history_id") or "").strip()
            question = str(row.get("question") or row.get("nl_query") or "").strip()
            if not case_id or not question:
                continue
            cases.append(
                BenchmarkCase(
                    case_id=case_id,
                    question=question,
                    category=str(row.get("category") or "").strip(),
                    expected_tables=tuple(_split_list(row.get("expected_tables") or row.get("tables_used"))),
                    expected_columns=tuple(_split_list(row.get("expected_columns") or row.get("columns_used"))),
                    expected_join_edges=tuple(
                        _normalize_join_edge(item)
                        for item in _split_list(row.get("expected_join_edges") or row.get("join_edges"))
                    ),
                    expected_ambiguities=tuple(_split_list(row.get("expected_ambiguities"))),
                    notes=str(row.get("notes") or "").strip(),
                )
            )
    return cases


def validate_benchmark_cases(cases: list[BenchmarkCase], metadata_descriptions: dict[str, Any]) -> list[str]:
    tables = set(map(str, metadata_descriptions.get("tables", {}).keys()))
    columns = {
        f"{table}.{column}"
        for table, table_columns in metadata_descriptions.get("columns", {}).items()
        for column in table_columns.keys()
    }
    errors = []
    seen_ids: set[str] = set()
    for case in cases:
        if case.case_id in seen_ids:
            errors.append(f"{case.case_id}: duplicate case_id")
        seen_ids.add(case.case_id)
        for table in case.expected_tables:
            if table not in tables:
                errors.append(f"{case.case_id}: unknown expected table {table}")
        for column in case.expected_columns:
            if column not in columns:
                errors.append(f"{case.case_id}: unknown expected column {column}")
        for edge in case.expected_join_edges:
            for ref in _join_edge_refs(edge):
                if ref not in columns:
                    errors.append(f"{case.case_id}: unknown expected join ref {ref}")
    return errors


def evaluate_semantic_catalog_baseline(
    *,
    cases: list[BenchmarkCase],
    semantic_catalog_path: Path,
    max_cards: int = 36,
    max_patterns: int = 8,
    recall_ks: tuple[int, ...] = (10, 20, 40),
) -> BenchmarkReport:
    compiler = SemanticCatalogCompiler.from_file(semantic_catalog_path)
    compile_card_limit = max((max_cards, *recall_ks), default=max_cards)
    case_results: list[dict[str, Any]] = []
    for case in cases:
        packet = compiler.compile(case.question, max_cards=compile_card_limit, max_patterns=max_patterns).to_dict()
        scores = evaluate_compiled_context(case=case, packet=packet, recall_ks=recall_ks)
        case_results.append(
            {
                "case": case.to_dict(),
                "scores": scores.to_dict(),
                "retrieval": packet.get("retrieval", {}),
                "candidate_columns": sorted(_packet_columns(packet)),
                "candidate_tables": sorted(_packet_tables(packet)),
                "join_edges": sorted(_packet_join_edges(packet)),
                "unresolved_terms": packet.get("unresolved_terms", []),
            }
        )
    return BenchmarkReport(
        benchmark_path="",
        semantic_catalog_path=str(semantic_catalog_path),
        case_count=len(cases),
        aggregate_scores=_aggregate_scores(case_results),
        cases=tuple(case_results),
    )


def evaluate_compiled_context(
    *,
    case: BenchmarkCase,
    packet: dict[str, Any],
    recall_ks: tuple[int, ...] = (10, 20, 40),
) -> BenchmarkScores:
    packet_tables = _packet_tables(packet)
    packet_columns = _packet_columns(packet)
    packet_joins = _packet_join_edges(packet)
    packet_ambiguities = _packet_ambiguity_terms(packet)

    expected_tables = set(case.expected_tables)
    expected_columns = set(case.expected_columns)
    expected_joins = set(case.expected_join_edges)
    expected_ambiguities = {item.lower() for item in case.expected_ambiguities}

    missing_tables = tuple(sorted(expected_tables - packet_tables))
    missing_columns = tuple(sorted(expected_columns - packet_columns))
    missing_joins = tuple(sorted(expected_joins - packet_joins))
    missing_ambiguities = tuple(sorted(expected_ambiguities - packet_ambiguities))
    direct_at_k: dict[str, float] = {}
    expanded_at_k: dict[str, float] = {}
    missing_direct_at_k: dict[str, tuple[str, ...]] = {}
    missing_expanded_at_k: dict[str, tuple[str, ...]] = {}
    for value in sorted({int(k) for k in recall_ks if int(k) > 0}):
        key = str(value)
        direct_columns = _candidate_card_columns(packet, limit=value)
        expanded_columns = direct_columns | _pattern_columns(packet)
        direct_at_k[key] = _recall(expected_columns, direct_columns)
        expanded_at_k[key] = _recall(expected_columns, expanded_columns)
        missing_direct_at_k[key] = tuple(sorted(expected_columns - direct_columns))
        missing_expanded_at_k[key] = tuple(sorted(expected_columns - expanded_columns))

    return BenchmarkScores(
        table_recall=_recall(expected_tables, packet_tables),
        column_recall=_recall(expected_columns, packet_columns),
        join_recall=_recall(expected_joins, packet_joins),
        ambiguity_recall=_recall(expected_ambiguities, packet_ambiguities),
        direct_column_recall_at_k=direct_at_k,
        expanded_column_recall_at_k=expanded_at_k,
        missing_tables=missing_tables,
        missing_columns=missing_columns,
        missing_join_edges=missing_joins,
        missing_ambiguities=missing_ambiguities,
        missing_direct_columns_at_k=missing_direct_at_k,
        missing_expanded_columns_at_k=missing_expanded_at_k,
    )


def write_report(report: BenchmarkReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "baseline_report.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    samples_path = output_dir / "compiled_context_samples.jsonl"
    with samples_path.open("w", encoding="utf-8") as handle:
        for case in report.cases:
            handle.write(json.dumps(case, sort_keys=True) + "\n")
    return path


def _packet_tables(packet: dict[str, Any]) -> set[str]:
    tables: set[str] = set()
    tables.update(map(str, packet.get("retrieval", {}).get("required_tables", [])))
    for card in packet.get("candidate_cards", []):
        sql_ref = card.get("sql_ref")
        if isinstance(sql_ref, str) and sql_ref:
            tables.add(sql_ref.split(".", 1)[0])
        metadata = card.get("metadata", {})
        if metadata.get("table_name"):
            tables.add(str(metadata["table_name"]))
    for pattern in packet.get("sql_patterns", []):
        tables.update(map(str, pattern.get("tables", [])))
        for column in pattern.get("columns", []):
            if "." in str(column):
                tables.add(str(column).split(".", 1)[0])
    for edge in packet.get("join_edges", []):
        tables.update(map(str, edge.get("tables", [])))
    return {table for table in tables if table}


def _packet_columns(packet: dict[str, Any]) -> set[str]:
    columns: set[str] = set()
    columns.update(_candidate_card_columns(packet))
    columns.update(_pattern_columns(packet))
    for pattern in packet.get("sql_patterns", []):
        columns.update(str(column) for column in pattern.get("columns", []) if "." in str(column))
    for edge in packet.get("join_edges", []):
        columns.update(_join_edge_refs(edge.get("sql_condition", "")))
    return columns


def _candidate_card_columns(packet: dict[str, Any], limit: int | None = None) -> set[str]:
    columns: set[str] = set()
    cards = packet.get("candidate_cards", [])
    if limit is not None:
        cards = cards[:limit]
    for card in cards:
        if card.get("kind") != "column":
            continue
        sql_ref = card.get("sql_ref")
        if isinstance(sql_ref, str) and "." in sql_ref:
            columns.add(sql_ref)
    return columns


def _pattern_columns(packet: dict[str, Any]) -> set[str]:
    columns: set[str] = set()
    for pattern in packet.get("sql_patterns", []):
        columns.update(str(column) for column in pattern.get("columns", []) if "." in str(column))
    return columns


def _packet_join_edges(packet: dict[str, Any]) -> set[str]:
    edges = set()
    for edge in packet.get("join_edges", []):
        condition = edge.get("sql_condition")
        if condition:
            edges.add(_normalize_join_edge(str(condition)))
    return edges


def _packet_ambiguity_terms(packet: dict[str, Any]) -> set[str]:
    terms = set()
    for item in packet.get("unresolved_terms", []):
        term = str(item.get("term") or "").strip().lower()
        if term:
            terms.add(term)
    return terms


def _aggregate_scores(case_results: list[dict[str, Any]]) -> dict[str, float]:
    if not case_results:
        return {
            "table_recall": 0.0,
            "column_recall": 0.0,
            "join_recall": 0.0,
            "ambiguity_recall": 0.0,
        }
    score_keys = ["table_recall", "column_recall", "join_recall", "ambiguity_recall"]
    aggregate = {
        key: mean(float(case["scores"][key]) for case in case_results)
        for key in score_keys
    }
    for namespace in ("direct_column_recall_at_k", "expanded_column_recall_at_k"):
        keys = sorted({
            str(k)
            for case in case_results
            for k in case["scores"].get(namespace, {}).keys()
        }, key=lambda item: int(item))
        for key in keys:
            aggregate[f"{namespace.replace('_at_k', '')}@{key}"] = mean(
                float(case["scores"].get(namespace, {}).get(key, 0.0))
                for case in case_results
            )
    return aggregate


def _recall(expected: set[str], actual: set[str]) -> float:
    if not expected:
        return 1.0
    return len(expected & actual) / len(expected)


def _split_list(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in text.split(";") if item.strip()]


def _normalize_join_edge(value: str) -> str:
    refs = _join_edge_refs(value)
    if len(refs) != 2:
        return " ".join(str(value or "").split())
    left, right = sorted(refs)
    return f"{left} = {right}"


def _join_edge_refs(value: Any) -> list[str]:
    text = str(value or "")
    if "=" not in text:
        return []
    left, right = text.split("=", 1)
    refs = [left.strip(), right.strip()]
    return [ref for ref in refs if "." in ref]
