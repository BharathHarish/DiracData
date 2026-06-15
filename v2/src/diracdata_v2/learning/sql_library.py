"""Build a compact SQL library from query history and safe self-play probes."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from diracdata_v2.semantic_catalog.sql_analysis import analyze_sql_references


class PatternGenerator(Protocol):
    """LLM adapter used to translate SQL templates into NL pattern metadata."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


@dataclass(frozen=True)
class SQLLibraryBuildResult:
    document: dict[str, Any]
    local_path: Path
    object_key: str | None = None


class SQLLibraryBuilder:
    """Create one simple SQL library document.

    Query history contributes observed patterns. Self-play contributes simple,
    validated column patterns for schema leaves not covered by query history.
    """

    def __init__(
        self,
        *,
        pattern_generator: PatternGenerator | None = None,
        pattern_batch_size: int = 20,
        pattern_limit: int = 80,
    ) -> None:
        self._pattern_generator = pattern_generator
        self._pattern_batch_size = pattern_batch_size
        self._pattern_limit = pattern_limit

    def build(
        self,
        *,
        schema_graph: dict[str, Any],
        query_history_path: Path,
        data_root: Path,
        catalog: str,
        database: str,
        schema: str,
        run_id: str,
        output_dir: Path,
        object_store: Any | None = None,
        object_prefix: str = "v2/learning/artifacts",
        history_limit: int = 80,
        pattern_batch_size: int | None = None,
        pattern_limit: int | None = None,
        nl_sql_pair_paths: tuple[Path, ...] = (),
        nl_sql_pair_limit: int | None = None,
        nl_sql_pair_review_status: str = "approved",
    ) -> SQLLibraryBuildResult:
        table_columns = _table_columns(schema_graph)
        covered = query_history_coverage(
            query_history_path=query_history_path,
            table_columns=table_columns,
            nl_sql_pair_paths=nl_sql_pair_paths,
        )
        entries: dict[str, dict[str, Any]] = {}
        entries.update(
            mine_history_templates(
                query_history_path=query_history_path,
                table_columns=table_columns,
                limit=history_limit,
            )
        )
        entries.update(
            mine_nl_sql_pair_templates(
                pair_paths=nl_sql_pair_paths,
                table_columns=table_columns,
                limit=nl_sql_pair_limit,
                review_status=nl_sql_pair_review_status,
            )
        )
        entries.update(
            build_self_play_templates(
                schema_graph=schema_graph,
                data_root=data_root,
                schema=schema,
                uncovered_columns=covered["columns_missing"],
            )
        )
        patterns = build_sql_patterns(
            entries=entries,
            schema_graph=schema_graph,
            generator=self._pattern_generator,
            batch_size=pattern_batch_size or self._pattern_batch_size,
            limit=pattern_limit or self._pattern_limit,
        )

        document = {
            "version": 1,
            "artifact_type": "sql_library",
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "scope": {"catalog": catalog, "database": database, "schema": schema},
            "coverage": covered,
            "entries": entries,
            "patterns": patterns,
        }

        output_dir.mkdir(parents=True, exist_ok=True)
        local_path = output_dir / "sql_library.json"
        local_path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")

        object_key = None
        if object_store is not None:
            object_key = f"{object_prefix.strip('/')}/{run_id}/sql_library.json"
            object_store.write_json(object_key, document)
        return SQLLibraryBuildResult(document=document, local_path=local_path, object_key=object_key)


def query_history_coverage(
    *,
    query_history_path: Path,
    table_columns: dict[str, list[str]],
    nl_sql_pair_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    table_mentions: dict[str, int] = {table: 0 for table in table_columns}
    column_mentions: dict[str, int] = {
        f"{table}.{column}": 0
        for table, columns in table_columns.items()
        for column in columns
    }
    successful_queries = 0
    for sql in _successful_sql(query_history_path):
        successful_queries += 1
        analysis = analyze_sql_references(sql, table_columns)
        for table in analysis.tables:
            table_mentions[table] = table_mentions.get(table, 0) + 1
        for column_ref in analysis.columns:
            column_mentions[column_ref] = column_mentions.get(column_ref, 0) + 1
    trusted_pair_queries = 0
    for row in _nl_sql_pair_rows(nl_sql_pair_paths):
        sql = _row_sql(row)
        if not sql:
            continue
        trusted_pair_queries += 1
        analysis = analyze_sql_references(sql, table_columns)
        tables = _row_refs(row, table_fields=("tables", "tables_used", "expected_tables")) or list(analysis.tables)
        columns = _valid_column_refs(
            _row_refs(row, table_fields=("columns", "columns_used", "expected_columns")),
            table_columns=table_columns,
        ) or list(analysis.columns)
        for table in tables:
            if table in table_columns:
                table_mentions[table] = table_mentions.get(table, 0) + 1
        for column_ref in columns:
            column_mentions[column_ref] = column_mentions.get(column_ref, 0) + 1

    mentioned_tables = sorted(table for table, count in table_mentions.items() if count)
    mentioned_columns = sorted(column for column, count in column_mentions.items() if count)
    all_columns = sorted(column_mentions)
    return {
        "successful_queries": successful_queries,
        "trusted_pair_queries": trusted_pair_queries,
        "tables_total": len(table_columns),
        "tables_covered": mentioned_tables,
        "tables_missing": sorted(set(table_columns) - set(mentioned_tables)),
        "columns_total": len(all_columns),
        "columns_covered": mentioned_columns,
        "columns_missing": sorted(set(all_columns) - set(mentioned_columns)),
        "table_mentions": table_mentions,
        "column_mentions": column_mentions,
    }


def mine_history_templates(
    *,
    query_history_path: Path,
    table_columns: dict[str, list[str]],
    limit: int,
) -> dict[str, dict[str, Any]]:
    templates: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for sql in _successful_sql(query_history_path):
        normalized = _normalize_sql(sql)
        if normalized in seen:
            continue
        seen.add(normalized)
        analysis = analyze_sql_references(sql, table_columns)
        tables = list(analysis.tables)
        columns = list(analysis.columns)
        if not tables or not columns:
            continue
        key = _history_key(tables=tables, columns=columns, normalized_sql=normalized)
        templates[key] = {
            "template": _template_name(tables=tables, columns=columns),
            "sql": normalized,
            "source": "query_history",
            "review_status": "observed",
            "tables": tables,
            "columns": columns,
            "join_edges": [pair.to_dict() for pair in analysis.join_pairs],
            "analysis": {"parser": analysis.parser},
        }
        if len(templates) >= limit:
            break
    return templates


def mine_nl_sql_pair_templates(
    *,
    pair_paths: tuple[Path, ...],
    table_columns: dict[str, list[str]],
    limit: int | None,
    review_status: str = "approved",
) -> dict[str, dict[str, Any]]:
    """Turn trusted NL-SQL pairs into reusable SQL library entries."""

    templates: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for row in _nl_sql_pair_rows(pair_paths):
        question = _row_question(row)
        sql = _row_sql(row)
        if not question or not sql:
            continue
        normalized = _normalize_sql(sql)
        dedupe_key = f"{question.strip().lower()}|{normalized}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        analysis = analyze_sql_references(sql, table_columns)
        tables = _valid_tables(
            _row_refs(row, table_fields=("tables", "tables_used", "expected_tables")),
            table_columns=table_columns,
        ) or list(analysis.tables)
        columns = _valid_column_refs(
            _row_refs(row, table_fields=("columns", "columns_used", "expected_columns")),
            table_columns=table_columns,
        ) or list(analysis.columns)
        if not tables or not columns:
            continue
        join_edges = _row_join_edges(row, table_columns=table_columns) or [
            pair.to_dict() for pair in analysis.join_pairs
        ]
        source_id = _row_identifier(row) or hashlib.sha1(dedupe_key.encode("utf-8")).hexdigest()[:12]
        key = _nl_sql_pair_key(source_id=source_id, question=question, normalized_sql=normalized)
        templates[key] = {
            "template": _one_line(row.get("template") or row.get("source_template")) or _template_name(tables=tables, columns=columns),
            "sql": normalized,
            "source": "nl_sql_pair",
            "review_status": _one_line(row.get("review_status")) or review_status,
            "tables": tables,
            "columns": columns,
            "join_edges": join_edges,
            "canonical_question": question,
            "paraphrases": _strings(row.get("paraphrases")),
            "source_case_id": source_id,
            "source_category": _one_line(row.get("category")),
            "source_difficulty": _one_line(row.get("difficulty")),
            "source_notes": _one_line(row.get("notes")),
            "analysis": {"parser": analysis.parser},
        }
        if limit is not None and len(templates) >= max(0, limit):
            break
    return templates


def build_self_play_templates(
    *,
    schema_graph: dict[str, Any],
    data_root: Path,
    schema: str,
    uncovered_columns: list[str],
) -> dict[str, dict[str, Any]]:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("self-play SQL validation requires duckdb") from exc

    con = duckdb.connect(":memory:")
    nodes = {node["id"]: node for node in schema_graph.get("nodes", [])}
    entries: dict[str, dict[str, Any]] = {}
    for column_ref in uncovered_columns:
        table, column = column_ref.split(".", 1)
        node = nodes.get(f"column:{column_ref}", {})
        role = str(node.get("metadata", {}).get("role") or "unknown")
        sql = _self_play_sql(table=table, column=column, role=role, data_root=data_root, schema=schema)
        validation = _validate_sql(con, sql)
        key = f"self_play:{table}.{column}"
        entries[key] = {
            "template": f"Explore {table}.{column}",
            "sql": sql,
            "source": "self_play",
            "review_status": "needs_review",
            "tables": [table],
            "columns": [column_ref],
            "role": role,
            "validation": validation,
        }
    return entries


def build_sql_patterns(
    *,
    entries: dict[str, dict[str, Any]],
    schema_graph: dict[str, Any],
    generator: PatternGenerator | None,
    batch_size: int,
    limit: int,
) -> dict[str, dict[str, Any]]:
    """Create NL-searchable SQL patterns from SQL library entries."""
    pattern_entries = {
        entry_id: entry
        for entry_id, entry in entries.items()
        if entry.get("source") in {"query_history", "nl_sql_pair"}
    }
    selected = list(pattern_entries.items())[: max(0, limit)]
    if not selected:
        return {}

    context_by_ref = _schema_context_by_ref(schema_graph)
    patterns: dict[str, dict[str, Any]] = {}
    if generator is not None:
        for batch in _batches(selected, max(1, batch_size)):
            generated = _generate_patterns_batch(
                batch=batch,
                context_by_ref=context_by_ref,
                generator=generator,
            )
            for pattern in generated:
                normalized = _normalize_pattern(pattern, entries=pattern_entries)
                if normalized is not None:
                    patterns[normalized["id"]] = normalized
    for entry_id, entry in selected:
        pattern_id = f"pattern:{entry_id}"
        if pattern_id in patterns:
            continue
        patterns[pattern_id] = _heuristic_pattern(entry_id=entry_id, entry=entry)
    return dict(sorted(patterns.items()))


def _generate_patterns_batch(
    *,
    batch: list[tuple[str, dict[str, Any]]],
    context_by_ref: dict[str, str],
    generator: PatternGenerator,
) -> list[dict[str, Any]]:
    payload = []
    for entry_id, entry in batch:
        refs = list(map(str, entry.get("tables", []))) + list(map(str, entry.get("columns", [])))
        payload.append(
            {
                "entry_id": entry_id,
                "canonical_question": entry.get("canonical_question"),
                "paraphrases": entry.get("paraphrases", []),
                "tables": entry.get("tables", []),
                "columns": entry.get("columns", []),
                "schema_meanings": {
                    ref: context_by_ref[ref]
                    for ref in refs
                    if ref in context_by_ref
                },
                "sql_template": entry.get("sql", ""),
            }
        )
    prompt = _pattern_prompt().replace(
        "{{sql_entries_json}}",
        json.dumps(payload, indent=2, sort_keys=True),
    )
    try:
        text = generator.complete(
            [
                {
                    "role": "system",
                    "content": "You translate observed SQL templates into compact NL2SQL pattern metadata. Return only valid JSON.",
                },
                {"role": "user", "content": prompt},
            ]
        )
        parsed = _loads_json_object(text)
    except Exception:
        return []
    patterns = parsed.get("patterns") if isinstance(parsed, dict) else None
    return patterns if isinstance(patterns, list) else []


def _pattern_prompt() -> str:
    return """Create reusable NL-to-SQL pattern metadata from observed SQL templates.

Requirements:
- Return only valid JSON.
- Do not invent tables, columns, values, or business definitions not supported by the SQL/template evidence.
- Use business-friendly natural language.
- Keep canonical_question under 30 words.
- Generate paraphrases that preserve the same grain, filters, and measures.
- The sql_template must stay close to the observed SQL and keep placeholders such as {{string}}, {{number}}, {{date}}, {{timestamp}}.

JSON shape:
{
  "patterns": [
    {
      "entry_id": "...",
      "canonical_question": "...",
      "paraphrases": ["...", "..."],
      "intent_signature": {
        "grain": "...",
        "measure": "...",
        "filters": ["..."],
        "dimensions": ["..."],
        "time_window": "..."
      },
      "summary": "...",
      "assumptions": ["..."]
    }
  ]
}

SQL entries:
{{sql_entries_json}}
"""


def _normalize_pattern(
    pattern: dict[str, Any],
    *,
    entries: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    entry_id = str(pattern.get("entry_id") or "")
    entry = entries.get(entry_id)
    if entry is None:
        return None
    pattern_id = f"pattern:{entry_id}"
    return {
        "id": pattern_id,
        "entry_id": entry_id,
        "source": entry.get("source") or "query_history",
        "review_status": entry.get("review_status", "observed"),
        "canonical_question": _one_line(pattern.get("canonical_question"))
        or _one_line(entry.get("canonical_question"))
        or _heuristic_question(entry),
        "paraphrases": (_strings(pattern.get("paraphrases")) or _strings(entry.get("paraphrases")))[:5],
        "intent_signature": _intent_signature(pattern.get("intent_signature"), entry=entry),
        "summary": _one_line(pattern.get("summary")),
        "assumptions": _strings(pattern.get("assumptions"))[:5],
        "tables": list(entry.get("tables", [])),
        "columns": list(entry.get("columns", [])),
        "sql_template": str(entry.get("sql") or ""),
        "source_entry_ids": [entry_id],
    }


def _heuristic_pattern(*, entry_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"pattern:{entry_id}",
        "entry_id": entry_id,
        "source": entry.get("source") or "query_history",
        "review_status": entry.get("review_status", "observed"),
        "canonical_question": _one_line(entry.get("canonical_question")) or _heuristic_question(entry),
        "paraphrases": _strings(entry.get("paraphrases")) or [_heuristic_paraphrase(entry)],
        "intent_signature": _intent_signature({}, entry=entry),
        "summary": _one_line(entry.get("template")) or _one_line(entry.get("canonical_question")),
        "assumptions": _strings(entry.get("assumptions")),
        "tables": list(entry.get("tables", [])),
        "columns": list(entry.get("columns", [])),
        "sql_template": str(entry.get("sql") or ""),
        "source_entry_ids": [entry_id],
    }


def _heuristic_question(entry: dict[str, Any]) -> str:
    tables = ", ".join(map(str, entry.get("tables", [])[:4]))
    columns = [str(column).split(".")[-1] for column in entry.get("columns", [])[:4]]
    if columns:
        return f"Analyze {', '.join(columns)} using {tables}."
    return f"Analyze data using {tables}."


def _heuristic_paraphrase(entry: dict[str, Any]) -> str:
    tables = " and ".join(map(str, entry.get("tables", [])[:3]))
    return f"Use the observed {tables} query pattern."


def _intent_signature(value: Any, *, entry: dict[str, Any]) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    columns = list(map(str, entry.get("columns", [])))
    return {
        "grain": _one_line(source.get("grain")),
        "measure": _one_line(source.get("measure")),
        "filters": _strings(source.get("filters")) or _column_names(columns[:3]),
        "dimensions": _strings(source.get("dimensions")) or _column_names(columns[3:8]),
        "time_window": _one_line(source.get("time_window")),
    }


def _schema_context_by_ref(schema_graph: dict[str, Any]) -> dict[str, str]:
    context: dict[str, str] = {}
    for node in schema_graph.get("nodes", []):
        sql_ref = node.get("sql_ref")
        if not sql_ref:
            continue
        text = " ".join(
            item
            for item in [
                str(node.get("name") or ""),
                str(node.get("description") or ""),
                str(node.get("grain") or ""),
                " ".join(map(str, node.get("aliases", []))),
            ]
            if item
        )
        context[str(sql_ref)] = text
    return context


def _batches(
    items: list[tuple[str, dict[str, Any]]],
    size: int,
) -> list[list[tuple[str, dict[str, Any]]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_one_line(item) for item in value if _one_line(item)]
    if isinstance(value, str) and value.strip():
        return [_one_line(value)]
    return []


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _column_names(columns: list[str]) -> list[str]:
    return [column.split(".")[-1] for column in columns if column]


def _loads_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            raise
        payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("expected JSON object")
    return payload


def _self_play_sql(*, table: str, column: str, role: str, data_root: Path, schema: str) -> str:
    parquet_path = _parquet_path(data_root=data_root, schema=schema, table=table).as_posix()
    source = f"read_parquet('{parquet_path}')"
    if role == "measure":
        return (
            f"SELECT COUNT(*) AS row_count, COUNT({column}) AS non_null_count, "
            f"MIN({column}) AS min_value, AVG({column}) AS avg_value, "
            f"MAX({column}) AS max_value, SUM({column}) AS total_value "
            f"FROM {source}"
        )
    if role == "time":
        return (
            f"SELECT date_trunc('month', {column}) AS month, COUNT(*) AS row_count "
            f"FROM {source} GROUP BY 1 ORDER BY 1"
        )
    if role in {"identifier", "join_key"} or column.endswith("_ref"):
        return (
            f"SELECT COUNT(*) AS row_count, COUNT({column}) AS non_null_count, "
            f"COUNT(DISTINCT {column}) AS distinct_count "
            f"FROM {source}"
        )
    return (
        f"SELECT {column}, COUNT(*) AS row_count "
        f"FROM {source} GROUP BY 1 ORDER BY row_count DESC LIMIT 20"
    )


def _parquet_path(*, data_root: Path, schema: str, table: str) -> Path:
    parquet_root = data_root / schema / "parquet"
    direct = parquet_root / f"{table}.parquet"
    if direct.exists():
        return direct
    matches = sorted(parquet_root.rglob(f"{table}.parquet"))
    if matches:
        return matches[0]
    return direct


def _validate_sql(con: Any, sql: str) -> dict[str, Any]:
    try:
        rows = con.execute(sql).fetchmany(3)
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}
    return {"status": "passed", "preview_rows": len(rows)}


def _table_columns(schema_graph: dict[str, Any]) -> dict[str, list[str]]:
    table_columns: dict[str, list[str]] = {}
    for node in schema_graph.get("nodes", []):
        if node.get("kind") != "column":
            continue
        sql_ref = str(node.get("sql_ref") or "")
        if "." not in sql_ref:
            continue
        table, column = sql_ref.split(".", 1)
        table_columns.setdefault(table, []).append(column)
    return {table: sorted(columns) for table, columns in sorted(table_columns.items())}


def _successful_sql(query_history_path: Path) -> list[str]:
    with query_history_path.open(newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(handle)
        return [
            row["statement_text"]
            for row in rows
            if str(row.get("execution_status") or "").upper() == "FINISHED" and row.get("statement_text")
        ]


def _nl_sql_pair_rows(pair_paths: tuple[Path, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in pair_paths:
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append({str(key): str(value or "") for key, value in row.items()})
    return rows


def _row_identifier(row: dict[str, str]) -> str:
    for field in ("case_id", "history_id", "id", "entry_id", "query_id", "statement_id"):
        value = _one_line(row.get(field))
        if value:
            return value
    return ""


def _row_question(row: dict[str, str]) -> str:
    for field in ("question", "nl_query", "canonical_question", "natural_language", "user_question"):
        value = _one_line(row.get(field))
        if value:
            return value
    return ""


def _row_sql(row: dict[str, str]) -> str:
    for field in ("sql", "statement_text", "query", "sql_template"):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def _row_refs(row: dict[str, str], *, table_fields: tuple[str, ...]) -> list[str]:
    for field in table_fields:
        values = _split_refs(row.get(field))
        if values:
            return values
    return []


def _split_refs(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    refs: list[str] = []
    for chunk in re.split(r"[;\n]", text):
        clean = _one_line(chunk)
        if clean:
            refs.append(clean)
    return refs


def _valid_tables(values: list[str], *, table_columns: dict[str, list[str]]) -> list[str]:
    tables = set(table_columns)
    return sorted({value for value in values if value in tables})


def _valid_column_refs(values: list[str], *, table_columns: dict[str, list[str]]) -> list[str]:
    return sorted({value for value in values if _valid_column_ref(value, table_columns)})


def _valid_column_ref(ref: str, table_columns: dict[str, list[str]]) -> bool:
    if "." not in ref:
        return False
    table, column = ref.split(".", 1)
    return column in table_columns.get(table, [])


def _row_join_edges(row: dict[str, str], *, table_columns: dict[str, list[str]]) -> list[dict[str, Any]]:
    output = []
    for value in _split_refs(row.get("join_edges") or row.get("expected_join_edges")):
        if "=" not in value:
            continue
        left, right = [_one_line(item) for item in value.split("=", 1)]
        if not (_valid_column_ref(left, table_columns) and _valid_column_ref(right, table_columns)):
            continue
        tables = sorted({left.split(".", 1)[0], right.split(".", 1)[0]})
        if len(tables) != 2:
            continue
        left_ref, right_ref = sorted([left, right])
        output.append(
            {
                "left_column": left_ref,
                "right_column": right_ref,
                "tables": tables,
                "sql_condition": f"{left_ref} = {right_ref}",
            }
        )
    return output


def _normalize_sql(sql: str) -> str:
    text = re.sub(r"'[^']*'", "{{string}}", sql)
    text = re.sub(r"\bTIMESTAMP\s+\{\{string\}\}", "{{timestamp}}", text, flags=re.IGNORECASE)
    text = re.sub(r"\bDATE\s+\{\{string\}\}", "{{date}}", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "{{number}}", text)
    return " ".join(text.split())


def _history_key(*, tables: list[str], columns: list[str], normalized_sql: str) -> str:
    digest = hashlib.sha1(normalized_sql.encode("utf-8")).hexdigest()[:10]
    table_part = "_".join(tables[:3])
    column_part = "_".join(column.split(".")[-1] for column in columns[:2])
    return f"history:{table_part}:{column_part}:{digest}"


def _nl_sql_pair_key(*, source_id: str, question: str, normalized_sql: str) -> str:
    digest = hashlib.sha1(f"{source_id}|{question}|{normalized_sql}".encode("utf-8")).hexdigest()[:10]
    clean_source = re.sub(r"[^a-zA-Z0-9_]+", "_", source_id.strip().lower()).strip("_")
    return f"nl_sql:{clean_source or 'pair'}:{digest}"


def _template_name(*, tables: list[str], columns: list[str]) -> str:
    table_text = " + ".join(tables)
    column_text = ", ".join(columns[:4])
    return f"{table_text} pattern using {column_text}"
