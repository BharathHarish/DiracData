"""Single-agent v2 harness: one ReAct agent and a small tool surface."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any, Iterable

from diracdata_v2.agent.middleware import NLASTMiddleware, SQLAuthoringMiddleware, SQLValidationMiddleware
from diracdata_v2.llms import agent_chat_model_from_settings
from diracdata_v2.query import DuckDBEngine
from diracdata_v2.settings import V2Settings
from diracdata_v2.tools import (
    ASTSearchService,
    CandidateSearchService,
    SQLPatternSearchService,
    SchemaInfoService,
    build_candidate_search_tool,
    build_column_values_tool,
    build_execute_sql_tool,
    build_pattern_search_tool,
    build_schema_search_ast_tool,
    build_schema_info_tools,
)


PROMPT_PACKAGE = "diracdata_v2.agent.prompts"
SYSTEM_PROMPT_FILE = "SYSTEM_PROMPT_V1.md"
TODO_PLANNING_PROMPT_FILE = "TODO_SQL_PLANNING_PROMPT.md"
TODO_PLANNING_TOOL_DESCRIPTION_FILE = "TODO_SQL_PLANNING_TOOL_DESCRIPTION.md"
NL_AST_MIDDLEWARE_PROMPT_FILE = "NL_AST_MIDDLEWARE_PROMPT.md"
SQL_AUTHORING_MIDDLEWARE_PROMPT_FILE = "SQL_AUTHORING_MIDDLEWARE_PROMPT.md"
SQL_VALIDATION_MIDDLEWARE_PROMPT_FILE = "SQL_VALIDATION_MIDDLEWARE_PROMPT.md"


@dataclass
class V2AgentRuntime:
    graph: Any
    settings: V2Settings

    def invoke(self, question: str) -> Any:
        return self.graph.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config={"recursion_limit": self.settings.agent_recursion_limit},
        )

    def stream(self, question: str, *, stream_mode: str | list[str] = "updates") -> Iterable[Any]:
        yield from self.graph.stream(
            {"messages": [{"role": "user", "content": question}]},
            stream_mode=stream_mode,
            config={"recursion_limit": self.settings.agent_recursion_limit},
        )


def create_v2_agent(
    *,
    settings: V2Settings,
    model: object | None = None,
    schema_search_service: ASTSearchService | None = None,
    engine: DuckDBEngine | None = None,
) -> V2AgentRuntime:
    try:
        from langchain.agents import create_agent
        from langchain.agents.middleware import TodoListMiddleware
    except ImportError as exc:
        raise RuntimeError("v2 agent requires langchain") from exc

    resolved_model = model or agent_chat_model_from_settings(settings)
    resolved_search = schema_search_service or ASTSearchService.from_files(
        schema_ast_path=settings.schema_ast_path,
        sql_library_path=settings.sql_library_path,
    )
    pattern_search = SQLPatternSearchService.from_file(
        settings.sql_library_path,
        embedding_model=settings.embedding_model,
        local_files_only=settings.embedding_local_files_only,
    )
    candidate_search = CandidateSearchService.from_files(
        schema_ast_path=settings.schema_ast_path,
        metadata_descriptions_path=settings.metadata_descriptions_path,
        retrieval_documents_path=settings.retrieval_documents_path,
        column_embeddings_path=settings.column_embeddings_path,
        embedding_model=settings.embedding_model,
        local_files_only=settings.embedding_local_files_only,
    )
    schema_info = SchemaInfoService.from_file(settings.metadata_descriptions_path)
    resolved_engine = engine or DuckDBEngine(data_root=settings.data_root, schema_name=settings.schema)
    tools = [
        build_schema_search_ast_tool(resolved_search),
        build_pattern_search_tool(pattern_search),
        build_candidate_search_tool(candidate_search),
        *build_schema_info_tools(schema_info),
        build_column_values_tool(settings=settings, engine=resolved_engine),
        build_execute_sql_tool(settings=settings, engine=resolved_engine),
    ]
    middleware = [
        NLASTMiddleware(prompt=load_nl_ast_middleware_prompt()),
        SQLAuthoringMiddleware(prompt=load_sql_authoring_middleware_prompt()),
        SQLValidationMiddleware(prompt=load_sql_validation_middleware_prompt()),
    ]
    if settings.agent_todo_planning_enabled:
        middleware.insert(
            0,
            TodoListMiddleware(
                system_prompt=load_todo_planning_prompt(),
                tool_description=load_todo_planning_tool_description(),
            ),
        )
    graph = create_agent(
        model=resolved_model,
        tools=tools,
        system_prompt=load_system_prompt(),
        middleware=middleware,
        name="diracdata_v2_lean_agent",
    )
    return V2AgentRuntime(graph=graph, settings=settings)


def load_system_prompt() -> str:
    return resources.files(PROMPT_PACKAGE).joinpath(SYSTEM_PROMPT_FILE).read_text(encoding="utf-8")


def load_todo_planning_prompt() -> str:
    return resources.files(PROMPT_PACKAGE).joinpath(TODO_PLANNING_PROMPT_FILE).read_text(
        encoding="utf-8"
    )


def load_todo_planning_tool_description() -> str:
    return resources.files(PROMPT_PACKAGE).joinpath(TODO_PLANNING_TOOL_DESCRIPTION_FILE).read_text(
        encoding="utf-8"
    )


def load_nl_ast_middleware_prompt() -> str:
    return resources.files(PROMPT_PACKAGE).joinpath(NL_AST_MIDDLEWARE_PROMPT_FILE).read_text(
        encoding="utf-8"
    )


def load_sql_authoring_middleware_prompt() -> str:
    return resources.files(PROMPT_PACKAGE).joinpath(SQL_AUTHORING_MIDDLEWARE_PROMPT_FILE).read_text(
        encoding="utf-8"
    )


def load_sql_validation_middleware_prompt() -> str:
    return resources.files(PROMPT_PACKAGE).joinpath(SQL_VALIDATION_MIDDLEWARE_PROMPT_FILE).read_text(
        encoding="utf-8"
    )
