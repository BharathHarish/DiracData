"""Read-only SQL execution tool for v2."""

from __future__ import annotations

import re
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel, Field

from diracdata_v2.artifacts import ResultArtifactWriter, profile_result_rows, rows_to_dicts
from diracdata_v2.query import DuckDBEngine
from diracdata_v2.settings import V2Settings
from diracdata_v2.storage import object_store_from_settings


class ExecuteSQLInput(BaseModel):
    sql: str = Field(description="Read-only DuckDB SELECT/WITH SQL, or EXPLAIN SELECT/WITH SQL.")
    max_rows: int | None = Field(default=None, description="Optional max rows to return.")


class SQLDryRunInput(BaseModel):
    sql: str = Field(description="Read-only DuckDB SELECT/WITH SQL. The tool runs EXPLAIN only.")


class QueryResultArtifactInput(BaseModel):
    manifest_key: str = Field(description="Object-store key for a SQL result artifact manifest.json.")
    sql: str = Field(
        description=(
            "Read-only DuckDB SELECT/WITH SQL over the persisted result snapshot. "
            "The artifact rows are exposed as table `result_rows`."
        )
    )
    max_rows: int | None = Field(default=None, description="Optional max preview rows to return.")


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

    result_writer = None
    if settings.result_artifacts_enabled:
        result_writer = ResultArtifactWriter(
            object_store=object_store_from_settings(settings),
            prefix=settings.result_artifact_prefix,
        )

    @tool("execute_sql", args_schema=ExecuteSQLInput)
    def execute_sql(sql: str, max_rows: int | None = None) -> dict[str, Any]:
        """Execute safe read-only SQL against the scoped local DuckDB views."""
        clean = sql.strip().rstrip(";")
        validation = validate_sql(clean, available_tables=set(engine.list_tables()))
        if validation["status"] != "ok":
            return {"status": "error", "sql": clean, **validation}
        try:
            preview_limit = max_rows or settings.agent_sql_max_rows
            result = engine.query(clean, max_rows=preview_limit)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "sql": clean, "error": str(exc)}
        preview_rows = rows_to_dicts(result.columns, result.rows)
        response: dict[str, Any] = {
            "status": "ok",
            "sql": clean,
            "columns": result.columns,
            "rows": preview_rows,
            "row_count": len(result.rows),
            "preview_row_count": len(result.rows),
        }
        if _is_result_artifact_candidate(clean) and result_writer is not None:
            response.update(
                _result_artifact_payload(
                    settings=settings,
                    engine=engine,
                    writer=result_writer,
                    sql=clean,
                    columns=result.columns,
                    preview_rows=preview_rows,
                )
            )
        elif _is_result_artifact_candidate(clean):
            response["result_profile"] = profile_result_rows(
                columns=result.columns,
                rows=preview_rows,
                total_row_count=len(result.rows),
                max_rows=len(result.rows),
            )
            response["total_row_count"] = len(result.rows)
        return {
            **response,
        }

    return execute_sql


def build_query_result_artifact_tool(*, settings: V2Settings) -> object:
    from langchain.tools import tool

    object_store = object_store_from_settings(settings)

    @tool("query_result_artifact", args_schema=QueryResultArtifactInput)
    def query_result_artifact(manifest_key: str, sql: str, max_rows: int | None = None) -> dict[str, Any]:
        """Run read-only DuckDB SQL over a persisted SQL result artifact."""
        clean = sql.strip().rstrip(";")
        validation = validate_sql(clean, available_tables={"result_rows"})
        if validation["status"] != "ok":
            return {"status": "error", "sql": clean, **validation}
        try:
            manifest = object_store.read_json(manifest_key)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "manifest_key": manifest_key, "sql": clean, "error": str(exc)}
        if not isinstance(manifest, dict):
            return {"status": "error", "manifest_key": manifest_key, "sql": clean, "error": "Invalid manifest"}
        rows_key = manifest.get("rows_key")
        if not isinstance(rows_key, str) or not rows_key.strip():
            return {
                "status": "error",
                "manifest_key": manifest_key,
                "sql": clean,
                "error": "Manifest missing rows_key",
            }
        try:
            rows_jsonl = object_store.read_text(rows_key)
            return _query_result_rows_jsonl(
                sql=clean,
                rows_jsonl=rows_jsonl,
                manifest_key=manifest_key,
                manifest=manifest,
                max_rows=max_rows or settings.agent_sql_max_rows,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "manifest_key": manifest_key,
                "rows_key": rows_key,
                "sql": clean,
                "error": str(exc),
            }

    return query_result_artifact


def _query_result_rows_jsonl(
    *,
    sql: str,
    rows_jsonl: str,
    manifest_key: str,
    manifest: dict[str, Any],
    max_rows: int,
) -> dict[str, Any]:
    import duckdb

    with TemporaryDirectory() as tmp:
        rows_path = f"{tmp}/rows.jsonl"
        with open(rows_path, "w", encoding="utf-8") as handle:
            handle.write(rows_jsonl)
        con = duckdb.connect(":memory:")
        try:
            con.execute(
                f"CREATE VIEW result_rows AS SELECT * FROM read_json_auto('{_duckdb_string(rows_path)}')"
            )
            count_sql = f"SELECT COUNT(*) AS row_count FROM ({sql}) AS diracdata_result_artifact_query"
            total_row_count = int(con.execute(count_sql).fetchone()[0])
            cursor = con.execute(sql)
            columns = [column[0] for column in cursor.description or []]
            rows = cursor.fetchmany(max(1, int(max_rows)))
        finally:
            con.close()
    preview_rows = rows_to_dicts(columns, rows)
    return {
        "status": "ok",
        "sql": sql,
        "columns": columns,
        "rows": preview_rows,
        "row_count": len(preview_rows),
        "preview_row_count": len(preview_rows),
        "total_row_count": total_row_count,
        "result_profile": profile_result_rows(
            columns=columns,
            rows=preview_rows,
            total_row_count=total_row_count,
            max_rows=len(preview_rows),
        ),
        "source_artifact": {
            "artifact_id": manifest.get("artifact_id"),
            "manifest_key": manifest_key,
            "rows_key": manifest.get("rows_key"),
            "total_row_count": manifest.get("row_count"),
            "stored_row_count": manifest.get("stored_row_count"),
            "truncated": manifest.get("truncated"),
            "conversation_id": manifest.get("conversation_id"),
            "thread_id": manifest.get("thread_id"),
        },
    }


def _result_artifact_payload(
    *,
    settings: V2Settings,
    engine: DuckDBEngine,
    writer: ResultArtifactWriter,
    sql: str,
    columns: list[str],
    preview_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    artifact_limit = max(1, settings.result_artifact_max_rows)
    try:
        total_row_count = engine.count_rows(sql)
        artifact_result = engine.query(sql, max_rows=artifact_limit)
        artifact = writer.write_result(
            schema=settings.schema,
            conversation_id=settings.conversation_id,
            thread_id=settings.thread_id,
            sql=sql,
            columns=artifact_result.columns,
            rows=artifact_result.rows,
            total_row_count=total_row_count,
            max_rows=artifact_limit,
        )
        return {
            "total_row_count": total_row_count,
            "result_profile": profile_result_rows(
                columns=artifact_result.columns,
                rows=rows_to_dicts(artifact_result.columns, artifact_result.rows),
                total_row_count=total_row_count,
                max_rows=artifact_limit,
            ),
            "result_artifact": artifact,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "total_row_count": len(preview_rows),
            "result_profile": profile_result_rows(
                columns=columns,
                rows=preview_rows,
                total_row_count=len(preview_rows),
                max_rows=len(preview_rows),
            ),
            "result_artifact_error": f"{type(exc).__name__}: {exc}",
        }


def _is_result_artifact_candidate(sql: str) -> bool:
    validation_sql = _strip_sql_comments(sql).strip()
    if validation_sql.lower().lstrip().startswith("explain"):
        return False
    return _strip_explain_prefix(validation_sql) is not None


def _duckdb_string(value: str) -> str:
    return value.replace("'", "''")


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
