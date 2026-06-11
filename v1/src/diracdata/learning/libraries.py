"""Mine reusable semantic/query libraries from scoped query history."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from diracdata.config.settings import DiracDataSettings
from diracdata.learning.models import LearningCollection, to_jsonable
from diracdata.learning.paths import active_learning_artifact_key, learning_artifact_key
from diracdata.learning.query_history import QueryHistoryRecord, load_query_history_csv
from diracdata.storage.object_store import ObjectStore


SUCCESS_STATUSES = {"FINISHED", "SUCCESS", "SUCCEEDED"}
SQL_KEYWORDS = {
    "on",
    "where",
    "join",
    "left",
    "right",
    "inner",
    "outer",
    "full",
    "cross",
    "group",
    "order",
    "having",
    "limit",
    "union",
}
METRIC_ALIAS_HINTS = {
    "avg",
    "average",
    "count",
    "cnt",
    "max",
    "min",
    "rate",
    "ratio",
    "sum",
    "total",
    "volume",
}


@dataclass(frozen=True)
class QueryLibraryBuildResult:
    """Artifact keys and counts from query-history library mining."""

    run_id: str
    manifest_artifact_key: str
    active_manifest_artifact_key: str
    unique_success_query_count: int
    query_pattern_count: int
    sql_template_count: int
    entity_binding_count: int
    metric_usage_count: int
    query_patterns_artifact_key: str | None = None
    sql_template_library_artifact_key: str | None = None
    entity_binding_patterns_artifact_key: str | None = None
    metric_usage_patterns_artifact_key: str | None = None
    sql_library_artifact_key: str | None = None
    sql_library_count: int = 0


class QueryLibraryBuilder:
    """Build compact, reusable libraries from successful scoped SQL history."""

    def __init__(
        self,
        *,
        settings: DiracDataSettings,
        object_store: ObjectStore,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.object_store = object_store
        self.progress_callback = progress_callback

    def build(
        self,
        *,
        collection: LearningCollection,
        query_history_path: str | Path | None = None,
        query_history_records: list[QueryHistoryRecord] | None = None,
        joinable_pairs_artifact_key: str | None = None,
        business_grounding: dict[str, Any] | None = None,
    ) -> QueryLibraryBuildResult:
        records = query_history_records
        if records is None and query_history_path is not None:
            records = load_query_history_csv(query_history_path)

        scoped_tables = {table.table_name for table in collection.table_profiles}
        schema_columns = {
            table.table_name: {column.column_name for column in table.columns}
            for table in collection.table_profiles
        }
        joinable_pairs = self._load_joinable_pairs(joinable_pairs_artifact_key)
        metric_index = _metric_index(business_grounding or {})
        rows = _successful_scoped_unique_rows(records or [], scoped_tables=scoped_tables)

        self._emit(f"query libraries: mine {len(rows)} exact-unique successful scoped queries")
        analyzed = [
            _analyze_sql(
                statement_id=row.statement_id,
                sql=row.statement_text,
                scoped_tables=scoped_tables,
                schema_columns=schema_columns,
                joinable_pairs=joinable_pairs,
                metric_index=metric_index,
            )
            for row in rows
        ]
        analyzed = [row for row in analyzed if row is not None]

        patterns = _pattern_families(analyzed, joinable_pairs=joinable_pairs)
        sql_templates = _sql_templates(patterns)
        entity_bindings = _entity_binding_patterns(analyzed)
        metric_usage = _metric_usage_patterns(analyzed, metric_index=metric_index)

        keys = _artifact_keys(self.settings, collection.run_id)
        self._write_jsonl_pair(
            keys["query_patterns"],
            keys["active_query_patterns"],
            patterns,
        )
        self._write_yaml_pair(
            keys["sql_template_library"],
            keys["active_sql_template_library"],
            {
                "version": 1,
                "artifact_type": "sql_template_library",
                "patterns": sql_templates,
            },
        )
        self._write_jsonl_pair(
            keys["entity_binding_patterns"],
            keys["active_entity_binding_patterns"],
            entity_bindings,
        )
        self._write_jsonl_pair(
            keys["metric_usage_patterns"],
            keys["active_metric_usage_patterns"],
            metric_usage,
        )

        manifest = {
            "artifact_type": "query_history_libraries",
            "catalog": collection.scope.catalog,
            "database": collection.scope.database,
            "schema": collection.scope.schema,
            "run_id": collection.run_id,
            "built_at": datetime.now(UTC).isoformat(),
            "unique_success_query_count": len(rows),
            "analyzed_query_count": len(analyzed),
            "query_pattern_count": len(patterns),
            "sql_template_count": len(sql_templates),
            "entity_binding_count": len(entity_bindings),
            "metric_usage_count": len(metric_usage),
            "canonical_artifacts": {
                "query_patterns_artifact_key": keys["query_patterns"],
                "sql_template_library_artifact_key": keys["sql_template_library"],
                "entity_binding_patterns_artifact_key": keys["entity_binding_patterns"],
                "metric_usage_patterns_artifact_key": keys["metric_usage_patterns"],
            },
            "active_artifacts": {
                "query_patterns_artifact_key": keys["active_query_patterns"],
                "sql_template_library_artifact_key": keys["active_sql_template_library"],
                "entity_binding_patterns_artifact_key": keys["active_entity_binding_patterns"],
                "metric_usage_patterns_artifact_key": keys["active_metric_usage_patterns"],
            },
        }
        self.object_store.write_json(keys["manifest"], manifest)
        self.object_store.write_json(keys["active_manifest"], manifest)
        self._update_active_manifest(keys=keys, manifest=manifest)

        return QueryLibraryBuildResult(
            run_id=collection.run_id,
            manifest_artifact_key=keys["manifest"],
            active_manifest_artifact_key=keys["active_manifest"],
            query_patterns_artifact_key=keys["query_patterns"],
            sql_template_library_artifact_key=keys["sql_template_library"],
            entity_binding_patterns_artifact_key=keys["entity_binding_patterns"],
            metric_usage_patterns_artifact_key=keys["metric_usage_patterns"],
            unique_success_query_count=len(rows),
            query_pattern_count=len(patterns),
            sql_template_count=len(sql_templates),
            entity_binding_count=len(entity_bindings),
            metric_usage_count=len(metric_usage),
        )

    def _load_joinable_pairs(self, key: str | None) -> list[dict[str, Any]]:
        if key is None or not self.object_store.exists(key):
            return []
        return [json.loads(line) for line in self.object_store.read_text(key).splitlines() if line.strip()]

    def _write_jsonl_pair(
        self,
        immutable_key: str,
        active_key: str,
        rows: list[dict[str, Any]],
    ) -> None:
        payload = "".join(json.dumps(to_jsonable(row), sort_keys=True) + "\n" for row in rows)
        self.object_store.write_text(immutable_key, payload, content_type="application/jsonl")
        self.object_store.write_text(active_key, payload, content_type="application/jsonl")

    def _write_yaml_pair(self, immutable_key: str, active_key: str, payload: dict[str, Any]) -> None:
        text = yaml.safe_dump(to_jsonable(payload), sort_keys=False, allow_unicode=False)
        self.object_store.write_text(immutable_key, text, content_type="application/yaml")
        self.object_store.write_text(active_key, text, content_type="application/yaml")

    def _update_active_manifest(self, *, keys: dict[str, str], manifest: dict[str, Any]) -> None:
        active_manifest_key = active_learning_artifact_key(self.settings, relative_path="manifest.json")
        if not self.object_store.exists(active_manifest_key):
            return
        active_manifest = self.object_store.read_json(active_manifest_key)
        if not isinstance(active_manifest, dict):
            return
        active_manifest.setdefault("immutable_artifacts", {})["query_libraries_manifest_artifact_key"] = (
            keys["manifest"]
        )
        active_manifest.setdefault("active_artifacts", {})["query_libraries_manifest_artifact_key"] = (
            keys["active_manifest"]
        )
        active_manifest["query_libraries"] = {
            "unique_success_query_count": manifest["unique_success_query_count"],
            "query_pattern_count": manifest["query_pattern_count"],
            "sql_template_count": manifest["sql_template_count"],
            "entity_binding_count": manifest["entity_binding_count"],
            "metric_usage_count": manifest["metric_usage_count"],
        }
        self.object_store.write_json(active_manifest_key, active_manifest)

    def _emit(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)


def _successful_scoped_unique_rows(
    records: list[QueryHistoryRecord],
    *,
    scoped_tables: set[str],
) -> list[QueryHistoryRecord]:
    seen: set[str] = set()
    rows: list[QueryHistoryRecord] = []
    for record in records:
        if record.execution_status.upper() not in SUCCESS_STATUSES:
            continue
        sql = record.statement_text.strip()
        if not sql or sql in seen:
            continue
        tables = _mentioned_tables(sql, scoped_tables)
        if not tables:
            continue
        seen.add(sql)
        rows.append(record)
    return rows


def _analyze_sql(
    *,
    statement_id: str,
    sql: str,
    scoped_tables: set[str],
    schema_columns: dict[str, set[str]],
    joinable_pairs: list[dict[str, Any]],
    metric_index: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    aliases = _table_aliases(sql, scoped_tables)
    tables = sorted(set(aliases.values()))
    if not tables:
        return None
    column_refs = _column_refs(sql, aliases, schema_columns)
    joins = _join_clauses(sql, aliases, schema_columns)
    join_columns = {ref for join in joins for ref in [join["left_ref"], join["right_ref"]]}
    filter_bindings = _filter_bindings(sql, aliases, schema_columns)
    filter_columns = {binding["column_ref"] for binding in filter_bindings}
    metrics = _metric_ids(sql, metric_index)
    metric_columns = _metric_columns(metrics, metric_index)
    selected_columns = sorted(column_refs - join_columns - filter_columns - metric_columns)
    first_table = _first_table(sql, scoped_tables)
    template = _parameterized_sql_template(sql)
    used_join_ids = sorted(
        {
            _join_id_from_refs(join["left_ref"], join["right_ref"])
            for join in joins
        }
    )
    alternate_joins = _alternate_joins(
        used_joins=joins,
        tables=set(tables),
        joinable_pairs=joinable_pairs,
    )
    return {
        "statement_id": statement_id,
        "sql_hash": _stable_id(sql),
        "statement_text": sql,
        "tables": tables,
        "fact_table": first_table or tables[0],
        "metrics": metrics,
        "selected_columns": selected_columns,
        "filter_bindings": filter_bindings,
        "join_clauses": joins,
        "join_ids": used_join_ids,
        "alternate_joins": alternate_joins,
        "parameterized_sql": template["sql"],
        "parameters": template["parameters"],
    }


def _pattern_families(
    analyzed_queries: list[dict[str, Any]],
    *,
    joinable_pairs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for query in analyzed_queries:
        grouped[_pattern_key(query)].append(query)

    patterns: list[dict[str, Any]] = []
    for key, queries in sorted(grouped.items()):
        table_counter: Counter[str] = Counter()
        metric_counter: Counter[str] = Counter()
        selected_counter: Counter[str] = Counter()
        filter_counter: Counter[str] = Counter()
        join_counter: Counter[tuple[str, str, str]] = Counter()
        alternate_join_counter: Counter[str] = Counter()
        fact_counter: Counter[str] = Counter()
        sql_counter: Counter[str] = Counter()
        example_ids: list[str] = []
        parameterized_examples: list[dict[str, Any]] = []
        for query in queries:
            table_counter.update(query["tables"])
            metric_counter.update(query["metrics"])
            selected_counter.update(query["selected_columns"])
            filter_counter.update(binding["column_ref"] for binding in query["filter_bindings"])
            fact_counter.update([query["fact_table"]])
            sql_counter.update([query["parameterized_sql"]])
            if len(example_ids) < 5:
                example_ids.append(query["statement_id"])
            if len(parameterized_examples) < 3:
                parameterized_examples.append(
                    {
                        "sql_hash": query["sql_hash"],
                        "statement_id": query["statement_id"],
                        "sql": query["parameterized_sql"],
                        "parameters": query["parameters"],
                    }
                )
            for join in query["join_clauses"]:
                join_counter.update([(join["left_ref"], join["operator"], join["right_ref"])])
            for alternate in query["alternate_joins"]:
                alternate_join_counter.update([alternate["avoid_join"]])

        canonical_joins = [
            {
                "left_ref": left_ref,
                "operator": operator,
                "right_ref": right_ref,
                "support_count": count,
            }
            for (left_ref, operator, right_ref), count in join_counter.most_common()
        ]
        risky_alternatives = [
            {
                "avoid_join": join,
                "reason": (
                    "This table pair has another observed join in the schema. "
                    "Use the canonical pattern join unless the user intent changes the grain."
                ),
                "support_count": count,
            }
            for join, count in alternate_join_counter.most_common()
        ]
        pattern = {
            "id": f"library_pattern:{key}",
            "artifact_type": "query_history_pattern",
            "query_count": len(queries),
            "source_statement_ids": example_ids,
            "fact_table": fact_counter.most_common(1)[0][0] if fact_counter else None,
            "tables": sorted(table_counter),
            "metrics": [item for item, _count in metric_counter.most_common()],
            "dimension_columns": [item for item, _count in selected_counter.most_common(20)],
            "filter_columns": [item for item, _count in filter_counter.most_common(20)],
            "canonical_joins": canonical_joins,
            "risky_alternatives": risky_alternatives,
            "top_sql_template": sql_counter.most_common(1)[0][0] if sql_counter else None,
            "parameterized_examples": parameterized_examples,
        }
        pattern["compact_contract"] = _compact_contract(pattern)
        patterns.append(pattern)
    return patterns


def _sql_templates(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = []
    for pattern in patterns:
        examples = pattern.get("parameterized_examples") or []
        if not examples:
            continue
        first = examples[0]
        templates.append(
            {
                "id": str(pattern["id"]).replace("library_pattern:", "sql_template:"),
                "source_pattern_id": pattern["id"],
                "query_count": pattern["query_count"],
                "tables": pattern["tables"],
                "metrics": pattern["metrics"],
                "parameters": first.get("parameters", []),
                "sql": first.get("sql"),
                "notes": [
                    "Template was mined from successful query history.",
                    "Bind parameters and preserve the pattern grain before reuse.",
                ],
            }
        )
    return templates


def _entity_binding_patterns(analyzed_queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for query in analyzed_queries:
        for binding in query["filter_bindings"]:
            grouped[binding["column_ref"]].append(binding)

    rows: list[dict[str, Any]] = []
    for column_ref, bindings in sorted(grouped.items()):
        values = Counter(str(binding.get("literal_preview") or "") for binding in bindings)
        operators = Counter(str(binding.get("operator") or "") for binding in bindings)
        rows.append(
            {
                "id": f"entity_binding:{_stable_id(column_ref)}",
                "artifact_type": "entity_binding_pattern",
                "column_ref": column_ref,
                "query_count": len(bindings),
                "operators": [item for item, _count in operators.most_common()],
                "example_values": [
                    value for value, _count in values.most_common(10) if value
                ],
                "literal_kinds": sorted(
                    {str(binding.get("literal_kind")) for binding in bindings if binding.get("literal_kind")}
                ),
            }
        )
    return rows


def _metric_usage_patterns(
    analyzed_queries: list[dict[str, Any]],
    *,
    metric_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for query in analyzed_queries:
        for metric_id in query["metrics"]:
            grouped[metric_id].append(query)

    rows: list[dict[str, Any]] = []
    for metric_id, queries in sorted(grouped.items()):
        table_counter: Counter[str] = Counter()
        column_counter: Counter[str] = Counter()
        join_counter: Counter[str] = Counter()
        for query in queries:
            table_counter.update(query["tables"])
            column_counter.update(query["selected_columns"])
            column_counter.update(binding["column_ref"] for binding in query["filter_bindings"])
            join_counter.update(query["join_ids"])
        metric = metric_index.get(metric_id, {})
        rows.append(
            {
                "id": f"metric_usage:{_stable_id(metric_id)}",
                "artifact_type": "metric_usage_pattern",
                "metric_id": metric_id,
                "name": metric.get("name"),
                "query_count": len(queries),
                "tables": [item for item, _count in table_counter.most_common()],
                "columns": [item for item, _count in column_counter.most_common(25)],
                "join_ids": [item for item, _count in join_counter.most_common(25)],
            }
        )
    return rows


def _metric_index(grounding: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for item in _as_list(grounding.get("metrics")):
        if not isinstance(item, dict):
            continue
        metric_id = str(item.get("id") or item.get("name") or _stable_id(json.dumps(item, sort_keys=True)))
        terms = {
            metric_id,
            str(item.get("name") or ""),
            str(item.get("calculation") or ""),
        }
        terms.update(str(value) for value in _as_list(item.get("synonyms")))
        metrics[metric_id] = {
            "id": metric_id,
            "name": item.get("name"),
            "terms": sorted({term.lower() for term in terms if term}),
            "columns": sorted(str(value) for value in _as_list(item.get("columns"))),
            "tables": sorted(str(value) for value in _as_list(item.get("tables"))),
        }
    return metrics


def _metric_ids(sql: str, metric_index: dict[str, dict[str, Any]]) -> list[str]:
    normalized = _normalize_text(sql)
    aliases = _select_aliases(sql)
    metric_ids: list[str] = []
    for metric_id, metric in sorted(metric_index.items()):
        terms = set(metric.get("terms") or [])
        if metric_id.lower() in aliases or any(_term_in_text(term, normalized) for term in terms):
            metric_ids.append(metric_id)

    generic_aliases = [
        alias
        for alias in aliases
        if any(hint in alias for hint in METRIC_ALIAS_HINTS)
    ]
    for alias in generic_aliases:
        if alias not in metric_ids:
            metric_ids.append(alias)
    return metric_ids


def _metric_columns(metric_ids: list[str], metric_index: dict[str, dict[str, Any]]) -> set[str]:
    columns: set[str] = set()
    for metric_id in metric_ids:
        columns.update(str(column) for column in metric_index.get(metric_id, {}).get("columns") or [])
    return columns


def _table_aliases(sql: str, scoped_tables: set[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    pattern = re.compile(
        r"\b(?:from|join)\s+([a-z_][a-z0-9_$.]*|\"[^\"]+\")"
        r"(?:\s+(?:as\s+)?([a-z_][a-z0-9_]*))?",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(sql):
        table = _clean_identifier(match.group(1)).split(".")[-1]
        if table not in scoped_tables:
            continue
        alias = (match.group(2) or table).lower()
        if alias in SQL_KEYWORDS:
            alias = table
        aliases[alias] = table
        aliases[table.lower()] = table
    return aliases


def _first_table(sql: str, scoped_tables: set[str]) -> str | None:
    match = re.search(r"\bfrom\s+([a-z_][a-z0-9_$.]*|\"[^\"]+\")", sql, flags=re.IGNORECASE)
    if not match:
        return None
    table = _clean_identifier(match.group(1)).split(".")[-1]
    return table if table in scoped_tables else None


def _mentioned_tables(sql: str, scoped_tables: set[str]) -> set[str]:
    aliases = _table_aliases(sql, scoped_tables)
    if aliases:
        return set(aliases.values())
    normalized = sql.lower()
    return {
        table
        for table in scoped_tables
        if re.search(rf"(?<![a-z0-9_]){re.escape(table.lower())}(?![a-z0-9_])", normalized)
    }


def _column_refs(
    sql: str,
    aliases: dict[str, str],
    schema_columns: dict[str, set[str]],
) -> set[str]:
    refs: set[str] = set()
    for alias, column in re.findall(
        r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b",
        sql,
        flags=re.IGNORECASE,
    ):
        table = aliases.get(alias.lower())
        if table is None:
            continue
        column_name = column.lower()
        canonical = _canonical_column(table, column_name, schema_columns)
        if canonical is not None:
            refs.add(f"{table}.{canonical}")
    return refs


def _join_clauses(
    sql: str,
    aliases: dict[str, str],
    schema_columns: dict[str, set[str]],
) -> list[dict[str, Any]]:
    joins: list[dict[str, Any]] = []
    on_pattern = re.compile(
        r"\bjoin\s+[a-z_][a-z0-9_$.]*\s+(?:as\s+)?[a-z_][a-z0-9_]*\s+on\s+"
        r"(?P<clause>.*?)(?=\b(?:join|where|group\s+by|having|order\s+by|limit|union)\b|$)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    equality_pattern = re.compile(
        r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\s*(=)\s*"
        r"([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b",
        flags=re.IGNORECASE,
    )
    seen: set[tuple[str, str, str]] = set()
    for on_match in on_pattern.finditer(sql):
        clause = on_match.group("clause")
        for match in equality_pattern.finditer(clause):
            left = _qualified_ref(match.group(1), match.group(2), aliases, schema_columns)
            right = _qualified_ref(match.group(4), match.group(5), aliases, schema_columns)
            if left is None or right is None:
                continue
            ordered = _ordered_join_refs(left, right)
            key = (ordered[0], match.group(3), ordered[1])
            if key in seen:
                continue
            seen.add(key)
            joins.append(
                {
                    "left_ref": ordered[0],
                    "operator": match.group(3),
                    "right_ref": ordered[1],
                    "clause": f"{ordered[0]} {match.group(3)} {ordered[1]}",
                }
            )
    return joins


def _filter_bindings(
    sql: str,
    aliases: dict[str, str],
    schema_columns: dict[str, set[str]],
) -> list[dict[str, Any]]:
    clause = _where_clause(sql)
    if not clause:
        return []
    pattern = re.compile(
        r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\s*"
        r"(=|<>|!=|>=|<=|>|<|like)\s*"
        r"(timestamp\s+'[^']+'|'[^']+'|\d+(?:\.\d+)?)",
        flags=re.IGNORECASE,
    )
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for match in pattern.finditer(clause):
        ref = _qualified_ref(match.group(1), match.group(2), aliases, schema_columns)
        if ref is None:
            continue
        operator = match.group(3).upper()
        literal = match.group(4)
        key = (ref, operator, literal)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "column_ref": ref,
                "operator": operator,
                "literal_kind": _literal_kind(literal),
                "literal_preview": _literal_preview(literal),
            }
        )
    return rows


def _alternate_joins(
    *,
    used_joins: list[dict[str, Any]],
    tables: set[str],
    joinable_pairs: list[dict[str, Any]],
) -> list[dict[str, str]]:
    used_by_pair: dict[frozenset[str], set[str]] = defaultdict(set)
    for join in used_joins:
        left_table = join["left_ref"].split(".", 1)[0]
        right_table = join["right_ref"].split(".", 1)[0]
        used_by_pair[frozenset([left_table, right_table])].add(
            _join_id_from_refs(join["left_ref"], join["right_ref"])
        )

    alternatives: list[dict[str, str]] = []
    for pair in joinable_pairs:
        left_ref = f"{pair.get('left_table')}.{pair.get('left_column')}"
        right_ref = f"{pair.get('right_table')}.{pair.get('right_column')}"
        left_table = str(pair.get("left_table"))
        right_table = str(pair.get("right_table"))
        if left_table not in tables or right_table not in tables:
            continue
        table_pair = frozenset([left_table, right_table])
        pair_join_id = _join_id_from_refs(left_ref, right_ref)
        if table_pair in used_by_pair and pair_join_id not in used_by_pair[table_pair]:
            alternatives.append({"avoid_join": _join_clause_from_refs(left_ref, right_ref)})
    return sorted(alternatives, key=lambda item: item["avoid_join"])


def _where_clause(sql: str) -> str:
    match = re.search(
        r"\bwhere\b(?P<clause>.*?)(?=\b(?:group\s+by|having|order\s+by|limit|union)\b|$)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group("clause") if match else ""


def _select_aliases(sql: str) -> set[str]:
    return {
        alias.lower()
        for alias in re.findall(r"\bas\s+([a-z_][a-z0-9_]*)\b", sql, flags=re.IGNORECASE)
    }


def _parameterized_sql_template(sql: str) -> dict[str, Any]:
    parameters: list[dict[str, str]] = []
    index = 0

    def replace_timestamp(match: re.Match[str]) -> str:
        nonlocal index
        index += 1
        name = f"timestamp_{index}"
        parameters.append({"name": name, "type": "timestamp", "example": match.group(0)})
        return "{{ " + name + " }}"

    def replace_string(match: re.Match[str]) -> str:
        nonlocal index
        index += 1
        name = f"value_{index}"
        parameters.append({"name": name, "type": "string", "example": match.group(0)})
        return "{{ " + name + " }}"

    templated = re.sub(
        r"\bTIMESTAMP\s+'[^']+'",
        replace_timestamp,
        sql,
        flags=re.IGNORECASE,
    )
    templated = re.sub(r"'[^']*'", replace_string, templated)
    return {"sql": templated, "parameters": parameters}


def _pattern_key(query: dict[str, Any]) -> str:
    payload = {
        "tables": query["tables"],
        "fact_table": query["fact_table"],
        "metrics": sorted(query["metrics"]),
        "selected_columns": sorted(query["selected_columns"]),
        "filter_columns": sorted(binding["column_ref"] for binding in query["filter_bindings"]),
        "joins": sorted(query["join_ids"]),
    }
    return _stable_id(json.dumps(payload, sort_keys=True))


def _compact_contract(pattern: dict[str, Any]) -> dict[str, Any]:
    return {
        "fact_table": pattern.get("fact_table"),
        "metrics": pattern.get("metrics", [])[:8],
        "tables": pattern.get("tables", [])[:10],
        "dimension_columns": pattern.get("dimension_columns", [])[:12],
        "filter_columns": pattern.get("filter_columns", [])[:12],
        "required_joins": [
            f"{join['left_ref']} {join['operator']} {join['right_ref']}"
            for join in _as_list(pattern.get("canonical_joins"))[:8]
        ],
        "avoid_joins": [
            item["avoid_join"]
            for item in _as_list(pattern.get("risky_alternatives"))[:5]
            if isinstance(item, dict) and item.get("avoid_join")
        ],
    }


def _qualified_ref(
    alias: str,
    column: str,
    aliases: dict[str, str],
    schema_columns: dict[str, set[str]],
) -> str | None:
    table = aliases.get(alias.lower())
    if table is None:
        return None
    canonical = _canonical_column(table, column.lower(), schema_columns)
    if canonical is None:
        return None
    return f"{table}.{canonical}"


def _canonical_column(table: str, column: str, schema_columns: dict[str, set[str]]) -> str | None:
    for candidate in schema_columns.get(table, set()):
        if candidate.lower() == column.lower():
            return candidate
    return None


def _ordered_join_refs(left_ref: str, right_ref: str) -> tuple[str, str]:
    return tuple(sorted([left_ref, right_ref]))  # type: ignore[return-value]


def _join_id_from_refs(left_ref: str, right_ref: str) -> str:
    left, right = _ordered_join_refs(left_ref, right_ref)
    return f"join_pair:{left}={right}"


def _join_clause_from_refs(left_ref: str, right_ref: str) -> str:
    left, right = _ordered_join_refs(left_ref, right_ref)
    return f"{left} = {right}"


def _literal_kind(literal: str) -> str:
    if literal.lower().startswith("timestamp"):
        return "timestamp"
    if literal.startswith("'"):
        return "string"
    return "number"


def _literal_preview(literal: str) -> str:
    clean = literal.strip()
    if clean.lower().startswith("timestamp"):
        clean = clean.split(None, 1)[1]
    return clean.strip("'")[:80]


def _term_in_text(term: str, text: str) -> bool:
    normalized = _normalize_text(term)
    if not normalized:
        return False
    if " " in normalized:
        return normalized in text
    return re.search(rf"(?<![a-z0-9_]){re.escape(normalized)}(?![a-z0-9_])", text) is not None


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _clean_identifier(value: str) -> str:
    return value.strip().strip('"').lower()


def _stable_id(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _artifact_keys(settings: DiracDataSettings, run_id: str) -> dict[str, str]:
    relative_paths = {
        "manifest": "libraries/manifest.json",
        "query_patterns": "libraries/query_patterns.jsonl",
        "sql_template_library": "libraries/sql_template_library.yaml",
        "entity_binding_patterns": "libraries/entity_binding_patterns.jsonl",
        "metric_usage_patterns": "libraries/metric_usage_patterns.jsonl",
    }
    keys = {
        name: learning_artifact_key(settings, run_id=run_id, relative_path=path)
        for name, path in relative_paths.items()
    }
    keys.update(
        {
            f"active_{name}": active_learning_artifact_key(settings, relative_path=path)
            for name, path in relative_paths.items()
        }
    )
    return keys


def _as_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]
