"""Join discovery and runtime join-recovery tools for the data analyst agent."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from pydantic import BaseModel, Field

from diracdata.agents.artifacts import AgentArtifactError, LearnedArtifactRepository
from diracdata.config.settings import DiracDataSettings
from diracdata.core.sql import quote_identifier
from diracdata.query_engines.base import QueryEngine


class JoinDiscoveryInput(BaseModel):
    tables: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of tables that must be joined. Use with fact_table to get a "
            "compact graph join path contract."
        ),
    )
    fact_table: str | None = Field(
        default=None,
        description="Optional starting/fact table for graph join path search.",
    )
    left_table: str | None = Field(
        default=None,
        description="Optional left table name to filter or recover joinable pairs.",
    )
    right_table: str | None = Field(
        default=None,
        description="Optional right table name to filter or recover joinable pairs.",
    )
    left_column: str | None = Field(
        default=None,
        description="Optional proposed left join column to validate.",
    )
    right_column: str | None = Field(
        default=None,
        description="Optional proposed right join column to validate.",
    )


@dataclass(frozen=True)
class _ColumnInfo:
    table_name: str
    column_name: str
    data_type: str
    row_count: int | None = None
    distinct_count: int | None = None


@dataclass(frozen=True)
class _JoinCandidate:
    left: _ColumnInfo
    right: _ColumnInfo
    score: float
    explicit: bool = False


def build_join_tools(
    *,
    settings: DiracDataSettings,
    repository: LearnedArtifactRepository,
    query_engine: QueryEngine,
) -> list[object]:
    from langchain.tools import tool

    @tool("join_discovery_tool", args_schema=JoinDiscoveryInput)
    def join_discovery_tool(
        tables: list[str] | None = None,
        fact_table: str | None = None,
        left_table: str | None = None,
        right_table: str | None = None,
        left_column: str | None = None,
        right_column: str | None = None,
    ) -> dict[str, object]:
        """Return learned joins or validate and persist runtime-recovered join pairs."""
        clean_tables = _clean_table_list(tables)
        clean_fact_table = _blank_to_none(fact_table)
        clean_left_table = _blank_to_none(left_table)
        clean_right_table = _blank_to_none(right_table)
        clean_left_column = _blank_to_none(left_column)
        clean_right_column = _blank_to_none(right_column)

        try:
            pairs = repository.load_joinable_pairs()
            if clean_tables:
                patterns = repository.load_query_library_patterns()
                contract = _join_path_contract(
                    pairs=pairs,
                    patterns=patterns,
                    tables=clean_tables,
                    fact_table=clean_fact_table,
                )
                if contract is not None:
                    return contract
                return {
                    "status": "not_found",
                    "source": "learned_graph",
                    "requested_tables": clean_tables,
                    "fact_table": clean_fact_table,
                    "join_path": [],
                    "risky_alternatives": [],
                    "joinable_pairs": _filter_pairs(
                        pairs,
                        left_table=clean_fact_table,
                        right_table=None,
                    )
                    if clean_fact_table
                    else [],
                }

            filtered = _filter_pairs(
                pairs,
                left_table=clean_left_table,
                right_table=clean_right_table,
                left_column=clean_left_column,
                right_column=clean_right_column,
            )
            if filtered:
                return {
                    "status": "ok",
                    "source": "learned",
                    "joinable_pairs": filtered,
                    "pair_count": len(filtered),
                }

            if not settings.agent_join_recovery_enabled:
                return {
                    "status": "not_found",
                    "joinable_pairs": [],
                    "pair_count": 0,
                    "schema": _schema_context(
                        repository=repository,
                        query_engine=query_engine,
                        table_names=[clean_left_table, clean_right_table],
                    ),
                }

            recovered = _recover_join_pairs(
                settings=settings,
                repository=repository,
                query_engine=query_engine,
                left_table=clean_left_table,
                right_table=clean_right_table,
                left_column=clean_left_column,
                right_column=clean_right_column,
            )
            if recovered:
                repository.persist_active_joinable_pairs(recovered)
                return {
                    "status": "ok",
                    "source": "runtime_recovery",
                    "joinable_pairs": recovered,
                    "pair_count": len(recovered),
                    "persisted": True,
                }

            return {
                "status": "not_found",
                "joinable_pairs": [],
                "pair_count": 0,
                "schema": _schema_context(
                    repository=repository,
                    query_engine=query_engine,
                    table_names=[clean_left_table, clean_right_table],
                ),
            }
        except AgentArtifactError as exc:
            return {"status": "error", "error": str(exc)}

    return [join_discovery_tool]


def _filter_pairs(
    pairs: list[dict[str, Any]],
    *,
    left_table: str | None,
    right_table: str | None,
    left_column: str | None = None,
    right_column: str | None = None,
) -> list[dict[str, Any]]:
    if left_table is None and right_table is None:
        return pairs

    requested_tables = {table for table in [left_table, right_table] if table is not None}
    requested_columns = {
        (table, column)
        for table, column in [(left_table, left_column), (right_table, right_column)]
        if table is not None and column is not None
    }
    filtered = []
    for pair in pairs:
        pair_endpoints = {
            (str(pair.get("left_table")), str(pair.get("left_column"))),
            (str(pair.get("right_table")), str(pair.get("right_column"))),
        }
        pair_tables = {table for table, _column in pair_endpoints}
        if left_table is not None and right_table is not None and requested_tables != pair_tables:
            continue
        if left_table is not None and right_table is None and not (requested_tables & pair_tables):
            continue
        if requested_columns and not requested_columns.issubset(pair_endpoints):
            continue
        filtered.append(pair)
    return filtered


def _join_path_contract(
    *,
    pairs: list[dict[str, Any]],
    patterns: list[dict[str, Any]],
    tables: list[str],
    fact_table: str | None,
) -> dict[str, object] | None:
    requested = _ordered_unique(tables)
    if len(requested) < 2:
        return None
    start = fact_table if fact_table in requested else requested[0]
    pattern_contract = _pattern_join_contract(patterns=patterns, requested_tables=set(requested), fact_table=start)
    if pattern_contract is not None:
        return pattern_contract

    graph = _table_graph(pairs)
    if start not in graph:
        return None
    path_edges: list[dict[str, Any]] = []
    for table in requested:
        if table == start:
            continue
        path = _shortest_join_path(graph=graph, start=start, target=table)
        if path is None:
            return None
        path_edges.extend(path)

    join_path = _dedupe_path_edges(path_edges)
    return {
        "status": "ok",
        "source": "learned_graph",
        "requested_tables": requested,
        "fact_table": start,
        "join_path": join_path,
        "join_count": len(join_path),
        "risky_alternatives": _risky_alternatives_for_path(pairs=pairs, join_path=join_path),
    }


def _pattern_join_contract(
    *,
    patterns: list[dict[str, Any]],
    requested_tables: set[str],
    fact_table: str,
) -> dict[str, object] | None:
    candidates = []
    for pattern in patterns:
        pattern_tables = {str(table) for table in pattern.get("tables", [])}
        if not requested_tables <= pattern_tables:
            continue
        support = int(pattern.get("query_count") or 0)
        fact_match = 1 if pattern.get("fact_table") == fact_table else 0
        candidates.append((fact_match, support, pattern))
    if not candidates:
        return None
    _fact_match, _support, pattern = sorted(candidates, key=lambda item: (-item[0], -item[1]))[0]
    compact = pattern.get("compact_contract")
    compact = compact if isinstance(compact, dict) else {}
    required_joins = [
        _join_path_item_from_clause(clause)
        for clause in compact.get("required_joins", [])
        if isinstance(clause, str)
    ]
    required_joins = [item for item in required_joins if item is not None]
    return {
        "status": "ok",
        "source": "query_history_library",
        "pattern_id": pattern.get("id"),
        "pattern_query_count": pattern.get("query_count"),
        "requested_tables": sorted(requested_tables),
        "fact_table": compact.get("fact_table") or pattern.get("fact_table") or fact_table,
        "join_path": required_joins,
        "join_count": len(required_joins),
        "risky_alternatives": [
            {"join_clause": clause}
            for clause in compact.get("avoid_joins", [])
            if isinstance(clause, str)
        ],
        "compact_contract": compact,
    }


def _table_graph(pairs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    graph: dict[str, list[dict[str, Any]]] = {}
    for pair in pairs:
        left_table = str(pair.get("left_table") or "")
        right_table = str(pair.get("right_table") or "")
        if not left_table or not right_table:
            continue
        edge = _path_edge(pair)
        graph.setdefault(left_table, []).append(edge)
        graph.setdefault(right_table, []).append(edge)
    for edges in graph.values():
        edges.sort(
            key=lambda edge: (
                -_confidence_score(str(edge.get("confidence"))),
                str(edge.get("join_clause")),
            )
        )
    return graph


def _shortest_join_path(
    *,
    graph: dict[str, list[dict[str, Any]]],
    start: str,
    target: str,
) -> list[dict[str, Any]] | None:
    queue: list[tuple[str, list[dict[str, Any]]]] = [(start, [])]
    seen = {start}
    while queue:
        table, path = queue.pop(0)
        if table == target:
            return path
        for edge in graph.get(table, []):
            next_table = edge["right_table"] if edge["left_table"] == table else edge["left_table"]
            if next_table in seen:
                continue
            seen.add(next_table)
            queue.append((next_table, [*path, edge]))
    return None


def _path_edge(pair: dict[str, Any]) -> dict[str, Any]:
    left_table = str(pair.get("left_table") or "")
    left_column = str(pair.get("left_column") or "")
    right_table = str(pair.get("right_table") or "")
    right_column = str(pair.get("right_column") or "")
    return {
        "left_table": left_table,
        "left_column": left_column,
        "right_table": right_table,
        "right_column": right_column,
        "join_clause": f"{left_table}.{left_column} = {right_table}.{right_column}",
        "join_type": str(pair.get("join_type") or ""),
        "confidence": str(pair.get("confidence") or ""),
    }


def _dedupe_path_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[frozenset[tuple[str, str]], dict[str, Any]] = {}
    for edge in edges:
        key = frozenset(
            [
                (str(edge["left_table"]), str(edge["left_column"])),
                (str(edge["right_table"]), str(edge["right_column"])),
            ]
        )
        if key not in deduped:
            deduped[key] = edge
    return list(deduped.values())


def _risky_alternatives_for_path(
    *,
    pairs: list[dict[str, Any]],
    join_path: list[dict[str, Any]],
) -> list[dict[str, str]]:
    used_by_table_pair: dict[frozenset[str], set[frozenset[tuple[str, str]]]] = {}
    for edge in join_path:
        table_pair = frozenset([str(edge["left_table"]), str(edge["right_table"])])
        column_pair = frozenset(
            [
                (str(edge["left_table"]), str(edge["left_column"])),
                (str(edge["right_table"]), str(edge["right_column"])),
            ]
        )
        used_by_table_pair.setdefault(table_pair, set()).add(column_pair)

    alternatives = []
    seen: set[str] = set()
    for pair in pairs:
        edge = _path_edge(pair)
        table_pair = frozenset([edge["left_table"], edge["right_table"]])
        column_pair = frozenset(
            [
                (edge["left_table"], edge["left_column"]),
                (edge["right_table"], edge["right_column"]),
            ]
        )
        if table_pair not in used_by_table_pair or column_pair in used_by_table_pair[table_pair]:
            continue
        clause = str(edge["join_clause"])
        if clause in seen:
            continue
        seen.add(clause)
        alternatives.append(
            {
                "join_clause": clause,
                "reason": (
                    "Same table pair has another join edge. Validate grain before using it."
                ),
            }
        )
    return alternatives


def _join_path_item_from_clause(clause: str) -> dict[str, str] | None:
    match = re.match(
        r"\s*([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\s*=\s*"
        r"([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\s*$",
        clause,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    left_table, left_column, right_table, right_column = match.groups()
    return {
        "left_table": left_table,
        "left_column": left_column,
        "right_table": right_table,
        "right_column": right_column,
        "join_clause": clause,
        "confidence": "query_history",
    }


def _confidence_score(value: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(value, 0)


def _clean_table_list(tables: list[str] | None) -> list[str]:
    if not tables:
        return []
    return _ordered_unique(table.strip() for table in tables if table and table.strip())


def _ordered_unique(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _recover_join_pairs(
    *,
    settings: DiracDataSettings,
    repository: LearnedArtifactRepository,
    query_engine: QueryEngine,
    left_table: str | None,
    right_table: str | None,
    left_column: str | None,
    right_column: str | None,
) -> list[dict[str, Any]]:
    if left_table is None or right_table is None:
        return []

    schema = _column_index(repository=repository, query_engine=query_engine)
    if left_table not in schema or right_table not in schema:
        return []

    candidates = _candidate_pairs(
        schema=schema,
        left_table=left_table,
        right_table=right_table,
        left_column=left_column,
        right_column=right_column,
        limit=settings.agent_join_recovery_candidate_limit,
    )
    recovered = []
    seen: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    for candidate in candidates:
        if not _type_compatible(candidate.left.data_type, candidate.right.data_type):
            continue
        if not _validate_join_candidate(query_engine=query_engine, candidate=candidate):
            continue
        pair = _join_pair(candidate, unique_tolerance=settings.join_key_unique_tolerance)
        key = _pair_key(pair)
        if key in seen:
            continue
        seen.add(key)
        recovered.append(pair)
    return recovered


def _candidate_pairs(
    *,
    schema: dict[str, list[_ColumnInfo]],
    left_table: str,
    right_table: str,
    left_column: str | None,
    right_column: str | None,
    limit: int,
) -> list[_JoinCandidate]:
    left_columns = _filter_columns(schema[left_table], left_column)
    right_columns = _filter_columns(schema[right_table], right_column)
    if left_column is not None and not left_columns:
        return []
    if right_column is not None and not right_columns:
        return []

    explicit = left_column is not None or right_column is not None
    candidates = []
    for left in left_columns:
        for right in right_columns:
            score = _candidate_score(left, right)
            if score > 0.0:
                candidates.append(_JoinCandidate(left=left, right=right, score=score, explicit=explicit))

    return sorted(
        candidates,
        key=lambda item: (
            -item.score,
            item.left.table_name,
            item.left.column_name,
            item.right.table_name,
            item.right.column_name,
        ),
    )[: max(limit, 0)]


def _filter_columns(columns: list[_ColumnInfo], column_name: str | None) -> list[_ColumnInfo]:
    if column_name is None:
        return columns
    return [column for column in columns if column.column_name == column_name]


def _candidate_score(left: _ColumnInfo, right: _ColumnInfo) -> float:
    structural = max(
        _ref_record_score(left, right),
        _ref_record_score(right, left),
    )
    if structural > 0:
        return structural
    if left.column_name == right.column_name and _looks_identifier_like(left.column_name):
        return 0.7
    return 0.0


def _ref_record_score(ref_column: _ColumnInfo, record_column: _ColumnInfo) -> float:
    if not ref_column.column_name.endswith("_ref"):
        return 0.0
    if not record_column.column_name.endswith("_record"):
        return 0.0

    ref_base = ref_column.column_name.removesuffix("_ref")
    record_base = record_column.column_name.removesuffix("_record")
    ref_tokens = _tokens(ref_base)
    record_tokens = _tokens(record_base)
    table_tokens = _singular_tokens(record_column.table_name)
    if record_tokens and _has_suffix(ref_tokens, record_tokens):
        return 1.0
    if table_tokens and _has_suffix(ref_tokens, table_tokens):
        return 0.95
    return 0.0


def _validate_join_candidate(*, query_engine: QueryEngine, candidate: _JoinCandidate) -> bool:
    sql = (
        "SELECT 1 AS join_match "
        f"FROM {quote_identifier(candidate.left.table_name)} AS left_table "
        f"JOIN {quote_identifier(candidate.right.table_name)} AS right_table "
        f"ON left_table.{quote_identifier(candidate.left.column_name)} = "
        f"right_table.{quote_identifier(candidate.right.column_name)} "
        f"WHERE left_table.{quote_identifier(candidate.left.column_name)} IS NOT NULL "
        f"AND right_table.{quote_identifier(candidate.right.column_name)} IS NOT NULL "
        "LIMIT 1"
    )
    try:
        result = query_engine.query(sql, max_rows=1)
    except Exception:
        return False
    return bool(result.rows)


def _join_pair(candidate: _JoinCandidate, *, unique_tolerance: float) -> dict[str, Any]:
    left, right = _orient(candidate.left, candidate.right)
    return {
        "left_table": left.table_name,
        "left_column": left.column_name,
        "right_table": right.table_name,
        "right_column": right.column_name,
        "join_type": _join_type(left, right, unique_tolerance=unique_tolerance),
        "confidence": _confidence(candidate),
    }


def _orient(left: _ColumnInfo, right: _ColumnInfo) -> tuple[_ColumnInfo, _ColumnInfo]:
    if left.column_name.endswith("_record") and right.column_name.endswith("_ref"):
        return right, left
    if left.column_name.endswith("_ref") and right.column_name.endswith("_record"):
        return left, right
    if _is_unique_like(left) and not _is_unique_like(right):
        return right, left
    return left, right


def _join_type(left: _ColumnInfo, right: _ColumnInfo, *, unique_tolerance: float) -> str:
    left_unique = _is_unique_like(left, unique_tolerance=unique_tolerance)
    right_unique = _is_unique_like(right, unique_tolerance=unique_tolerance)
    if left_unique and right_unique:
        return "one_to_one"
    if not left_unique and right_unique:
        return "many_to_one"
    if left_unique and not right_unique:
        return "one_to_many"
    return "many_to_many"


def _confidence(candidate: _JoinCandidate) -> str:
    if candidate.score >= 0.95:
        return "high"
    if candidate.explicit:
        return "medium"
    return "low"


def _column_index(
    *,
    repository: LearnedArtifactRepository,
    query_engine: QueryEngine,
) -> dict[str, list[_ColumnInfo]]:
    index: dict[str, list[_ColumnInfo]] = {}
    try:
        profile = repository.load_profile()
    except AgentArtifactError:
        profile = {"tables": []}

    for table in profile.get("tables", []):
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("table_name", ""))
        row_count = _optional_int(table.get("row_count"))
        columns = []
        for column in table.get("columns", []):
            if not isinstance(column, dict):
                continue
            columns.append(
                _ColumnInfo(
                    table_name=table_name,
                    column_name=str(column.get("column_name", "")),
                    data_type=str(column.get("data_type", "")),
                    row_count=row_count,
                    distinct_count=_optional_int(column.get("distinct_count")),
                )
            )
        if table_name and columns:
            index[table_name] = columns

    for table_name in query_engine.list_tables():
        if table_name in index:
            continue
        index[table_name] = [
            _ColumnInfo(
                table_name=table_name,
                column_name=column.name,
                data_type=column.data_type,
            )
            for column in query_engine.describe_table(table_name)
        ]
    return index


def _schema_context(
    *,
    repository: LearnedArtifactRepository,
    query_engine: QueryEngine,
    table_names: list[str | None],
) -> dict[str, object]:
    schema = _column_index(repository=repository, query_engine=query_engine)
    requested = [table for table in table_names if table]
    if not requested:
        requested = sorted(schema)
    return {
        table: {
            "columns": [
                {"name": column.column_name, "data_type": column.data_type}
                for column in schema.get(table, [])
            ]
        }
        for table in requested
    }


def _type_compatible(left_type: str, right_type: str) -> bool:
    left = _type_family(left_type)
    right = _type_family(right_type)
    if left == "unknown" or right == "unknown":
        return True
    if left == right:
        return True
    return left == "numeric" and right == "numeric"


def _type_family(data_type: str) -> str:
    clean = data_type.lower()
    if any(token in clean for token in ["int", "decimal", "double", "float", "numeric", "number"]):
        return "numeric"
    if any(token in clean for token in ["char", "text", "string", "varchar"]):
        return "string"
    if "date" in clean:
        return "date"
    if "time" in clean:
        return "time"
    if "bool" in clean:
        return "boolean"
    return "unknown"


def _is_unique_like(column: _ColumnInfo, unique_tolerance: float = 0.02) -> bool:
    if column.row_count is None or column.row_count <= 0 or column.distinct_count is None:
        return False
    return column.distinct_count >= int(column.row_count * (1.0 - unique_tolerance))


def _looks_identifier_like(column_name: str) -> bool:
    return (
        column_name.endswith("_id")
        or column_name.endswith("_ref")
        or column_name.endswith("_record")
        or column_name == "id"
    )


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", value.lower()) if token]


def _has_suffix(tokens: list[str], suffix: list[str]) -> bool:
    if not tokens or not suffix or len(suffix) > len(tokens):
        return False
    return tokens[-len(suffix):] == suffix


def _singular_tokens(table_name: str) -> list[str]:
    return [_singular(token) for token in _tokens(table_name)]


def _singular(token: str) -> str:
    if token.endswith("ies") and len(token) > 3:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 3:
        return token[:-2]
    if token.endswith("s") and len(token) > 1:
        return token[:-1]
    return token


def _pair_key(pair: dict[str, Any]) -> tuple[tuple[str, str], tuple[str, str]]:
    left = (str(pair["left_table"]), str(pair["left_column"]))
    right = (str(pair["right_table"]), str(pair["right_column"]))
    first, second = sorted([left, right])
    return first, second


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    return clean or None
