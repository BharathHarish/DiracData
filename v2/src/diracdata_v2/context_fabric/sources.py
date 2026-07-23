"""Normalize SQL-bearing context-fabric sources."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from diracdata_v2.context_fabric.contracts import (
    KnowledgeSourceType,
    KnowledgeTrustLevel,
    NormalizedKnowledgeCase,
    NormalizedKnowledgeCorpus,
)
from diracdata_v2.semantic_catalog.sql_analysis import analyze_sql_references


def table_columns_from_metadata(metadata_descriptions: dict[str, Any]) -> dict[str, list[str]]:
    """Extract a table->columns map from metadata descriptions."""

    columns = metadata_descriptions.get("columns", {})
    if not isinstance(columns, dict):
        return {}
    output: dict[str, list[str]] = {}
    for table_name, table_columns in columns.items():
        if not isinstance(table_columns, dict):
            continue
        output[str(table_name)] = sorted(str(column) for column in table_columns.keys())
    return output


def build_normalized_corpus(
    *,
    table_columns: dict[str, list[str]],
    query_history_path: Path | None = None,
    nl_sql_pair_paths: Iterable[Path] = (),
    sql_library_document: dict[str, Any] | None = None,
    experience_object_store: Any | None = None,
    experience_schema: str | None = None,
    experience_prefix: str = "experience/candidates",
    query_history_limit: int | None = None,
    nl_sql_pair_limit: int | None = None,
    self_play_limit: int | None = None,
    experience_limit: int | None = None,
) -> NormalizedKnowledgeCorpus:
    cases: list[NormalizedKnowledgeCase] = []
    if query_history_path is not None:
        cases.extend(
            normalize_query_history(
                query_history_path=query_history_path,
                table_columns=table_columns,
                limit=query_history_limit,
            )
        )
    cases.extend(
        normalize_nl_sql_pairs(
            pair_paths=tuple(nl_sql_pair_paths),
            table_columns=table_columns,
            limit=nl_sql_pair_limit,
        )
    )
    if sql_library_document is not None:
        cases.extend(
            normalize_sql_library_entries(
                sql_library_document=sql_library_document,
                table_columns=table_columns,
                limit=self_play_limit,
            )
        )
    if experience_object_store is not None and experience_schema:
        cases.extend(
            normalize_experience_candidates(
                object_store=experience_object_store,
                schema=experience_schema,
                table_columns=table_columns,
                prefix=experience_prefix,
                limit=experience_limit,
            )
        )
    return NormalizedKnowledgeCorpus(
        cases=tuple(_dedupe_cases(cases)),
        metadata={"artifact_type": "normalized_knowledge_corpus", "version": 1},
    )


def normalize_sql_library_entries(
    *,
    sql_library_document: dict[str, Any],
    table_columns: dict[str, list[str]],
    limit: int | None = None,
    sources: tuple[str, ...] = ("self_play",),
    review_statuses: tuple[str, ...] = ("approved",),
    validation_statuses: tuple[str, ...] = ("passed",),
) -> list[NormalizedKnowledgeCase]:
    """Normalize approved SQL library entries into reusable synthetic cases."""

    entries = sql_library_document.get("entries", {})
    if not isinstance(entries, dict):
        return []
    source_filter = set(sources)
    review_filter = set(review_statuses)
    validation_filter = set(validation_statuses)
    cases: list[NormalizedKnowledgeCase] = []
    for entry_id, entry in sorted(entries.items()):
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "")
        if source_filter and source not in source_filter:
            continue
        review_status = str(entry.get("review_status") or "")
        if review_filter and review_status not in review_filter:
            continue
        validation = entry.get("validation") if isinstance(entry.get("validation"), dict) else {}
        validation_status = str(validation.get("status") or "")
        if validation_filter and validation_status not in validation_filter:
            continue
        sql = _one_line_sql(str(entry.get("sql") or ""))
        if not sql:
            continue
        analysis = analyze_sql_references(sql, table_columns)
        tables = _valid_tables(_strings(entry.get("tables")), table_columns=table_columns) or analysis.tables
        columns = _valid_columns(_strings(entry.get("columns")), table_columns=table_columns) or analysis.columns
        if not tables or not columns:
            continue
        case = NormalizedKnowledgeCase(
            case_id=_source_case_id("self_play", str(entry_id), sql),
            source_type=KnowledgeSourceType.SELF_PLAY,
            trust_level=KnowledgeTrustLevel.SYNTHETIC,
            question=_first_non_empty(
                entry.get("canonical_question"),
                entry.get("template"),
                f"Self-play SQL pattern for {', '.join(columns[:3])}",
            ),
            sql=sql,
            tables=tuple(sorted(tables)),
            columns=tuple(sorted(columns)),
            join_edges=tuple(edge.to_dict() for edge in analysis.join_pairs),
            review_status=review_status,
            provenance={
                "source_entry_id": str(entry_id),
                "source": source,
                "validation_status": validation_status,
                "reviewed_by": entry.get("reviewed_by"),
                "reviewed_at": entry.get("reviewed_at"),
            },
        )
        cases.append(case)
        if limit is not None and len(cases) >= max(0, limit):
            break
    return cases


def normalize_query_history(
    *,
    query_history_path: Path,
    table_columns: dict[str, list[str]],
    limit: int | None = None,
) -> list[NormalizedKnowledgeCase]:
    if not query_history_path.exists():
        return []
    cases: list[NormalizedKnowledgeCase] = []
    with query_history_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if not _is_successful_history_row(row):
                continue
            sql = _one_line_sql(_first_value(row, "statement_text", "sql", "query", "query_text"))
            if not sql:
                continue
            case = _case_from_sql(
                case_id=_source_case_id(
                    "history",
                    _first_value(row, "statement_id", "query_id", "id"),
                    sql,
                ),
                source_type=KnowledgeSourceType.QUERY_HISTORY,
                trust_level=KnowledgeTrustLevel.OBSERVED,
                sql=sql,
                question=_first_value(row, "question", "nl_query", "natural_language_query"),
                review_status="observed",
                table_columns=table_columns,
                provenance={
                    "path": str(query_history_path),
                    "statement_id": _first_value(row, "statement_id", "query_id", "id"),
                    "execution_status": _first_value(row, "execution_status", "status"),
                    "statement_type": _first_value(row, "statement_type"),
                    "query_source": _first_value(row, "query_source"),
                },
            )
            if case.tables and case.columns:
                cases.append(case)
            if limit is not None and len(cases) >= max(0, limit):
                break
    return cases


def normalize_nl_sql_pairs(
    *,
    pair_paths: Iterable[Path],
    table_columns: dict[str, list[str]],
    limit: int | None = None,
    default_review_status: str = "approved",
) -> list[NormalizedKnowledgeCase]:
    cases: list[NormalizedKnowledgeCase] = []
    for pair_path in pair_paths:
        if not pair_path.exists():
            continue
        with pair_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                question = _first_value(row, "nl_query", "question", "natural_language_query")
                sql = _one_line_sql(_first_value(row, "sql", "statement_text", "query"))
                if not question or not sql:
                    continue
                review_status = _first_value(row, "review_status") or default_review_status
                analysis = analyze_sql_references(sql, table_columns)
                explicit_tables = _valid_tables(
                    _split_refs(_first_value(row, "tables_used", "tables", "expected_tables")),
                    table_columns=table_columns,
                )
                explicit_columns = _valid_columns(
                    _split_refs(_first_value(row, "columns_used", "columns", "expected_columns")),
                    table_columns=table_columns,
                )
                join_edges = _join_edges_from_field(
                    _first_value(row, "join_edges", "expected_join_edges"),
                    table_columns=table_columns,
                ) or tuple(edge.to_dict() for edge in analysis.join_pairs)
                source_id = _first_value(row, "case_id", "id", "source_case_id")
                cases.append(
                    NormalizedKnowledgeCase(
                        case_id=_source_case_id("gold", source_id, question + sql),
                        source_type=KnowledgeSourceType.GOLD_NL_SQL,
                        trust_level=_trust_for_review_status(review_status, default=KnowledgeTrustLevel.GOLD),
                        question=question,
                        sql=sql,
                        tables=tuple(sorted(explicit_tables or analysis.tables)),
                        columns=tuple(sorted(explicit_columns or analysis.columns)),
                        join_edges=tuple(sorted(join_edges, key=lambda item: str(item.get("sql_condition") or ""))),
                        review_status=review_status,
                        provenance={
                            "path": str(pair_path),
                            "source_case_id": source_id,
                            "category": _first_value(row, "category"),
                            "difficulty": _first_value(row, "difficulty"),
                            "notes": _first_value(row, "notes"),
                        },
                    )
                )
                if limit is not None and len(cases) >= max(0, limit):
                    return cases
    return cases


def normalize_experience_candidates(
    *,
    object_store: Any,
    schema: str,
    table_columns: dict[str, list[str]],
    prefix: str = "experience/candidates",
    limit: int | None = None,
    review_statuses: tuple[str, ...] = ("approved", "needs_review"),
) -> list[NormalizedKnowledgeCase]:
    cases: list[NormalizedKnowledgeCase] = []
    status_filter = set(review_statuses)
    for key in object_store.list_keys(f"{prefix.strip('/')}/{schema}"):
        try:
            payload = object_store.read_json(key)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(payload, dict):
            continue
        review_status = str(payload.get("review_status") or "")
        if status_filter and review_status not in status_filter:
            continue
        sql = _one_line_sql(str(payload.get("final_sql") or ""))
        question = _one_line(str(payload.get("question") or ""))
        if not sql or not question:
            continue
        analysis = analyze_sql_references(sql, table_columns)
        if not analysis.tables or not analysis.columns:
            continue
        case_id = _source_case_id("experience", str(payload.get("id") or ""), question + sql)
        cases.append(
            NormalizedKnowledgeCase(
                case_id=case_id,
                source_type=KnowledgeSourceType.EXPERIENCE,
                trust_level=_trust_for_review_status(review_status, default=KnowledgeTrustLevel.NEEDS_REVIEW),
                question=question,
                sql=sql,
                tables=analysis.tables,
                columns=analysis.columns,
                join_edges=tuple(edge.to_dict() for edge in analysis.join_pairs),
                review_status=review_status,
                provenance={
                    "object_key": key,
                    "source_trace_id": payload.get("source_trace_id"),
                    "model_profile": payload.get("model_profile"),
                    "workflow": payload.get("workflow"),
                    "created_at": payload.get("created_at"),
                },
            )
        )
        if limit is not None and len(cases) >= max(0, limit):
            break
    return cases


def write_corpus_jsonl(corpus: NormalizedKnowledgeCorpus, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for case in corpus.cases:
            handle.write(json.dumps(case.to_dict(), sort_keys=True) + "\n")
    return path


def _case_from_sql(
    *,
    case_id: str,
    source_type: KnowledgeSourceType,
    trust_level: KnowledgeTrustLevel,
    sql: str,
    question: str,
    review_status: str,
    table_columns: dict[str, list[str]],
    provenance: dict[str, Any],
) -> NormalizedKnowledgeCase:
    analysis = analyze_sql_references(sql, table_columns)
    return NormalizedKnowledgeCase(
        case_id=case_id,
        source_type=source_type,
        trust_level=trust_level,
        question=_one_line(question),
        sql=sql,
        tables=analysis.tables,
        columns=analysis.columns,
        join_edges=tuple(edge.to_dict() for edge in analysis.join_pairs),
        review_status=review_status,
        provenance={key: value for key, value in provenance.items() if value not in {None, ""}},
    )


def _is_successful_history_row(row: dict[str, Any]) -> bool:
    status = _first_value(row, "execution_status", "status").strip().upper()
    if status and status not in {"FINISHED", "SUCCESS", "SUCCEEDED", "OK"}:
        return False
    statement_type = _first_value(row, "statement_type", "query_type").strip().upper()
    if statement_type and statement_type not in {"SELECT", "UNKNOWN", ""}:
        return False
    return True


def _first_value(row: dict[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value is None:
            continue
        clean = _one_line(str(value))
        if clean:
            return clean
    return ""


def _one_line_sql(sql: str) -> str:
    return _one_line(sql).rstrip(";")


def _one_line(value: str) -> str:
    return " ".join(str(value or "").split())


def _source_case_id(prefix: str, source_id: str, fallback: str) -> str:
    clean_source = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(source_id or "").strip()).strip("_")
    if clean_source:
        return f"{prefix}:{clean_source}"
    digest = hashlib.sha1(fallback.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def _split_refs(value: str) -> list[str]:
    clean = str(value or "").strip()
    if not clean:
        return []
    if clean.startswith("["):
        try:
            parsed = json.loads(clean)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            return [_one_line(str(item)) for item in parsed if _one_line(str(item))]
    return [
        _one_line(item)
        for item in re.split(r"[;,]", clean)
        if _one_line(item)
    ]


def _strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_one_line(str(item)) for item in value if _one_line(str(item))]
    if isinstance(value, tuple):
        return [_one_line(str(item)) for item in value if _one_line(str(item))]
    if isinstance(value, str):
        return _split_refs(value)
    return []


def _first_non_empty(*values: Any) -> str:
    for value in values:
        clean = _one_line(str(value or ""))
        if clean:
            return clean
    return ""


def _valid_tables(values: Iterable[str], *, table_columns: dict[str, list[str]]) -> tuple[str, ...]:
    valid = {table.lower(): table for table in table_columns}
    output = []
    for value in values:
        table = valid.get(str(value).strip().lower())
        if table:
            output.append(table)
    return tuple(sorted(set(output)))


def _valid_columns(values: Iterable[str], *, table_columns: dict[str, list[str]]) -> tuple[str, ...]:
    valid = {
        f"{table.lower()}.{column.lower()}": f"{table}.{column}"
        for table, columns in table_columns.items()
        for column in columns
    }
    output = []
    for value in values:
        column = valid.get(str(value).strip().lower())
        if column:
            output.append(column)
    return tuple(sorted(set(output)))


def _join_edges_from_field(value: str, *, table_columns: dict[str, list[str]]) -> tuple[dict[str, Any], ...]:
    edges = []
    valid_columns = set(_valid_columns(_split_join_column_refs(value), table_columns=table_columns))
    for condition in _split_refs(value):
        match = re.match(
            r"^\s*([a-zA-Z_][\w]*\.[a-zA-Z_][\w]*)\s*=\s*([a-zA-Z_][\w]*\.[a-zA-Z_][\w]*)\s*$",
            condition,
        )
        if not match:
            continue
        left, right = match.group(1), match.group(2)
        canonical = _valid_columns((left, right), table_columns=table_columns)
        if len(canonical) != 2 or set(canonical) - valid_columns:
            continue
        ordered = tuple(sorted(canonical))
        left_column, right_column = ordered
        left_table = left_column.split(".", 1)[0]
        right_table = right_column.split(".", 1)[0]
        if left_table == right_table:
            continue
        edges.append(
            {
                "left_column": left_column,
                "right_column": right_column,
                "tables": sorted([left_table, right_table]),
                "sql_condition": f"{left_column} = {right_column}",
            }
        )
    return tuple({edge["sql_condition"]: edge for edge in edges}.values())


def _split_join_column_refs(value: str) -> list[str]:
    refs = []
    for condition in _split_refs(value):
        refs.extend(re.findall(r"[a-zA-Z_][\w]*\.[a-zA-Z_][\w]*", condition))
    return refs


def _trust_for_review_status(
    review_status: str,
    *,
    default: KnowledgeTrustLevel,
) -> KnowledgeTrustLevel:
    normalized = str(review_status or "").strip().lower()
    if normalized == "approved":
        return KnowledgeTrustLevel.APPROVED if default != KnowledgeTrustLevel.GOLD else KnowledgeTrustLevel.GOLD
    if normalized == "gold":
        return KnowledgeTrustLevel.GOLD
    if normalized in {"observed", "history"}:
        return KnowledgeTrustLevel.OBSERVED
    if normalized in {"synthetic", "self_play"}:
        return KnowledgeTrustLevel.SYNTHETIC
    if normalized in {"needs_review", "pending", "review"}:
        return KnowledgeTrustLevel.NEEDS_REVIEW
    return default


def _dedupe_cases(cases: Iterable[NormalizedKnowledgeCase]) -> list[NormalizedKnowledgeCase]:
    seen: set[tuple[str, str, str]] = set()
    output = []
    for case in cases:
        key = (case.source_type.value, case.question.lower(), case.sql.lower())
        if key in seen:
            continue
        seen.add(key)
        output.append(case)
    output.sort(key=lambda item: (item.source_type.value, item.case_id))
    return output
