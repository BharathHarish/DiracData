"""Build the full data analyst agent toolset."""

from __future__ import annotations

from diracdata.agents.artifacts import LearnedArtifactRepository
from diracdata.grounding.business import BusinessGroundingRepository
from diracdata.retrieval import CandidateBindingSearchService
from diracdata.tools.candidate_tools import build_candidate_tools
from diracdata.tools.grounding_tools import build_grounding_tools
from diracdata.tools.join_tools import build_join_tools
from diracdata.tools.profile_tools import build_profile_tools
from diracdata.tools.schema_tools import build_schema_tools
from diracdata.tools.sql_tools import build_sql_tools
from diracdata.config.settings import DiracDataSettings
from diracdata.query_engines.base import QueryEngine
from diracdata.storage.object_store import ObjectStore


def build_data_analyst_tools(
    *,
    settings: DiracDataSettings,
    object_store: ObjectStore,
    query_engine: QueryEngine,
    candidate_search_service: CandidateBindingSearchService | None = None,
) -> list[object]:
    repository = LearnedArtifactRepository(settings=settings, object_store=object_store)
    grounding_repository = BusinessGroundingRepository(
        settings=settings,
        object_store=object_store,
    )
    tools: list[object] = []
    if settings.agent_candidate_search_enabled:
        tools.extend(
            build_candidate_tools(
                service=candidate_search_service
                or CandidateBindingSearchService(
                    settings=settings,
                    object_store=object_store,
                )
            )
        )
    tools.extend(
        [
            *build_grounding_tools(
                repository=grounding_repository,
                default_limit=settings.agent_business_search_limit,
            ),
            *build_schema_tools(
                repository=repository,
                default_limit=settings.agent_schema_search_limit,
            ),
            *build_join_tools(
                settings=settings,
                repository=repository,
                query_engine=query_engine,
            ),
            *build_profile_tools(
                repository=repository,
                default_limit=settings.agent_profile_values_limit,
            ),
            *build_sql_tools(settings=settings, query_engine=query_engine),
        ]
    )
    return tools
