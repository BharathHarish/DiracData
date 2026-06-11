"""Schema description tools for the data analyst agent."""

from __future__ import annotations

from pydantic import BaseModel, Field

from diracdata.agents.artifacts import AgentArtifactError, LearnedArtifactRepository


class SchemaSearchInput(BaseModel):
    query: str = Field(description="Natural-language search query over table and column meanings.")
    limit: int | None = Field(default=None, description="Optional maximum number of matches.")


class TableDescriptionsInput(BaseModel):
    table_name: str | None = Field(
        default=None,
        description="Optional table name. Leave empty to return all table short descriptions.",
    )


class TableColumnsInput(BaseModel):
    table_name: str = Field(description="Table name to inspect.")


class ColumnDescriptionInput(BaseModel):
    table_name: str = Field(description="Table name containing the column.")
    column_name: str = Field(description="Column name to inspect.")


def build_schema_tools(
    *,
    repository: LearnedArtifactRepository,
    default_limit: int,
) -> list[object]:
    from langchain.tools import tool

    @tool("schema_info_tool", args_schema=SchemaSearchInput)
    def schema_info_tool(query: str, limit: int | None = None) -> dict[str, object]:
        """Search learned table and column descriptions using business-language keywords."""
        try:
            effective_limit = _effective_limit(limit, default_limit)
            hits = repository.search_descriptions(query, limit=effective_limit)
            return {
                "status": "ok",
                "query": query,
                "matches": [
                    {
                        "kind": hit.kind,
                        "table_name": hit.table_name,
                        "column_name": hit.column_name,
                        "short_description": hit.short_description,
                        "score": hit.score,
                    }
                    for hit in hits
                ],
            }
        except AgentArtifactError as exc:
            return _error(str(exc))

    @tool("get_table_descriptions", args_schema=TableDescriptionsInput)
    def get_table_descriptions(table_name: str | None = None) -> dict[str, object]:
        """Return table short descriptions. If table_name is empty, return all tables."""
        try:
            descriptions = repository.table_descriptions(_blank_to_none(table_name))
            return {"status": "ok", "table_descriptions": descriptions}
        except AgentArtifactError as exc:
            return _error(str(exc))

    @tool("get_table_columns_tool", args_schema=TableColumnsInput)
    def get_table_columns_tool(table_name: str) -> dict[str, object]:
        """Return all columns for one table with short business descriptions."""
        try:
            columns = repository.table_columns(table_name)
            status = "ok" if columns else "not_found"
            return {"status": status, "table_name": table_name, "columns": columns}
        except AgentArtifactError as exc:
            return _error(str(exc))

    @tool("get_column_description", args_schema=ColumnDescriptionInput)
    def get_column_description(table_name: str, column_name: str) -> dict[str, object]:
        """Return a short business description for one column."""
        try:
            description = repository.column_description(table_name, column_name)
            if description is None:
                return {
                    "status": "not_found",
                    "table_name": table_name,
                    "column_name": column_name,
                }
            return {
                "status": "ok",
                "table_name": table_name,
                "column_name": column_name,
                "short_description": description,
            }
        except AgentArtifactError as exc:
            return _error(str(exc))

    return [
        schema_info_tool,
        get_table_descriptions,
        get_table_columns_tool,
        get_column_description,
    ]


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    return clean or None


def _effective_limit(value: int | None, default: int) -> int:
    if value is None:
        return default
    return max(1, min(value, default))


def _error(message: str) -> dict[str, object]:
    return {"status": "error", "error": message}
