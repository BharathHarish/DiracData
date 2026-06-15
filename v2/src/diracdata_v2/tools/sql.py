"""Read-only SQL execution tool for v2."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from diracdata_v2.query import DuckDBEngine
from diracdata_v2.settings import V2Settings


class ExecuteSQLInput(BaseModel):
    sql: str = Field(description="Read-only DuckDB SELECT/WITH SQL, or EXPLAIN SELECT/WITH SQL.")
    max_rows: int | None = Field(default=None, description="Optional max rows to return.")


class SQLDryRunInput(BaseModel):
    sql: str = Field(description="Read-only DuckDB SELECT/WITH SQL. The tool runs EXPLAIN only.")


DISALLOWED = {
    "alter",
    "analyze",
    "attach",
    "call",
    "copy",
    "create",
    "delete",
    "detach",
    "drop",
    "insert",
    "install",
    "load",
    "pragma",
    "replace",
    "truncate",
    "update",
}


def build_execute_sql_tool(*, settings: V2Settings, engine: DuckDBEngine) -> object:
    from langchain.tools import tool

    @tool("execute_sql", args_schema=ExecuteSQLInput)
    def execute_sql(sql: str, max_rows: int | None = None) -> dict[str, Any]:
        """Execute safe read-only SQL against the scoped local DuckDB views."""
        clean = sql.strip().rstrip(";")
        validation = validate_sql(clean, available_tables=set(engine.list_tables()))
        if validation["status"] != "ok":
            return {"status": "error", "sql": clean, **validation}
        try:
            result = engine.query(clean, max_rows=max_rows or settings.agent_sql_max_rows)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "sql": clean, "error": str(exc)}
        return {
            "status": "ok",
            "sql": clean,
            "columns": result.columns,
            "rows": [dict(zip(result.columns, row, strict=False)) for row in result.rows],
            "row_count": len(result.rows),
        }

    return execute_sql


def build_sql_dry_run_tool(*, engine: DuckDBEngine) -> object:
    from langchain.tools import tool

    @tool("sql_dry_run", args_schema=SQLDryRunInput)
    def sql_dry_run(sql: str) -> dict[str, Any]:
        """Validate SQL and run EXPLAIN without executing the query body."""
        clean = sql.strip().rstrip(";")
        validation = validate_sql(clean, available_tables=set(engine.list_tables()))
        if validation["status"] != "ok":
            return {"status": "error", "sql": clean, **validation}
        explain_sql = _to_explain_sql(clean)
        if explain_sql is None:
            return {
                "status": "error",
                "sql": clean,
                "error": "sql_dry_run accepts only SELECT/WITH or EXPLAIN SELECT/WITH SQL",
            }
        try:
            result = engine.query(explain_sql, max_rows=20)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "sql": clean, "explain_sql": explain_sql, "error": str(exc)}
        return {
            "status": "ok",
            "sql": clean,
            "explain_sql": explain_sql,
            "columns": result.columns,
            "rows": [dict(zip(result.columns, row, strict=False)) for row in result.rows],
            "row_count": len(result.rows),
        }

    return sql_dry_run


def validate_sql(sql: str, *, available_tables: set[str]) -> dict[str, Any]:
    validation_sql = _strip_sql_comments(sql).strip()
    if not validation_sql:
        return {"status": "error", "error": "SQL cannot be empty"}
    lowered = validation_sql.lower()
    for token in DISALLOWED:
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return {"status": "error", "error": f"Disallowed SQL token: {token}"}
    semantic_sql = _strip_explain_prefix(validation_sql)
    if semantic_sql is None:
        return {"status": "error", "error": "Only SELECT/WITH or EXPLAIN SELECT/WITH SQL is allowed"}
    lowered = semantic_sql.lower()
    cte_names = _cte_names(lowered)
    referenced = {
        match
        for match in re.findall(r"\b(?:from|join)\s+([a-zA-Z_][\w]*)\b", lowered)
        if match not in {"select"}
    }
    unknown = sorted(referenced - available_tables - cte_names)
    if unknown:
        return {"status": "error", "error": "Unknown tables", "unknown_tables": unknown}
    return {"status": "ok"}


def _to_explain_sql(sql: str) -> str | None:
    validation_sql = _strip_sql_comments(sql).strip()
    semantic_sql = _strip_explain_prefix(validation_sql)
    if semantic_sql is None:
        return None
    if validation_sql.lower().lstrip().startswith("explain"):
        return validation_sql
    return f"EXPLAIN {validation_sql}"


def _strip_explain_prefix(sql: str) -> str | None:
    lowered = sql.lower()
    if re.match(r"^\s*(select|with)\b", lowered):
        return sql
    match = re.match(r"^\s*explain\s+(?P<body>.*)$", sql, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    body = match.group("body").strip()
    if re.match(r"^analyze\b", body, flags=re.IGNORECASE):
        return None
    if not re.match(r"^\s*(select|with)\b", body, flags=re.IGNORECASE):
        return None
    return body


def _cte_names(sql: str) -> set[str]:
    if not re.match(r"^\s*with\b", sql):
        return set()
    return set(re.findall(r"(?:with|,)\s*([a-zA-Z_][\w]*)\s+as\s*\(", sql))


def _strip_sql_comments(sql: str) -> str:
    without_line_comments = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
    return re.sub(r"/\*.*?\*/", "", without_line_comments, flags=re.DOTALL)
