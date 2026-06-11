"""Business grounding tools for the data analyst agent."""

from __future__ import annotations

from pydantic import BaseModel, Field

from diracdata.grounding.business import BusinessGroundingError, BusinessGroundingRepository


class BusinessSearchInput(BaseModel):
    query: str = Field(description="Natural-language business term, metric, or policy to search.")
    limit: int | None = Field(default=None, description="Optional maximum number of matches.")


class BusinessLookupInput(BaseModel):
    id_or_term: str = Field(description="Business id, term, synonym, or phrase to look up.")


class MetricLookupInput(BaseModel):
    metric_id: str = Field(description="Metric id to inspect.")


class TemplateLookupInput(BaseModel):
    template_id: str = Field(description="SQL template id to inspect.")


def build_grounding_tools(
    *,
    repository: BusinessGroundingRepository,
    default_limit: int,
) -> list[object]:
    from langchain.tools import tool

    @tool("business_term_search_tool", args_schema=BusinessSearchInput)
    def business_term_search_tool(query: str, limit: int | None = None) -> dict[str, object]:
        """Search customer-supplied business definitions, metrics, defaults, and SQL patterns."""
        try:
            matches = repository.search(query, limit=_effective_limit(limit, default_limit))
            return {"status": "ok", "query": query, "matches": matches}
        except BusinessGroundingError as exc:
            return _not_found(str(exc))

    @tool("resolve_business_intent_tool", args_schema=BusinessSearchInput)
    def resolve_business_intent_tool(query: str, limit: int | None = None) -> dict[str, object]:
        """Resolve exact, typed business grounding that should be treated as binding."""
        try:
            resolution = repository.resolve_business_intent(
                query,
                limit=_effective_limit(limit, default_limit),
            )
            return {"status": "ok", **resolution}
        except BusinessGroundingError as exc:
            return _not_found(str(exc))

    @tool("get_business_definition_tool", args_schema=BusinessLookupInput)
    def get_business_definition_tool(id_or_term: str) -> dict[str, object]:
        """Return a business glossary, definition, or default policy by id or term."""
        try:
            value = repository.get_definition(id_or_term)
        except BusinessGroundingError as exc:
            return _not_found(str(exc))
        if value is None:
            return {"status": "not_found", "id_or_term": id_or_term}
        return {"status": "ok", "definition": value}

    @tool("get_metric_definition_tool", args_schema=MetricLookupInput)
    def get_metric_definition_tool(metric_id: str) -> dict[str, object]:
        """Return customer-supplied metric meaning, grain, and calculation guidance."""
        try:
            value = repository.get_metric(metric_id)
        except BusinessGroundingError as exc:
            return _not_found(str(exc))
        if value is None:
            return {"status": "not_found", "metric_id": metric_id}
        return {"status": "ok", "metric": value}

    @tool("get_sql_template_tool", args_schema=TemplateLookupInput)
    def get_sql_template_tool(template_id: str) -> dict[str, object]:
        """Return a trusted SQL template or pattern for a business question type."""
        try:
            value = repository.get_sql_template(template_id)
        except BusinessGroundingError as exc:
            return _not_found(str(exc))
        if value is None:
            return {"status": "not_found", "template_id": template_id}
        return {"status": "ok", "sql_template": value}

    @tool("get_default_policy_tool", args_schema=BusinessLookupInput)
    def get_default_policy_tool(id_or_term: str) -> dict[str, object]:
        """Return a default interpretation policy for ambiguous business wording."""
        try:
            value = repository.get_default_policy(id_or_term)
        except BusinessGroundingError as exc:
            return _not_found(str(exc))
        if value is None:
            return {"status": "not_found", "id_or_term": id_or_term}
        return {"status": "ok", "default_policy": value}

    return [
        business_term_search_tool,
        resolve_business_intent_tool,
        get_business_definition_tool,
        get_metric_definition_tool,
        get_sql_template_tool,
        get_default_policy_tool,
    ]


def _effective_limit(value: int | None, default: int) -> int:
    if value is None:
        return default
    return max(1, min(value, default))


def _not_found(message: str) -> dict[str, object]:
    return {
        "status": "not_found",
        "message": "business grounding artifact is not published",
        "error": message,
    }
