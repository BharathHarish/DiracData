"""LangGraph data analyst agent factory and runtime wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from diracdata.agents.artifacts import LearnedArtifactRepository
from diracdata.agents.checkpointers import checkpointer_from_settings
from diracdata.agents.middleware import (
    DataAnalystMiddlewareConfig,
    build_data_analyst_middleware,
)
from diracdata.agents.prompt_loader import load_system_prompt_v1
from diracdata.agents.settings import (
    AgentRuntimeSettings,
    AgentStreaming,
    LangGraphStreamMode,
    parse_stream_modes,
    stream_mode_values,
)
from diracdata.agents.stores import store_from_settings
from diracdata.config.settings import DiracDataSettings
from diracdata.llms import agent_chat_model_from_settings
from diracdata.query_engines.base import QueryEngine
from diracdata.retrieval import CandidateBindingSearchService
from diracdata.storage.object_store import ObjectStore
from diracdata.tools import build_data_analyst_tools


@dataclass
class DataAnalystAgentRuntime:
    """Thin runtime wrapper around a LangGraph create_agent graph."""

    graph: Any
    settings: DiracDataSettings
    runtime_settings: AgentRuntimeSettings
    artifact_repository: LearnedArtifactRepository

    def preflight(self) -> dict[str, bool]:
        return self.artifact_repository.preflight()

    def invoke(self, question: str, *, thread_id: str) -> Any:
        return self.graph.invoke(
            _agent_input(question),
            config=_thread_config(thread_id),
            version=self.runtime_settings.stream_version,
        )

    def stream(
        self,
        question: str,
        *,
        thread_id: str,
        stream_modes: str | Iterable[str] | None = None,
    ) -> Iterable[dict[str, Any]]:
        modes = (
            parse_stream_modes(list(stream_modes) if not isinstance(stream_modes, str) else stream_modes)
            if stream_modes is not None
            else self.runtime_settings.stream_modes
        )
        yield from self.graph.stream(
            _agent_input(question),
            config=_thread_config(thread_id),
            stream_mode=stream_mode_values(list(modes)),
            version=self.runtime_settings.stream_version,
        )

    def run(
        self,
        question: str,
        *,
        thread_id: str,
        stream_modes: str | Iterable[str] | None = None,
        streaming: bool | None = None,
    ) -> Any | Iterable[dict[str, Any]]:
        should_stream = (
            streaming
            if streaming is not None
            else self.runtime_settings.streaming == AgentStreaming.ON
        )
        if should_stream:
            return self.stream(question, thread_id=thread_id, stream_modes=stream_modes)
        return self.invoke(question, thread_id=thread_id)


def create_data_analyst_agent(
    *,
    settings: DiracDataSettings,
    object_store: ObjectStore,
    query_engine: QueryEngine,
    model: object | None = None,
    checkpointer: object | None = None,
    store: object | None = None,
    tools: list[object] | None = None,
    system_prompt: str | None = None,
    middleware: list[object] | None = None,
) -> DataAnalystAgentRuntime:
    """Create the main DiracData analyst agent with LangChain's create_agent API."""
    try:
        from langchain.agents import create_agent
    except ImportError as exc:
        raise RuntimeError("Data analyst agent requires langchain") from exc

    runtime_settings = AgentRuntimeSettings.from_settings(settings)
    resolved_model = model or agent_chat_model_from_settings(settings)
    candidate_search_service = (
        CandidateBindingSearchService(settings=settings, object_store=object_store)
        if settings.agent_candidate_search_enabled
        else None
    )
    resolved_tools = (
        build_data_analyst_tools(
            settings=settings,
            object_store=object_store,
            query_engine=query_engine,
            candidate_search_service=candidate_search_service,
        )
        if tools is None
        else tools
    )
    artifact_repository = LearnedArtifactRepository(
        settings=settings,
        object_store=object_store,
    )
    resolved_system_prompt = system_prompt or load_system_prompt_v1()
    resolved_middleware = (
        build_data_analyst_middleware(
            config=DataAnalystMiddlewareConfig(
                settings=settings,
                repository=artifact_repository,
                object_store=object_store,
                query_engine=query_engine,
                base_system_prompt=resolved_system_prompt,
                candidate_search_service=candidate_search_service,
            )
        )
        if middleware is None
        else middleware
    )
    graph = create_agent(
        model=resolved_model,
        tools=resolved_tools,
        system_prompt=resolved_system_prompt,
        middleware=resolved_middleware,
        checkpointer=checkpointer if checkpointer is not None else checkpointer_from_settings(settings),
        store=store if store is not None else store_from_settings(settings),
        name="diracdata_data_analyst_agent",
    )
    return DataAnalystAgentRuntime(
        graph=graph,
        settings=settings,
        runtime_settings=runtime_settings,
        artifact_repository=artifact_repository,
    )


def _agent_input(question: str) -> dict[str, list[dict[str, str]]]:
    return {"messages": [{"role": "user", "content": question}]}


def _thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}
