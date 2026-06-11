"""Column value grounding tool for v2."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from diracdata_v2.query import DuckDBEngine
from diracdata_v2.settings import V2Settings


class ColumnValuesInput(BaseModel):
    table_name: str = Field(description="Scoped table name returned by schema_search_ast.")
    column_name: str = Field(description="Column name returned by schema_search_ast.")
    search_text: str | None = Field(
        default=None,
        description="Optional user phrase to fuzzy-match against stored values.",
    )
    max_values: int | None = Field(default=None, description="Maximum values to return.")


def build_column_values_tool(*, settings: V2Settings, engine: DuckDBEngine) -> object:
    from langchain.tools import tool

    @tool("column_values", args_schema=ColumnValuesInput)
    def column_values(
        table_name: str,
        column_name: str,
        search_text: str | None = None,
        max_values: int | None = None,
    ) -> dict[str, Any]:
        """Return distinct values and counts for a scoped table column."""
        table = table_name.strip()
        column = column_name.strip()
        available_tables = set(engine.list_tables())
        if table not in available_tables:
            return {
                "status": "error",
                "error": "Unknown table",
                "table_name": table,
                "available_tables": sorted(available_tables),
            }
        available_columns = set(engine.list_columns(table))
        if column not in available_columns:
            return {
                "status": "error",
                "error": "Unknown column",
                "table_name": table,
                "column_name": column,
                "available_columns": sorted(available_columns),
            }

        limit = max(1, min(max_values or settings.agent_column_values_max_values, settings.agent_column_values_max_values))
        where = f"WHERE {_identifier(column)} IS NOT NULL"
        if search_text:
            where += f" AND CAST({_identifier(column)} AS VARCHAR) ILIKE '%{_sql_like(search_text)}%'"
        sql = (
            f"SELECT {_identifier(column)} AS value, COUNT(*) AS row_count "
            f"FROM {_identifier(table)} "
            f"{where} "
            f"GROUP BY 1 "
            f"ORDER BY row_count DESC, value "
            f"LIMIT {int(limit)}"
        )
        try:
            result = engine.query(sql, max_rows=limit)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "table_name": table, "column_name": column, "error": str(exc)}
        return {
            "status": "ok",
            "table_name": table,
            "column_name": column,
            "search_text": search_text,
            "values": [dict(zip(result.columns, row, strict=False)) for row in result.rows],
            "value_count": len(result.rows),
            "guidance": "Use exact `value` strings from this result when writing SQL predicates.",
        }

    return column_values


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_like(value: str) -> str:
    return value.replace("'", "''").replace("%", "\\%").replace("_", "\\_")
