"""Primitive data-agent harness with an analyst-led specialist loop."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any, Callable, Iterable

from diracdata_v2.llms import ChatModelFactory, agent_chat_model_from_settings
from diracdata_v2.primitive import (
    GatedPrimitiveWorkflow,
    PrimitiveAgentRunner,
    PrimitiveRunResult,
    PrimitiveTraceEvent,
    SupervisorPrimitiveWorkflow,
    TypedPrimitiveWorkflow,
    TypedWorkflowConfig,
)
from diracdata_v2.primitive.runner import build_subagent_tool
from diracdata_v2.query import DuckDBEngine
from diracdata_v2.semantic_catalog import LLMIntentFrameExtractor, SemanticCatalogCompiler
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
SUPERVISOR_PROMPT_FILE = "PRIMITIVE_SUPERVISOR_PROMPT.md"
INTENT_PROMPT_FILE = "PRIMITIVE_INTENT_PROMPT.md"
ANALYST_PROMPT_FILE = "PRIMITIVE_ANALYST_PROMPT.md"
SQL_AUTHOR_PROMPT_FILE = "PRIMITIVE_SQL_AUTHOR_PROMPT.md"
DATA_STEWARD_PROMPT_FILE = "PRIMITIVE_DATA_STEWARD_PROMPT.md"
DATA_ENGINEERING_PROMPT_FILE = "PRIMITIVE_DATA_ENGINEERING_PROMPT.md"


@dataclass(frozen=True)
class PrimitiveDataAgentRuntime:
    outer_agent: PrimitiveAgentRunner
    supervisor_agent: PrimitiveAgentRunner
    subagents: dict[str, PrimitiveAgentRunner]
    gated_workflow: GatedPrimitiveWorkflow
    supervisor_workflow: SupervisorPrimitiveWorkflow
    typed_workflow: TypedPrimitiveWorkflow
    default_workflow: str = "gated"

    def invoke(
        self,
        question: str,
        *,
        clarification: str | None = None,
        previous_context: str | None = None,
        event_sink: Callable[[PrimitiveTraceEvent], None] | None = None,
        workflow: str | None = None,
    ) -> PrimitiveRunResult:
        mode = (workflow or self.default_workflow).strip().lower()
        if mode == "supervisor":
            return self.supervisor_workflow.run(
                question,
                clarification=clarification,
                previous_context=previous_context,
                event_sink=event_sink,
            )
        if mode == "typed":
            return self.typed_workflow.run(
                question,
                clarification=clarification,
                previous_context=previous_context,
                event_sink=event_sink,
            )
        return self.gated_workflow.run(
            question,
            clarification=clarification,
            previous_context=previous_context,
            event_sink=event_sink,
        )

    def invoke_supervisor(
        self,
        question: str,
        *,
        clarification: str | None = None,
        previous_context: str | None = None,
        event_sink: Callable[[PrimitiveTraceEvent], None] | None = None,
    ) -> PrimitiveRunResult:
        return self.supervisor_workflow.run(
            question,
            clarification=clarification,
            previous_context=previous_context,
            event_sink=event_sink,
        )

    def invoke_typed(
        self,
        question: str,
        *,
        clarification: str | None = None,
        previous_context: str | None = None,
        event_sink: Callable[[PrimitiveTraceEvent], None] | None = None,
    ) -> PrimitiveRunResult:
        return self.typed_workflow.run(
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
    schema_tools = build_schema_info_tools(schema_info)
    retrieval_tools = [
        build_pattern_search_tool(pattern_search),
        build_candidate_search_tool(candidate_search),
        *schema_tools,
    ]
    resolved_engine = engine or DuckDBEngine(data_root=settings.data_root, schema_name=settings.schema)
    sql_dry_run_tool = build_sql_dry_run_tool(engine=resolved_engine)
    sql_tools = [
        *retrieval_tools,
        build_column_values_tool(settings=settings, engine=resolved_engine),
        sql_dry_run_tool,
    ]
    execute_sql_tool = build_execute_sql_tool(settings=settings, engine=resolved_engine)
    context_compiler = None
    if settings.semantic_catalog_path is not None:
        intent_extractor = None
        if settings.context_compiler_mode.strip().lower() == "agentic":
            context_model = ChatModelFactory(settings=settings).create_chat_model(
                profile_id=settings.context_compiler_model_profile,
            )
            intent_extractor = LLMIntentFrameExtractor(model=context_model)
        semantic_compiler = SemanticCatalogCompiler.from_file(
            settings.semantic_catalog_path,
            intent_extractor=intent_extractor,
        )

        def context_compiler(question: str) -> Any:
            return semantic_compiler.compile(
                question,
                max_cards=settings.context_compiler_max_cards,
                max_patterns=settings.context_compiler_max_patterns,
            )

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
    supervisor_tools = [
        build_subagent_tool(
            name="intent_subagent",
            description=(
                "Create or repair the semantic intent packet. Pass the original user question verbatim; "
                "do not add inferred filters, exclusions, joins, table scopes, or guessed meanings. "
                "Use this before SQL authoring and again when Steward finds that the intent changed, "
                "lost dimensions, or misunderstood the user."
            ),
            runner=intent_agent,
        ),
        build_subagent_tool(
            name="sql_author_subagent",
            description=(
                "Write or repair safe read-only SQL from an approved intent packet. Use dry-run only; "
                "do not execute final SQL."
            ),
            runner=sql_author_agent,
        ),
        build_subagent_tool(
            name="data_steward_subagent",
            description=(
                "Validate the exact SQL Author packet as a semantic unit test. Use this after every "
                "SQL draft and after any Data Engineering rewrite."
            ),
            runner=data_steward_agent,
        ),
        build_subagent_tool(
            name="data_engineer_subagent",
            description=(
                "Optimize a Steward-approved SQL candidate for cost and execution shape without "
                "changing semantics. Use for complex joins, many CTEs, anti/semi joins, fanout risk, "
                "or repeated scans."
            ),
            runner=data_engineer_agent,
        ),
        execute_sql_tool,
    ]
    supervisor_agent = PrimitiveAgentRunner(
        name="primitive_supervisor_agent",
        model=resolved_model,
        tools=supervisor_tools,
        system_prompt=load_primitive_supervisor_prompt(),
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
    supervisor_workflow = SupervisorPrimitiveWorkflow(
        supervisor=supervisor_agent,
        context_compiler=context_compiler,
    )
    typed_workflow = TypedPrimitiveWorkflow(
        intent=intent_agent,
        sql_author=sql_author_agent,
        steward=data_steward_agent,
        data_engineer=data_engineer_agent,
        sql_dry_run_tool=sql_dry_run_tool,
        final_execute_tool=execute_sql_tool,
        context_compiler=context_compiler,
        config=TypedWorkflowConfig(max_sql_repairs=1, enable_data_engineering=True),
    )
    return PrimitiveDataAgentRuntime(
        outer_agent=outer_agent,
        supervisor_agent=supervisor_agent,
        gated_workflow=gated_workflow,
        supervisor_workflow=supervisor_workflow,
        typed_workflow=typed_workflow,
        default_workflow=settings.primitive_workflow_mode,
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


def load_primitive_supervisor_prompt() -> str:
    return resources.files(PROMPT_PACKAGE).joinpath(SUPERVISOR_PROMPT_FILE).read_text(encoding="utf-8")


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
