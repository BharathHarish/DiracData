"""Candidate binding search tools for the data analyst agent."""

from __future__ import annotations

from pydantic import BaseModel, Field

from diracdata.retrieval import CandidateBindingSearchService


class CandidateSearchInput(BaseModel):
    nl_query: str = Field(
        description="The user's full natural-language business question.",
    )


def build_candidate_tools(
    *,
    service: CandidateBindingSearchService,
) -> list[object]:
    from langchain.tools import tool

    @tool("candidate_search_tool", args_schema=CandidateSearchInput)
    def candidate_search_tool(nl_query: str) -> dict[str, object]:
        """Resolve likely table/column candidates and rejected confounders for a user question."""
        return service.search(nl_query)

    return [candidate_search_tool]
