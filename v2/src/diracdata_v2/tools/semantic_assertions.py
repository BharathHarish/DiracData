"""Generic SQL evidence tools for semantic assertion checks."""

from __future__ import annotations

import re
from numbers import Number
from typing import Any

from pydantic import BaseModel, Field

from diracdata_v2.query import DuckDBEngine
from diracdata_v2.semantic_catalog.sql_analysis import analyze_sql_references
from diracdata_v2.tools.sql import validate_sql


class SQLSemanticProfileInput(BaseModel):
    sql: str = Field(description="Read-only SQL to profile.")
    reference_sql: str | None = Field(
        default=None,
        description="Optional known-good SQL pattern to diff against this SQL.",
    )


class SQLSanityProbeInput(BaseModel):
    sql: str = Field(description="Read-only SQL to probe.")
    probe_type: str = Field(default="row_count", description="row_count or preview.")
    max_rows: int | None = Field(default=10, description="Preview rows for probe_type=preview.")


class DataAvailabilityProbeInput(BaseModel):
    sql: str = Field(description="Small read-only SQL that represents an intent-critical data existence check.")
    purpose: str = Field(
        default="",
        description="Short reason for the check, for example table/date/entity availability.",
    )


def build_sql_semantic_profile_tool(*, engine: DuckDBEngine, dialect: str = "duckdb") -> object:
    from langchain.tools import tool

    @tool("sql_semantic_profile", args_schema=SQLSemanticProfileInput)
    def sql_semantic_profile(sql: str, reference_sql: str | None = None) -> dict[str, Any]:
        """Profile SQL structure for Steward semantic checks without judging correctness."""
        table_columns = _table_columns(engine)
        profile = profile_sql(
            sql=sql, table_columns=table_columns, available_tables=set(table_columns), dialect=dialect
        )
        if reference_sql:
            reference = profile_sql(
                sql=reference_sql,
                table_columns=table_columns,
                available_tables=set(table_columns),
                dialect=dialect,
            )
            profile["reference_sql"] = _compact_reference(reference)
            profile["reference_diff"] = _reference_diff(profile, reference)
        profile["guidance"] = (
            "Use this as evidence only. It does not prove semantic correctness; compare it "
            "against the approved intent, learned pattern, value probes, and domain definitions."
        )
        return profile

    return sql_semantic_profile


def build_sql_sanity_probe_tool(*, engine: DuckDBEngine) -> object:
    from langchain.tools import tool

    @tool("sql_sanity_probe", args_schema=SQLSanityProbeInput)
    def sql_sanity_probe(sql: str, probe_type: str = "row_count", max_rows: int | None = 10) -> dict[str, Any]:
        """Run a small read-only row-count or preview probe for semantic validation."""
        clean = sql.strip().rstrip(";")
        validation = validate_sql(clean, available_tables=set(engine.list_tables()))
        if validation["status"] != "ok":
            return {"status": "error", "sql": clean, **validation}
        probe = probe_type.strip().lower()
        try:
            if probe == "row_count":
                return {"status": "ok", "probe_type": "row_count", "sql": clean, "row_count": engine.count_rows(clean)}
            if probe == "preview":
                limit = max(1, min(int(max_rows or 10), 100))
                result = engine.query(clean, max_rows=limit)
                return {
                    "status": "ok",
                    "probe_type": "preview",
                    "sql": clean,
                    "columns": result.columns,
                    "rows": [dict(zip(result.columns, row, strict=False)) for row in result.rows],
                    "preview_row_count": len(result.rows),
                }
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "sql": clean, "probe_type": probe, "error": str(exc)}
        return {"status": "error", "sql": clean, "error": "probe_type must be row_count or preview"}

    return sql_sanity_probe


def build_data_availability_probe_tool(*, engine: DuckDBEngine) -> object:
    from langchain.tools import tool

    @tool("data_availability_probe", args_schema=DataAvailabilityProbeInput)
    def data_availability_probe(sql: str, purpose: str = "") -> dict[str, Any]:
        """Run a tiny read-only existence probe for intent-critical table/date availability."""
        clean = sql.strip().rstrip(";")
        validation = validate_sql(clean, available_tables=set(engine.list_tables()))
        if validation["status"] != "ok":
            return {"status": "error", "sql": clean, "purpose": purpose, **validation}
        try:
            result = engine.query(clean, max_rows=5)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "sql": clean, "purpose": purpose, "error": str(exc)}
        evidence = _availability_evidence(sql=clean, columns=result.columns, rows=result.rows)
        return {
            "status": "ok",
            "purpose": purpose,
            "sql": clean,
            **evidence,
            "guidance": (
                "Use this only to decide whether an intent-critical table/date/entity slice exists. "
                "If available is false for a required clause, return NEEDS_CLARIFICATION or FAIL before SQL authoring."
            ),
        }

    return data_availability_probe


def profile_sql(
    *,
    sql: str,
    table_columns: dict[str, list[str]],
    available_tables: set[str],
    dialect: str = "duckdb",
) -> dict[str, Any]:
    clean = sql.strip().rstrip(";")
    validation = validate_sql(clean, available_tables=available_tables)
    if validation["status"] != "ok":
        return {"status": "error", "sql": clean, **validation}
    analysis = analyze_sql_references(clean, table_columns, dialect=dialect)
    parsed = _parse_profile(clean, table_columns=table_columns, dialect=dialect)
    return {
        "status": "ok",
        "sql": clean,
        "parser": analysis.parser,
        "tables": list(analysis.tables),
        "columns": list(analysis.columns),
        "join_edges": [pair.to_dict() for pair in analysis.join_pairs],
        **parsed,
    }


def _table_columns(engine: DuckDBEngine) -> dict[str, list[str]]:
    return {table: engine.list_columns(table) for table in engine.list_tables()}


def _availability_evidence(*, sql: str, columns: list[str], rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    preview_rows = [dict(zip(columns, row, strict=False)) for row in rows]
    if not rows:
        return {
            "observed_row_count": 0,
            "matched_row_count": 0,
            "row_count": 0,
            "available": False,
            "availability_basis": "no_rows",
            "columns": columns,
            "rows": preview_rows,
        }
    if len(rows) == 1:
        values = list(rows[0])
        if all(value is None for value in values):
            return {
                "observed_row_count": 1,
                "matched_row_count": 0,
                "row_count": 0,
                "available": False,
                "availability_basis": "all_values_null",
                "columns": columns,
                "rows": preview_rows,
            }
        if re.search(r"\bcount\s*\(", sql, flags=re.IGNORECASE) and all(_is_number_or_none(value) for value in values):
            numeric_values = [float(value) for value in values if _is_number_or_none(value) and value is not None]
            matched_row_count = int(max(numeric_values)) if numeric_values else 0
            return {
                "observed_row_count": 1,
                "matched_row_count": matched_row_count,
                "row_count": matched_row_count,
                "available": matched_row_count > 0,
                "availability_basis": "aggregate_count",
                "columns": columns,
                "rows": preview_rows,
            }
    return {
        "observed_row_count": len(rows),
        "matched_row_count": len(rows),
        "row_count": len(rows),
        "available": True,
        "availability_basis": "preview_rows",
        "columns": columns,
        "rows": preview_rows,
    }


def _is_number_or_none(value: Any) -> bool:
    return value is None or isinstance(value, Number)


def _parse_profile(sql: str, *, table_columns: dict[str, list[str]], dialect: str = "duckdb") -> dict[str, Any]:
    try:
        return _parse_with_sqlglot(sql, table_columns=table_columns, dialect=dialect)
    except Exception:  # noqa: BLE001
        return _parse_with_regex(sql, table_columns=table_columns)


def _parse_with_sqlglot(
    sql: str, *, table_columns: dict[str, list[str]], dialect: str = "duckdb"
) -> dict[str, Any]:
    import sqlglot
    from sqlglot import expressions as exp

    expression = sqlglot.parse_one(sql, read=dialect)
    aliases = _sqlglot_aliases(expression=expression, table_columns=table_columns)
    predicates = []
    for node in expression.find_all(exp.Predicate):
        predicate = _predicate_from_expression(node, aliases=aliases, table_columns=table_columns)
        if predicate is not None:
            predicates.append(predicate)
    group_by = []
    for group in expression.find_all(exp.Group):
        for item in group.expressions:
            group_by.append(_expression_sql(item))
    order_by = []
    for order in expression.find_all(exp.Order):
        for item in order.expressions:
            order_by.append(_expression_sql(item))
    return {
        "cte_count": len(list(expression.find_all(exp.CTE))),
        "predicates": _dedupe_dicts(predicates),
        "aggregates": _aggregate_calls(expression),
        "group_by": _dedupe_strings(group_by),
        "order_by": _dedupe_strings(order_by),
        "negative_constructs": _negative_constructs(sql),
    }


def _predicate_from_expression(
    node: Any,
    *,
    aliases: dict[str, str],
    table_columns: dict[str, list[str]],
) -> dict[str, Any] | None:
    from sqlglot import expressions as exp

    operator = node.key.upper()
    if isinstance(node, exp.In):
        column = _column_ref(node.this, aliases=aliases, table_columns=table_columns)
        if not column:
            return None
        return {
            "column": column,
            "operator": "IN",
            "values": [_literal_value(item) for item in node.expressions if _literal_value(item) is not None],
            "sql": _expression_sql(node),
        }
    if isinstance(node, (exp.Like, exp.ILike)):
        column = _column_ref(node.this, aliases=aliases, table_columns=table_columns)
        value = _literal_value(node.expression)
        if not column:
            return None
        return {"column": column, "operator": operator, "values": [value] if value is not None else [], "sql": _expression_sql(node)}
    if not hasattr(node, "left") or not hasattr(node, "right"):
        return None
    left_ref = _column_ref(node.left, aliases=aliases, table_columns=table_columns)
    right_ref = _column_ref(node.right, aliases=aliases, table_columns=table_columns)
    left_literal = _literal_value(node.left)
    right_literal = _literal_value(node.right)
    if left_ref and right_literal is not None:
        return {"column": left_ref, "operator": operator, "values": [right_literal], "sql": _expression_sql(node)}
    if right_ref and left_literal is not None:
        return {"column": right_ref, "operator": operator, "values": [left_literal], "sql": _expression_sql(node)}
    if left_ref and right_ref:
        return {"column": left_ref, "operator": operator, "right_column": right_ref, "values": [], "sql": _expression_sql(node)}
    return None


def _aggregate_calls(expression: Any) -> list[str]:
    from sqlglot import expressions as exp

    output = []
    for node in expression.walk():
        item = node[0] if isinstance(node, tuple) else node
        if isinstance(item, exp.AggFunc):
            output.append(_expression_sql(item))
    return _dedupe_strings(output)


def _sqlglot_aliases(*, expression: Any, table_columns: dict[str, list[str]]) -> dict[str, str]:
    from sqlglot import expressions as exp

    table_lookup = {table.lower(): table for table in table_columns}
    aliases: dict[str, str] = {}
    for table in expression.find_all(exp.Table):
        actual = table_lookup.get(table.name.lower())
        if actual is None:
            continue
        aliases[actual.lower()] = actual
        if table.alias:
            aliases[table.alias.lower()] = actual
    return aliases


def _column_ref(column: Any, *, aliases: dict[str, str], table_columns: dict[str, list[str]]) -> str | None:
    try:
        from sqlglot import expressions as exp
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(column, exp.Column):
        return None
    qualifier = str(column.table or "").lower()
    column_name = str(column.name or "")
    if qualifier:
        table = aliases.get(qualifier)
        if table is None:
            return None
        lookup = {item.lower(): item for item in table_columns.get(table, [])}
        actual = lookup.get(column_name.lower())
        return f"{table}.{actual}" if actual else None
    matches = []
    for table, columns in table_columns.items():
        lookup = {item.lower(): item for item in columns}
        actual = lookup.get(column_name.lower())
        if actual:
            matches.append(f"{table}.{actual}")
    return matches[0] if len(matches) == 1 else None


def _literal_value(node: Any) -> str | int | float | bool | None:
    try:
        from sqlglot import expressions as exp
    except Exception:  # noqa: BLE001
        return None
    if isinstance(node, exp.Literal):
        if node.is_string:
            return str(node.this)
        text = str(node.this)
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return text
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    return None


def _parse_with_regex(sql: str, *, table_columns: dict[str, list[str]]) -> dict[str, Any]:
    predicates = []
    for qualifier, column, value in re.findall(
        r"\b([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)\s*=\s*'([^']*)'",
        sql,
        flags=re.IGNORECASE,
    ):
        ref = _regex_column_ref(qualifier=qualifier, column=column, table_columns=table_columns)
        if ref:
            predicates.append({"column": ref, "operator": "EQ", "values": [value], "sql": f"{qualifier}.{column} = '{value}'"})
    return {
        "cte_count": len(re.findall(r"(?:with|,)\s+[a-zA-Z_][\w]*\s+as\s*\(", sql, flags=re.IGNORECASE)),
        "predicates": _dedupe_dicts(predicates),
        "aggregates": _dedupe_strings(re.findall(r"\b(?:count|sum|avg|min|max)\s*\([^)]*\)", sql, flags=re.IGNORECASE)),
        "group_by": [],
        "order_by": [],
        "negative_constructs": _negative_constructs(sql),
    }


def _regex_column_ref(*, qualifier: str, column: str, table_columns: dict[str, list[str]]) -> str | None:
    table_lookup = {table.lower(): table for table in table_columns}
    table = table_lookup.get(qualifier.lower())
    if table is None:
        return None
    column_lookup = {item.lower(): item for item in table_columns.get(table, [])}
    actual = column_lookup.get(column.lower())
    return f"{table}.{actual}" if actual else None


def _negative_constructs(sql: str) -> list[str]:
    lowered = sql.lower()
    constructs = []
    if re.search(r"\bnot\s+exists\s*\(", lowered):
        constructs.append("not_exists")
    if re.search(r"\bnot\s+in\s*\(", lowered):
        constructs.append("not_in")
    if re.search(r"\bexcept\b", lowered):
        constructs.append("except")
    if re.search(r"\bleft\s+join\b[\s\S]+\bis\s+null\b", lowered):
        constructs.append("left_join_is_null")
    return constructs


def _reference_diff(profile: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    reference_filter_slots = _placeholder_filter_columns(str(reference.get("sql") or ""))
    profile_predicate_columns = _predicate_columns(profile)
    reference_group_by = set(map(_normalize_sql_fragment, reference.get("group_by", [])))
    profile_group_by = set(map(_normalize_sql_fragment, profile.get("group_by", [])))
    reference_aggregates = set(map(_normalize_aggregate_signature, reference.get("aggregates", [])))
    profile_aggregates = set(map(_normalize_aggregate_signature, profile.get("aggregates", [])))
    return {
        "tables_added": sorted(set(profile.get("tables", [])) - set(reference.get("tables", []))),
        "tables_missing": sorted(set(reference.get("tables", [])) - set(profile.get("tables", []))),
        "columns_added": sorted(set(profile.get("columns", [])) - set(reference.get("columns", []))),
        "columns_missing": sorted(set(reference.get("columns", [])) - set(profile.get("columns", []))),
        "join_edges_added": _edge_diff(profile, reference),
        "join_edges_missing": _edge_diff(reference, profile),
        "negative_constructs_changed": sorted(set(profile.get("negative_constructs", [])) ^ set(reference.get("negative_constructs", []))),
        "reference_filter_slots": sorted(reference_filter_slots),
        "filter_slots_missing": sorted(reference_filter_slots - profile_predicate_columns),
        "group_by_added": sorted(profile_group_by - reference_group_by),
        "group_by_missing": sorted(reference_group_by - profile_group_by),
        "aggregate_shapes_added": sorted(profile_aggregates - reference_aggregates),
        "aggregate_shapes_missing": sorted(reference_aggregates - profile_aggregates),
    }


def _placeholder_filter_columns(sql: str) -> set[str]:
    columns = set()
    for qualifier, column in re.findall(
        r"\b([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)\s*(?:=|<>|!=|>|>=|<|<=|in\s*)\s*\{\{[^}]+\}\}",
        sql,
        flags=re.IGNORECASE,
    ):
        columns.add(f"{qualifier}.{column}".lower())
    return columns


def _predicate_columns(profile: dict[str, Any]) -> set[str]:
    columns = set()
    for predicate in profile.get("predicates", []):
        if not isinstance(predicate, dict):
            continue
        values = predicate.get("values")
        if not values:
            continue
        column = str(predicate.get("sql") or predicate.get("column") or "").lower()
        match = re.search(r"\b([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)\b", column)
        if match:
            columns.add(f"{match.group(1)}.{match.group(2)}")
            continue
        if predicate.get("column"):
            columns.add(str(predicate["column"]).lower())
    return columns


def _normalize_sql_fragment(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _normalize_aggregate_signature(value: Any) -> str:
    text = _normalize_sql_fragment(value)
    text = re.sub(r"\{\{[^}]+\}\}", "?", text)
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\{[^{}]*\}", "?", text)
    text = re.sub(r"'(?:''|[^'])*'", "?", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "?", text)
    return text


def _edge_diff(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    left_edges = {_edge_key(edge) for edge in left.get("join_edges", []) if isinstance(edge, dict)}
    right_edges = {_edge_key(edge) for edge in right.get("join_edges", []) if isinstance(edge, dict)}
    return sorted(left_edges - right_edges)


def _edge_key(edge: dict[str, Any]) -> str:
    columns = sorted([str(edge.get("left_column") or ""), str(edge.get("right_column") or "")])
    return "=".join(columns)


def _compact_reference(reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": reference.get("status"),
        "tables": reference.get("tables", []),
        "columns": reference.get("columns", []),
        "join_edges": reference.get("join_edges", []),
        "predicates": reference.get("predicates", []),
        "negative_constructs": reference.get("negative_constructs", []),
    }


def _expression_sql(expression: Any) -> str:
    try:
        return expression.sql(dialect="duckdb")
    except Exception:  # noqa: BLE001
        return str(expression)


def _dedupe_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _dedupe_dicts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    output = []
    for value in values:
        key = tuple(sorted((item, jsonish) for item, jsonish in _jsonable(value).items()))
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def _jsonable(value: dict[str, Any]) -> dict[str, str]:
    return {str(key): repr(item) for key, item in value.items()}
