"""Long-context NL AST parser agent experiment."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

from diracdata_v2.llms import agent_chat_model_from_settings
from diracdata_v2.settings import V2Settings
from diracdata_v2.tools import (
    CandidateSearchService,
    SQLPatternSearchService,
    SchemaInfoService,
    build_candidate_search_tool,
    build_pattern_search_tool,
    build_schema_info_tools,
)


PROMPT_PACKAGE = "diracdata_v2.agent.prompts"
SYSTEM_PROMPT_FILE = "NL_AST_AGENT_SYSTEM_PROMPT.md"


@dataclass(frozen=True)
class NLASTAgentRuntime:
    graph: Any
    table_descriptions_path: Path
    table_column_descriptions_path: Path
    recursion_limit: int = 20

    def invoke(self, question: str) -> Any:
        return self.graph.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config={"recursion_limit": self.recursion_limit},
        )

    def stream(self, question: str, *, stream_mode: str | list[str] = "updates") -> Iterable[Any]:
        yield from self.graph.stream(
            {"messages": [{"role": "user", "content": question}]},
            stream_mode=stream_mode,
            config={"recursion_limit": self.recursion_limit},
        )


def create_nl_ast_agent(
    *,
    settings: V2Settings,
    table_descriptions_path: Path,
    table_column_descriptions_path: Path,
    model: object | None = None,
) -> NLASTAgentRuntime:
    """Create the long-context NL AST parser agent."""
    try:
        from langchain.agents import create_agent
    except ImportError as exc:
        raise RuntimeError("NL AST agent requires langchain") from exc

    schema_info_service = SchemaInfoService.from_file(settings.metadata_descriptions_path)
    pattern_search_service = SQLPatternSearchService.from_file(
        settings.sql_library_path,
        embedding_model=settings.embedding_model,
        local_files_only=settings.embedding_local_files_only,
    )
    candidate_search_service = CandidateSearchService.from_files(
        schema_ast_path=settings.schema_ast_path,
        metadata_descriptions_path=settings.metadata_descriptions_path,
        retrieval_documents_path=settings.retrieval_documents_path,
        column_embeddings_path=settings.column_embeddings_path,
        embedding_model=settings.embedding_model,
        local_files_only=settings.embedding_local_files_only,
    )

    resolved_model = model or agent_chat_model_from_settings(settings)
    tools = [
        build_pattern_search_tool(pattern_search_service),
        build_candidate_search_tool(candidate_search_service),
        *build_schema_info_tools(schema_info_service),
    ]
    graph = create_agent(
        model=resolved_model,
        tools=tools,
        system_prompt=load_nl_ast_agent_system_prompt(),
        name="diracdata_nl_ast_agent",
    )
    return NLASTAgentRuntime(
        graph=graph,
        table_descriptions_path=table_descriptions_path,
        table_column_descriptions_path=table_column_descriptions_path,
        recursion_limit=settings.agent_recursion_limit,
    )


def load_nl_ast_agent_system_prompt() -> str:
    return resources.files(PROMPT_PACKAGE).joinpath(SYSTEM_PROMPT_FILE).read_text(encoding="utf-8")
