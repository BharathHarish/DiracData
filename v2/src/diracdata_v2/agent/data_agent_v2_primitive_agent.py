"""Primitive data-agent harness with an analyst-led specialist loop."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any, Callable, Iterable

from diracdata_v2.llms import agent_chat_model_from_settings
from diracdata_v2.primitive import (
    GatedPrimitiveWorkflow,
    PrimitiveAgentRunner,
    PrimitiveRunResult,
    PrimitiveTraceEvent,
)
from diracdata_v2.primitive.runner import build_subagent_tool
from diracdata_v2.query import DuckDBEngine
from diracdata_v2.semantic_catalog import SemanticCatalogCompiler
from diracdata_v2.settings import V2Settings
from diracdata_v2.tools import (
    CandidateSearchService,
    SQLPatternSearchService,
    SchemaInfoService,
    build_candidate_search_tool,
    build_column_values_tool,
    build_execute_sql_tool,
    build_pattern_search_tool,
    build_schema_info_tools,
    build_sql_dry_run_tool,
)


PROMPT_PACKAGE = "diracdata_v2.agent.prompts"
OUTER_PROMPT_FILE = "PRIMITIVE_OUTER_AGENT_PROMPT.md"
INTENT_PROMPT_FILE = "PRIMITIVE_INTENT_PROMPT.md"
ANALYST_PROMPT_FILE = "PRIMITIVE_ANALYST_PROMPT.md"
SQL_AUTHOR_PROMPT_FILE = "PRIMITIVE_SQL_AUTHOR_PROMPT.md"
DATA_STEWARD_PROMPT_FILE = "PRIMITIVE_DATA_STEWARD_PROMPT.md"
DATA_ENGINEERING_PROMPT_FILE = "PRIMITIVE_DATA_ENGINEERING_PROMPT.md"


@dataclass(frozen=True)
class PrimitiveDataAgentRuntime:
    outer_agent: PrimitiveAgentRunner
    subagents: dict[str, PrimitiveAgentRunner]
    gated_workflow: GatedPrimitiveWorkflow

    def invoke(
        self,
        question: str,
        *,
        clarification: str | None = None,
        previous_context: str | None = None,
        event_sink: Callable[[PrimitiveTraceEvent], None] | None = None,
    ) -> PrimitiveRunResult:
        return self.gated_workflow.run(
            question,
            clarification=clarification,
            previous_context=previous_context,
            event_sink=event_sink,
        )

    def invoke_outer(self, question: str) -> PrimitiveRunResult:
        return self.outer_agent.run(question)

    def stream(self, question: str) -> Iterable[PrimitiveTraceEvent]:
        yield from self.outer_agent.stream(question)


def create_primitive_data_agent(
    *,
    settings: V2Settings,
    model: Any | None = None,
    engine: DuckDBEngine | None = None,
) -> PrimitiveDataAgentRuntime:
    """Create the primitive outer ReAct agent and specialist subagents."""
    resolved_model = model or agent_chat_model_from_settings(settings)
    schema_info = SchemaInfoService.from_file(settings.metadata_descriptions_path)
    pattern_search = SQLPatternSearchService.from_file(settings.sql_library_path)
    candidate_search = CandidateSearchService.from_files(
        schema_ast_path=settings.schema_ast_path,
        metadata_descriptions_path=settings.metadata_descriptions_path,
        retrieval_documents_path=settings.retrieval_documents_path,
        column_embeddings_path=settings.column_embeddings_path,
        embedding_model=settings.embedding_model,
        local_files_only=settings.embedding_local_files_only,
    )
    schema_tools = build_schema_info_tools(schema_info)
    retrieval_tools = [
        build_pattern_search_tool(pattern_search),
        build_candidate_search_tool(candidate_search),
        *schema_tools,
    ]
    resolved_engine = engine or DuckDBEngine(data_root=settings.data_root, schema_name=settings.schema)
    sql_tools = [
        *retrieval_tools,
        build_column_values_tool(settings=settings, engine=resolved_engine),
        build_sql_dry_run_tool(engine=resolved_engine),
    ]
    execute_sql_tool = build_execute_sql_tool(settings=settings, engine=resolved_engine)
    context_compiler = None
    if settings.semantic_catalog_path is not None:
        semantic_compiler = SemanticCatalogCompiler.from_file(settings.semantic_catalog_path)

        def context_compiler(question: str) -> Any:
            return semantic_compiler.compile(question)

    intent_agent = PrimitiveAgentRunner(
        name="intent_subagent",
        model=resolved_model,
        tools=retrieval_tools,
        system_prompt=load_primitive_intent_prompt(),
        max_iterations=settings.primitive_subagent_max_iterations,
        max_tool_result_chars=settings.primitive_max_tool_result_chars,
    )
    sql_author_agent = PrimitiveAgentRunner(
        name="sql_author_subagent",
        model=resolved_model,
        tools=sql_tools,
        system_prompt=load_primitive_sql_author_prompt(),
        max_iterations=settings.primitive_subagent_max_iterations,
        max_tool_result_chars=settings.primitive_max_tool_result_chars,
    )

    analyst_agent = PrimitiveAgentRunner(
        name="analyst_subagent",
        model=resolved_model,
        tools=sql_tools,
        system_prompt=load_primitive_analyst_prompt(),
        max_iterations=settings.primitive_subagent_max_iterations,
        max_tool_result_chars=settings.primitive_max_tool_result_chars,
    )
    data_steward_agent = PrimitiveAgentRunner(
        name="data_steward_subagent",
        model=resolved_model,
        tools=sql_tools,
        system_prompt=load_primitive_data_steward_prompt(),
        max_iterations=settings.primitive_subagent_max_iterations,
        max_tool_result_chars=settings.primitive_max_tool_result_chars,
    )
    data_engineer_agent = PrimitiveAgentRunner(
        name="data_engineer_subagent",
        model=resolved_model,
        tools=sql_tools,
        system_prompt=load_primitive_data_engineering_prompt(),
        max_iterations=settings.primitive_subagent_max_iterations,
        max_tool_result_chars=settings.primitive_max_tool_result_chars,
    )
    outer_tools = [
        build_subagent_tool(
            name="analyst_subagent",
            description=(
                "Interpret the analytics question, retrieve schema or pattern context, probe data, "
                "author read-only SQL, run EXPLAIN, execute SQL, and return an analyst work packet."
            ),
            runner=analyst_agent,
        ),
        build_subagent_tool(
            name="data_steward_subagent",
            description=(
                "Review an analyst work packet as a data quality and semantic correctness gate. "
                "Check NULL behavior, value grounding, grain, joins, execution evidence, and assumptions."
            ),
            runner=data_steward_agent,
        ),
        build_subagent_tool(
            name="data_engineer_subagent",
            description=(
                "Optimize a validated SQL candidate for cost, CTE shape, predicate pushdown, "
                "and join efficiency without changing business semantics."
            ),
            runner=data_engineer_agent,
        ),
    ]
    outer_agent = PrimitiveAgentRunner(
        name="primitive_outer_agent",
        model=resolved_model,
        tools=outer_tools,
        system_prompt=load_primitive_outer_prompt(),
        max_iterations=settings.primitive_max_iterations,
        max_tool_result_chars=settings.primitive_max_tool_result_chars,
    )
    gated_workflow = GatedPrimitiveWorkflow(
        analyst=analyst_agent,
        steward=data_steward_agent,
        data_engineer=data_engineer_agent,
        intent=intent_agent,
        sql_author=sql_author_agent,
        final_execute_tool=execute_sql_tool,
        context_compiler=context_compiler,
        max_correction_rounds=1,
        enable_data_engineering=True,
    )
    return PrimitiveDataAgentRuntime(
        outer_agent=outer_agent,
        gated_workflow=gated_workflow,
        subagents={
            "intent_subagent": intent_agent,
            "sql_author_subagent": sql_author_agent,
            "analyst_subagent": analyst_agent,
            "data_steward_subagent": data_steward_agent,
            "data_engineer_subagent": data_engineer_agent,
        },
    )


def load_primitive_outer_prompt() -> str:
    return resources.files(PROMPT_PACKAGE).joinpath(OUTER_PROMPT_FILE).read_text(encoding="utf-8")


def load_primitive_intent_prompt() -> str:
    return resources.files(PROMPT_PACKAGE).joinpath(INTENT_PROMPT_FILE).read_text(encoding="utf-8")


def load_primitive_analyst_prompt() -> str:
    return resources.files(PROMPT_PACKAGE).joinpath(ANALYST_PROMPT_FILE).read_text(encoding="utf-8")


def load_primitive_sql_author_prompt() -> str:
    return resources.files(PROMPT_PACKAGE).joinpath(SQL_AUTHOR_PROMPT_FILE).read_text(encoding="utf-8")


def load_primitive_data_steward_prompt() -> str:
    return resources.files(PROMPT_PACKAGE).joinpath(DATA_STEWARD_PROMPT_FILE).read_text(
        encoding="utf-8"
    )


def load_primitive_data_engineering_prompt() -> str:
    return resources.files(PROMPT_PACKAGE).joinpath(DATA_ENGINEERING_PROMPT_FILE).read_text(
        encoding="utf-8"
    )


def load_primitive_sql_validator_prompt() -> str:
    """Backward-compatible alias for older tests and scripts."""
    return load_primitive_data_steward_prompt()
