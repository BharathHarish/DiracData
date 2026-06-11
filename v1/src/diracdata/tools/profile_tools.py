"""Profile evidence tools for the data analyst agent."""

from __future__ import annotations

from pydantic import BaseModel, Field

from diracdata.agents.artifacts import AgentArtifactError, LearnedArtifactRepository


class ProfileColumnValuesInput(BaseModel):
    table_name: str = Field(description="Table name containing the column.")
    column_name: str = Field(description="Column name to inspect.")
    limit: int | None = Field(default=None, description="Optional maximum values to return.")


def build_profile_tools(
    *,
    repository: LearnedArtifactRepository,
    default_limit: int,
) -> list[object]:
    from langchain.tools import tool

    @tool("profile_column_values_tool", args_schema=ProfileColumnValuesInput)
    def profile_column_values_tool(
        table_name: str,
        column_name: str,
        limit: int | None = None,
    ) -> dict[str, object]:
        """Return learned profile values for one column, including top and distinct values."""
        try:
            effective_limit = _effective_limit(limit, default_limit)
            profile = repository.profile_column_values(
                table_name=table_name,
                column_name=column_name,
                limit=effective_limit,
            )
            if profile is None:
                return {
                    "status": "not_found",
                    "table_name": table_name,
                    "column_name": column_name,
                }
            return {"status": "ok", **profile}
        except AgentArtifactError as exc:
            return {"status": "error", "error": str(exc)}

    return [profile_column_values_tool]


def _effective_limit(value: int | None, default: int) -> int:
    if value is None:
        return default
    return max(1, min(value, default))
