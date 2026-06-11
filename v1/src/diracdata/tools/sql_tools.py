"""Read-only SQL execution tool for the data analyst agent."""

from __future__ import annotations

import re
import threading
from typing import Any

from typing import Literal

from pydantic import BaseModel, Field

from diracdata.config.settings import DiracDataSettings
from diracdata.learning.models import to_jsonable
from diracdata.query_engines.base import QueryEngine


DISALLOWED_SQL_TOKENS = {
    "alter",
    "attach",
    "call",
    "copy",
    "create",
    "delete",
    "detach",
    "drop",
    "export",
    "import",
    "insert",
    "install",
    "load",
    "pragma",
    "replace",
    "truncate",
    "update",
}
DISALLOWED_FUNCTIONS = {
    "glob",
    "read_blob",
    "read_csv",
    "read_json",
    "read_parquet",
    "sqlite_scan",
}


class RunSqlInput(BaseModel):
    sql: str = Field(description="Read-only SQL query to execute.")
    max_rows: int | None = Field(
        default=None,
        description="Optional maximum rows to return. Defaults to DIRACDATA_AGENT_SQL_MAX_ROWS.",
    )
    purpose: Literal["probe", "final"] | None = Field(
        default=None,
        description=(
            "Use 'probe' for verification SQL such as row counts, filter selectivity, "
            "join fanout, freshness, or key uniqueness. Use 'final' for the final "
            "business result query."
        ),
    )
    check_name: str | None = Field(
        default=None,
        description=(
            "For probe SQL, provide a concise check name such as base_population, "
            "filter_selectivity, join_fanout, freshness, dimension_quality, or key_uniqueness."
        ),
    )


def build_sql_tools(
    *,
    settings: DiracDataSettings,
    query_engine: QueryEngine,
) -> list[object]:
    from langchain.tools import tool

    query_lock = threading.RLock()

    @tool("run_sql_tool", args_schema=RunSqlInput)
    def run_sql_tool(
        sql: str,
        max_rows: int | None = None,
        purpose: str | None = None,
        check_name: str | None = None,
    ) -> dict[str, object]:
        """Execute safe read-only SQL and return rows or a repairable SQL error observation."""
        normalized_sql = normalize_sql(sql)
        normalized_purpose = _normalize_purpose(purpose)
        normalized_check_name = _normalize_check_name(check_name)
        try:
            effective_max_rows = _effective_max_rows(max_rows, settings.agent_sql_max_rows)
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
        with query_lock:
            validation = validate_read_only_sql(
                normalized_sql,
                available_tables=set(query_engine.list_tables()),
                sql_dialect=settings.sql_dialect,
            )
            if validation["status"] != "ok":
                return _repairable_error(
                    sql=normalized_sql,
                    error=str(validation.get("error", "SQL validation failed")),
                    sql_dialect=settings.sql_dialect,
                    purpose=normalized_purpose,
                    check_name=normalized_check_name,
                    extra=validation,
                )

            try:
                result = query_engine.query(normalized_sql, max_rows=effective_max_rows)
            except Exception as exc:  # noqa: BLE001 - tool returns model-readable execution errors
                return _repairable_error(
                    sql=normalized_sql,
                    error=str(exc),
                    sql_dialect=settings.sql_dialect,
                    purpose=normalized_purpose,
                    check_name=normalized_check_name,
                )

        rows = [
            {
                column: to_jsonable(value)
                for column, value in zip(result.columns, row, strict=False)
            }
            for row in result.rows
        ]
        return {
            "status": "ok",
            "sql": normalized_sql,
            "sql_dialect": settings.sql_dialect,
            "purpose": normalized_purpose,
            "check_name": normalized_check_name,
            "columns": result.columns,
            "rows": rows,
            "row_count": len(rows),
            "max_rows": effective_max_rows,
            "possibly_truncated": len(rows) >= effective_max_rows,
        }

    return [run_sql_tool]


def validate_read_only_sql(
    sql: str,
    *,
    available_tables: set[str],
    sql_dialect: str,
) -> dict[str, object]:
    clean_sql = normalize_sql(sql)
    if not clean_sql:
        return {"status": "error", "error": "SQL query cannot be empty"}

    token_match = _disallowed_token(clean_sql)
    if token_match is not None:
        return {
            "status": "error",
            "error": f"Only read-only SELECT SQL is allowed; found disallowed token {token_match!r}",
        }

    try:
        import sqlglot
        from sqlglot import exp

        expressions = sqlglot.parse(clean_sql, read=sql_dialect)
    except Exception as exc:  # noqa: BLE001 - validation error should be model-readable
        return {"status": "error", "error": f"SQL parse failed: {exc}"}

    if len(expressions) != 1:
        return {"status": "error", "error": "Only one SQL statement is allowed"}

    expression = expressions[0]
    if not isinstance(expression, (exp.Select, exp.Union, exp.Except, exp.Intersect)):
        return {"status": "error", "error": "Only read-only SELECT queries are allowed"}

    function_match = _disallowed_function(expression)
    if function_match is not None:
        return {
            "status": "error",
            "error": f"External read function {function_match!r} is not allowed",
        }

    referenced_tables = _referenced_tables(expression)
    cte_names = _cte_names(expression)
    unknown_tables = sorted(
        table
        for table in referenced_tables
        if table not in available_tables and table not in cte_names
    )
    if unknown_tables:
        return {
            "status": "error",
            "error": "SQL references tables outside the scoped pod",
            "unknown_tables": unknown_tables,
            "available_tables": sorted(available_tables),
        }

    return {"status": "ok"}


def normalize_sql(sql: str) -> str:
    """Normalize harmless formatting that commonly breaks wrapped dry-runs."""
    clean_sql = sql.strip()
    while clean_sql.endswith(";"):
        clean_sql = clean_sql[:-1].rstrip()
    return clean_sql


def _repairable_error(
    *,
    sql: str,
    error: str,
    sql_dialect: str,
    purpose: str | None,
    check_name: str | None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    error_type = classify_sql_error(error)
    payload: dict[str, object] = {
        "status": "error",
        "sql": sql,
        "sql_dialect": sql_dialect,
        "purpose": purpose,
        "check_name": check_name,
        "error": error,
        "error_type": error_type,
        "observation": (
            "SQL failed. Treat this as a tool observation, repair the SQL, "
            "and call run_sql_tool again before giving a final data answer."
        ),
        "repair_instruction": repair_instruction_for_sql_error(error_type),
    }
    if extra:
        payload.update(
            {
                key: value
                for key, value in extra.items()
                if key not in {"status", "error", "sql", "sql_dialect"}
            }
        )
    return payload


def _normalize_purpose(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized if normalized in {"probe", "final"} else None


def _normalize_check_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")
    return normalized or None


def classify_sql_error(error: str) -> str:
    """Classify common query-engine errors into model-actionable buckets."""
    normalized = error.lower()
    if "ambiguous reference" in normalized or "ambiguous column" in normalized:
        return "ambiguous_column"
    if "column" in normalized and (
        "not found" in normalized
        or "does not exist" in normalized
        or "could not be found" in normalized
        or "referenced column" in normalized
    ):
        return "unknown_column"
    if "table" in normalized and (
        "not found" in normalized
        or "does not exist" in normalized
        or "unknown table" in normalized
    ):
        return "unknown_table"
    if "parser error" in normalized or "syntax error" in normalized or "parse failed" in normalized:
        return "syntax_error"
    if "binder error" in normalized:
        return "binder_error"
    if "catalog error" in normalized:
        return "catalog_error"
    return "execution_error"


def repair_instruction_for_sql_error(error_type: str) -> str:
    """Return a concise SQL repair instruction for the agent loop."""
    instructions = {
        "ambiguous_column": (
            "Qualify the ambiguous column with the correct table alias and retry the query."
        ),
        "unknown_column": (
            "Inspect the scoped schema, replace the missing column with an available column, "
            "and retry the query."
        ),
        "unknown_table": (
            "Inspect the scoped table list, replace the missing table with an available table, "
            "and retry the query."
        ),
        "syntax_error": (
            "Rewrite the query in the configured SQL dialect and retry it as a single SELECT statement."
        ),
        "binder_error": (
            "Fix column qualification, aliases, grouping, or type issues reported by the engine and retry."
        ),
        "catalog_error": (
            "Use only tables available in the scoped catalog/schema and retry the query."
        ),
        "execution_error": (
            "Use the query-engine error to repair the SQL while preserving the business intent, then retry."
        ),
    }
    return instructions.get(error_type, instructions["execution_error"])


def _effective_max_rows(value: int | None, default: int) -> int:
    if value is None:
        return default
    if value <= 0:
        raise ValueError("max_rows must be greater than zero")
    return value


def _disallowed_token(sql: str) -> str | None:
    tokens = {token.lower() for token in re.findall(r"[a-z_]+", sql)}
    matches = tokens & DISALLOWED_SQL_TOKENS
    if not matches:
        return None
    return sorted(matches)[0]


def _disallowed_function(expression: Any) -> str | None:
    try:
        from sqlglot import exp
    except ImportError:
        return None

    for node in expression.walk():
        class_name = node.__class__.__name__.lower()
        if class_name in {name.replace("_", "") for name in DISALLOWED_FUNCTIONS}:
            return class_name
        if isinstance(node, exp.Anonymous):
            name = str(node.name).lower()
            if name in DISALLOWED_FUNCTIONS:
                return name
    return None


def _referenced_tables(expression: Any) -> set[str]:
    from sqlglot import exp

    return {table.name for table in expression.find_all(exp.Table) if table.name}


def _cte_names(expression: Any) -> set[str]:
    from sqlglot import exp

    names = set()
    for cte in expression.find_all(exp.CTE):
        if cte.alias:
            names.add(cte.alias)
    return names
