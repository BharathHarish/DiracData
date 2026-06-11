"""Stage-gated analyst compiler runtime for trustworthy NL2SQL."""

from __future__ import annotations

import json
import operator
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Iterable, Protocol

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from diracdata.agents.artifacts import LearnedArtifactRepository
from diracdata.agents.checkpointers import checkpointer_from_settings
from diracdata.agents.prompt_loader import (
    load_intent_frame_prompt_v1,
    load_sql_plan_prompt_v1,
    load_truth_compiler_prompt_v1,
)
from diracdata.agents.settings import (
    AgentRuntimeSettings,
    AgentStreaming,
    parse_stream_modes,
    stream_mode_values,
)
from diracdata.config.settings import DiracDataSettings
from diracdata.grounding.business import BusinessGroundingRepository
from diracdata.learning.models import to_jsonable
from diracdata.llms import agent_chat_model_from_settings
from diracdata.query_engines.base import QueryEngine
from diracdata.storage.object_store import ObjectStore
from diracdata.tools.sql_tools import validate_read_only_sql


class CompilerRoute(StrEnum):
    """Answer-time route selected after intent and context grounding."""

    CLARIFY = "clarify"
    KNOWN_METRIC = "known_metric"
    NOVEL_QUERY = "novel_query"


class VerificationStatus(StrEnum):
    """Final answer verification state."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class TimeRangeIntent(BaseModel):
    """Business time interpretation extracted from the question."""

    text: str | None = Field(default=None, description="User-facing time phrase.")
    start: str | None = Field(default=None, description="Inclusive ISO date/timestamp if explicit.")
    end: str | None = Field(default=None, description="Exclusive ISO date/timestamp if explicit.")
    grain: str | None = Field(default=None, description="Requested time grain, such as day/month.")
    needs_clarification: bool = False


class FilterIntent(BaseModel):
    """A business filter requested by the user."""

    term: str
    value: str | None = None
    operator: str = "="
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class IntentFrame(BaseModel):
    """Typed first-pass interpretation of a business data question."""

    normalized_task: str
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[FilterIntent] = Field(default_factory=list)
    time_range: TimeRangeIntent = Field(default_factory=TimeRangeIntent)
    business_entities: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    analyst_notes: list[str] = Field(default_factory=list)


class SelectedColumn(BaseModel):
    """Column selected for SQL planning."""

    table: str
    column: str
    purpose: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class PlannedJoin(BaseModel):
    """A join clause selected by the SQL planner."""

    left_table: str
    left_column: str
    right_table: str
    right_column: str
    join_type: str = "inner"
    reason: str


class SQLPlan(BaseModel):
    """Concrete SQL plan with probes and final SQL."""

    route: str
    base_table: str
    base_grain: str
    selected_columns: list[SelectedColumn] = Field(default_factory=list)
    joins: list[PlannedJoin] = Field(default_factory=list)
    probe_sql: list[str] = Field(default_factory=list)
    final_sql: str
    assumptions: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class TruthReport(BaseModel):
    """Final answer and verification report."""

    answer: str
    verification_status: VerificationStatus
    checks_performed: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class StructuredModelRunner(Protocol):
    """Small protocol for typed LLM stage calls."""

    def invoke(
        self,
        *,
        schema: type[BaseModel],
        system_prompt: str,
        payload: dict[str, Any],
    ) -> BaseModel: ...


class LangChainStructuredModelRunner:
    """LangChain-backed structured model runner."""

    def __init__(self, model: object) -> None:
        self.model = model

    def invoke(
        self,
        *,
        schema: type[BaseModel],
        system_prompt: str,
        payload: dict[str, Any],
    ) -> BaseModel:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, indent=2, sort_keys=True)},
        ]
        try:
            structured = self.model.with_structured_output(schema)  # type: ignore[attr-defined]
            response = structured.invoke(messages)
            return _coerce_structured_response(schema, response)
        except Exception:
            response = self.model.invoke(
                [
                    {
                        "role": "system",
                        "content": (
                            f"{system_prompt}\n\nReturn only valid JSON matching this schema: "
                            f"{schema.model_json_schema()}"
                        ),
                    },
                    messages[1],
                ]
            )
            return _coerce_structured_response(schema, response)


class AnalystCompilerState(TypedDict, total=False):
    """LangGraph state for the analyst compiler."""

    question: str
    intent_frame: dict[str, Any]
    context: dict[str, Any]
    route: str
    sql_plan: dict[str, Any]
    sql_validations: list[dict[str, Any]]
    probe_results: list[dict[str, Any]]
    sql_result: dict[str, Any]
    truth_report: dict[str, Any]
    final_answer: str
    repair_attempts: int
    trace: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[dict[str, Any]], operator.add]
    turns: Annotated[list[dict[str, Any]], operator.add]


@dataclass
class AnalystCompilerRuntime:
    """Runtime wrapper around the controlled analyst StateGraph."""

    graph: Any
    settings: DiracDataSettings
    runtime_settings: AgentRuntimeSettings
    artifact_repository: LearnedArtifactRepository

    def preflight(self) -> dict[str, bool]:
        return self.artifact_repository.preflight()

    def invoke(self, question: str, *, thread_id: str) -> dict[str, Any]:
        result = self.graph.invoke(
            _compiler_input(question),
            config=_thread_config(thread_id),
            version=self.runtime_settings.stream_version,
        )
        result = getattr(result, "value", result)
        return result if isinstance(result, dict) else {"result": result}

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
            _compiler_input(question),
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
    ) -> dict[str, Any] | Iterable[dict[str, Any]]:
        should_stream = (
            streaming
            if streaming is not None
            else self.runtime_settings.streaming == AgentStreaming.ON
        )
        if should_stream:
            return self.stream(question, thread_id=thread_id, stream_modes=stream_modes)
        return self.invoke(question, thread_id=thread_id)


def create_analyst_compiler(
    *,
    settings: DiracDataSettings,
    object_store: ObjectStore,
    query_engine: QueryEngine,
    model: object | None = None,
    model_runner: StructuredModelRunner | None = None,
    checkpointer: object | None = None,
) -> AnalystCompilerRuntime:
    """Create a controlled LangGraph analyst compiler for NL2SQL."""
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError("Analyst compiler requires langgraph") from exc

    runtime_settings = AgentRuntimeSettings.from_settings(settings)
    artifact_repository = LearnedArtifactRepository(
        settings=settings,
        object_store=object_store,
    )
    grounding_repository = BusinessGroundingRepository(
        settings=settings,
        object_store=object_store,
    )
    runner = model_runner or LangChainStructuredModelRunner(
        model or agent_chat_model_from_settings(settings)
    )

    def build_intent_frame(state: AnalystCompilerState) -> dict[str, Any]:
        payload = {
            "question": state["question"],
            "current_date": _current_date(),
            "recent_turns": _recent_turns(state),
        }
        intent = runner.invoke(
            schema=IntentFrame,
            system_prompt=load_intent_frame_prompt_v1(),
            payload=payload,
        )
        intent_dict = _model_dump(intent)
        return {
            "intent_frame": intent_dict,
            "trace": [_trace("build_intent_frame", intent_dict)],
        }

    def retrieve_context(state: AnalystCompilerState) -> dict[str, Any]:
        intent = state.get("intent_frame", {})
        query_text = _context_query(state["question"], intent)
        business_matches = _safe_business_search(
            grounding_repository,
            query_text,
            limit=settings.agent_business_search_limit,
        )
        schema_matches = _safe_schema_search(
            artifact_repository,
            query_text,
            limit=settings.agent_schema_search_limit,
        )
        join_pairs = _safe_join_pairs(artifact_repository)
        table_descriptions = _safe_table_descriptions(artifact_repository)
        available_schema = _available_schema(
            repository=artifact_repository,
            query_engine=query_engine,
        )
        profile_hints = _profile_hints(
            artifact_repository,
            schema_matches=schema_matches,
            limit=settings.agent_profile_values_limit,
        )
        context = {
            "business_matches": business_matches,
            "schema_matches": schema_matches,
            "joinable_pairs": join_pairs,
            "table_descriptions": table_descriptions,
            "available_schema": available_schema,
            "profile_hints": profile_hints,
        }
        return {
            "context": context,
            "trace": [
                _trace(
                    "retrieve_context",
                    {
                        "business_matches": len(business_matches),
                        "schema_matches": len(schema_matches),
                        "joinable_pairs": len(join_pairs),
                        "available_tables": len(available_schema),
                        "profile_hints": len(profile_hints),
                    },
                )
            ],
        }

    def decide_route(state: AnalystCompilerState) -> dict[str, Any]:
        intent = state.get("intent_frame", {})
        context = state.get("context", {})
        if intent.get("needs_clarification") and intent.get("clarification_questions"):
            route = CompilerRoute.CLARIFY.value
        elif _has_metric_context(intent, context):
            route = CompilerRoute.KNOWN_METRIC.value
        else:
            route = CompilerRoute.NOVEL_QUERY.value
        return {
            "route": route,
            "trace": [_trace("decide_route", {"route": route})],
        }

    def clarify(state: AnalystCompilerState) -> dict[str, Any]:
        intent = state.get("intent_frame", {})
        questions = intent.get("clarification_questions") or [
            "Could you clarify the metric, time period, or filters you want me to use?"
        ]
        answer = "I need one clarification before writing SQL:\n" + "\n".join(
            f"- {question}" for question in questions[:3]
        )
        return {
            "final_answer": answer,
            "truth_report": {
                "answer": answer,
                "verification_status": VerificationStatus.WARNING.value,
                "checks_performed": ["intent clarification"],
                "caveats": ["SQL was not generated because the intent is ambiguous."],
                "confidence": 0.8,
            },
            "turns": [_turn(state, final_answer=answer)],
            "trace": [_trace("clarify", {"question_count": len(questions)})],
        }

    def plan_sql(state: AnalystCompilerState) -> dict[str, Any]:
        payload = _planner_payload(state, settings=settings)
        plan = runner.invoke(
            schema=SQLPlan,
            system_prompt=load_sql_plan_prompt_v1(),
            payload=payload,
        )
        plan_dict = _model_dump(plan)
        return {
            "sql_plan": plan_dict,
            "repair_attempts": state.get("repair_attempts", 0),
            "trace": [
                _trace(
                    "plan_sql",
                    {
                        "base_table": plan_dict.get("base_table"),
                        "probe_count": len(plan_dict.get("probe_sql", [])),
                        "confidence": plan_dict.get("confidence"),
                    },
                )
            ],
        }

    def validate_sql_plan(state: AnalystCompilerState) -> dict[str, Any]:
        plan = state.get("sql_plan", {})
        validations = []
        for sql in _probe_sql(plan, settings=settings):
            validations.append(_validate_sql(sql, query_engine=query_engine, settings=settings))
        final_sql = str(plan.get("final_sql", ""))
        validations.append(_validate_sql(final_sql, query_engine=query_engine, settings=settings))
        ok = all(item.get("status") == "ok" for item in validations)
        return {
            "sql_validations": validations,
            "trace": [_trace("validate_sql_plan", {"status": "ok" if ok else "error"})],
        }

    def execute_probes(state: AnalystCompilerState) -> dict[str, Any]:
        plan = state.get("sql_plan", {})
        results = []
        for sql in _probe_sql(plan, settings=settings):
            results.append(
                _execute_sql(
                    sql,
                    query_engine=query_engine,
                    max_rows=settings.agent_compiler_probe_max_rows,
                )
            )
        return {
            "probe_results": results,
            "trace": [_trace("execute_probes", {"probe_count": len(results)})],
        }

    def execute_sql(state: AnalystCompilerState) -> dict[str, Any]:
        plan = state.get("sql_plan", {})
        result = _execute_sql(
            str(plan.get("final_sql", "")),
            query_engine=query_engine,
            max_rows=settings.agent_sql_max_rows,
        )
        return {
            "sql_result": result,
            "trace": [_trace("execute_sql", {"status": result.get("status")})],
        }

    def repair_sql(state: AnalystCompilerState) -> dict[str, Any]:
        attempts = state.get("repair_attempts", 0) + 1
        payload = _planner_payload(state, settings=settings)
        payload["repair_context"] = {
            "previous_sql_plan": state.get("sql_plan"),
            "sql_validations": state.get("sql_validations"),
            "probe_results": state.get("probe_results"),
            "sql_result": state.get("sql_result"),
            "attempt": attempts,
        }
        plan = runner.invoke(
            schema=SQLPlan,
            system_prompt=load_sql_plan_prompt_v1(),
            payload=payload,
        )
        return {
            "sql_plan": _model_dump(plan),
            "repair_attempts": attempts,
            "trace": [_trace("repair_sql", {"attempt": attempts})],
        }

    def truth_compile(state: AnalystCompilerState) -> dict[str, Any]:
        payload = {
            "question": state.get("question"),
            "current_date": _current_date(),
            "intent_frame": state.get("intent_frame"),
            "context": _compact_truth_context(state.get("context", {})),
            "sql_plan": state.get("sql_plan"),
            "probe_results": state.get("probe_results"),
            "sql_result": state.get("sql_result"),
            "result_facts": _result_facts(state.get("sql_result", {})),
        }
        report = runner.invoke(
            schema=TruthReport,
            system_prompt=load_truth_compiler_prompt_v1(),
            payload=payload,
        )
        report_dict = _model_dump(report)
        answer = _render_verified_answer(
            question=str(state.get("question", "")),
            sql_result=state.get("sql_result", {}),
            truth_report=report_dict,
        )
        return {
            "truth_report": report_dict,
            "final_answer": answer,
            "turns": [_turn(state, final_answer=answer)],
            "trace": [
                _trace(
                    "truth_compile",
                    {
                        "verification_status": report_dict.get("verification_status"),
                        "confidence": report_dict.get("confidence"),
                    },
                )
            ],
        }

    graph_builder = StateGraph(AnalystCompilerState)
    graph_builder.add_node("build_intent_frame", build_intent_frame)
    graph_builder.add_node("retrieve_context", retrieve_context)
    graph_builder.add_node("decide_route", decide_route)
    graph_builder.add_node("clarify", clarify)
    graph_builder.add_node("plan_sql", plan_sql)
    graph_builder.add_node("validate_sql_plan", validate_sql_plan)
    graph_builder.add_node("execute_probes", execute_probes)
    graph_builder.add_node("execute_sql", execute_sql)
    graph_builder.add_node("repair_sql", repair_sql)
    graph_builder.add_node("truth_compile", truth_compile)
    graph_builder.add_edge(START, "build_intent_frame")
    graph_builder.add_edge("build_intent_frame", "retrieve_context")
    graph_builder.add_edge("retrieve_context", "decide_route")
    graph_builder.add_conditional_edges(
        "decide_route",
        _route_after_decision,
        {
            "clarify": "clarify",
            "plan_sql": "plan_sql",
        },
    )
    graph_builder.add_edge("clarify", END)
    graph_builder.add_edge("plan_sql", "validate_sql_plan")
    graph_builder.add_conditional_edges(
        "validate_sql_plan",
        lambda state: _route_after_validation(state, settings=settings),
        {
            "execute_probes": "execute_probes",
            "repair_sql": "repair_sql",
            "truth_compile": "truth_compile",
        },
    )
    graph_builder.add_edge("execute_probes", "execute_sql")
    graph_builder.add_conditional_edges(
        "execute_sql",
        lambda state: _route_after_execution(state, settings=settings),
        {
            "truth_compile": "truth_compile",
            "repair_sql": "repair_sql",
        },
    )
    graph_builder.add_edge("repair_sql", "validate_sql_plan")
    graph_builder.add_edge("truth_compile", END)
    graph = graph_builder.compile(
        checkpointer=checkpointer if checkpointer is not None else checkpointer_from_settings(settings)
    )
    return AnalystCompilerRuntime(
        graph=graph,
        settings=settings,
        runtime_settings=runtime_settings,
        artifact_repository=artifact_repository,
    )


def _route_after_decision(state: AnalystCompilerState) -> str:
    if state.get("route") == CompilerRoute.CLARIFY.value:
        return "clarify"
    return "plan_sql"


def _route_after_validation(
    state: AnalystCompilerState,
    *,
    settings: DiracDataSettings,
) -> str:
    validations = state.get("sql_validations", [])
    if all(item.get("status") == "ok" for item in validations):
        return "execute_probes"
    if state.get("repair_attempts", 0) < settings.agent_compiler_max_repairs:
        return "repair_sql"
    return "truth_compile"


def _route_after_execution(
    state: AnalystCompilerState,
    *,
    settings: DiracDataSettings,
) -> str:
    if state.get("sql_result", {}).get("status") == "ok":
        return "truth_compile"
    if state.get("repair_attempts", 0) < settings.agent_compiler_max_repairs:
        return "repair_sql"
    return "truth_compile"


def _compiler_input(question: str) -> dict[str, Any]:
    return {
        "question": question,
        "trace": [],
        "errors": [],
    }


def _thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}


def _recent_turns(state: AnalystCompilerState) -> list[dict[str, Any]]:
    turns = state.get("turns", [])
    return turns[-3:] if isinstance(turns, list) else []


def _turn(state: AnalystCompilerState, *, final_answer: str) -> dict[str, Any]:
    return {
        "question": state.get("question"),
        "intent_frame": state.get("intent_frame"),
        "route": state.get("route"),
        "sql_plan": state.get("sql_plan"),
        "final_answer": final_answer,
    }


def _trace(node: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "node": node,
        "details": to_jsonable(details),
    }


def _context_query(question: str, intent: dict[str, Any]) -> str:
    pieces = [question]
    pieces.extend(str(item) for item in intent.get("metrics", []) if item)
    pieces.extend(str(item) for item in intent.get("dimensions", []) if item)
    pieces.extend(str(item) for item in intent.get("business_entities", []) if item)
    for item in intent.get("filters", []):
        if isinstance(item, dict):
            pieces.append(str(item.get("term", "")))
            pieces.append(str(item.get("value", "")))
    return " ".join(piece for piece in pieces if piece)


def _safe_business_search(
    repository: BusinessGroundingRepository,
    query: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    try:
        return repository.search(query, limit=limit)
    except Exception as exc:  # noqa: BLE001 - repository absence becomes context signal
        return [{"status": "not_found", "error": str(exc)}]


def _safe_schema_search(
    repository: LearnedArtifactRepository,
    query: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    try:
        return [
            {
                "kind": hit.kind,
                "table_name": hit.table_name,
                "column_name": hit.column_name,
                "short_description": hit.short_description,
                "score": hit.score,
            }
            for hit in repository.search_descriptions(query, limit=limit)
        ]
    except Exception as exc:  # noqa: BLE001
        return [{"status": "not_found", "error": str(exc)}]


def _safe_join_pairs(repository: LearnedArtifactRepository) -> list[dict[str, Any]]:
    try:
        return repository.load_joinable_pairs()
    except Exception as exc:  # noqa: BLE001
        return [{"status": "not_found", "error": str(exc)}]


def _safe_table_descriptions(repository: LearnedArtifactRepository) -> dict[str, str]:
    try:
        return repository.table_descriptions()
    except Exception:
        return {}


def _available_schema(
    *,
    repository: LearnedArtifactRepository,
    query_engine: QueryEngine,
) -> list[dict[str, Any]]:
    rows = []
    table_descriptions = _safe_table_descriptions(repository)
    for table_name in sorted(query_engine.list_tables()):
        columns = []
        for column in query_engine.describe_table(table_name):
            columns.append(
                {
                    "name": column.name,
                    "data_type": column.data_type,
                    "short_description": repository.column_description(
                        table_name,
                        column.name,
                    )
                    or "",
                }
            )
        rows.append(
            {
                "table_name": table_name,
                "short_description": table_descriptions.get(table_name, ""),
                "columns": columns,
            }
        )
    return rows


def _profile_hints(
    repository: LearnedArtifactRepository,
    *,
    schema_matches: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    hints = []
    seen: set[tuple[str, str]] = set()
    for match in schema_matches:
        table = match.get("table_name")
        column = match.get("column_name")
        if not isinstance(table, str) or not isinstance(column, str):
            continue
        key = (table, column)
        if key in seen:
            continue
        seen.add(key)
        try:
            hint = repository.profile_column_values(
                table_name=table,
                column_name=column,
                limit=limit,
            )
        except Exception:
            hint = None
        if hint is not None:
            hints.append(hint)
    return hints


def _has_metric_context(intent: dict[str, Any], context: dict[str, Any]) -> bool:
    metrics = {str(metric).lower() for metric in intent.get("metrics", [])}
    if not metrics:
        return False
    for match in context.get("business_matches", []):
        if not isinstance(match, dict):
            continue
        section = str(match.get("section", ""))
        if section in {"metrics", "sql_templates", "glossary", "definitions", "defaults"}:
            return True
    return False


def _planner_payload(
    state: AnalystCompilerState,
    *,
    settings: DiracDataSettings,
) -> dict[str, Any]:
    return {
        "question": state.get("question"),
        "current_date": _current_date(),
        "route": state.get("route"),
        "intent_frame": state.get("intent_frame"),
        "context": state.get("context"),
        "sql_dialect": settings.sql_dialect,
        "requirements": {
            "max_probe_queries": settings.agent_compiler_max_probes,
            "probe_max_rows": settings.agent_compiler_probe_max_rows,
            "final_max_rows": settings.agent_sql_max_rows,
        },
    }


def _current_date() -> str:
    return date.today().isoformat()


def _probe_sql(plan: dict[str, Any], *, settings: DiracDataSettings) -> list[str]:
    value = plan.get("probe_sql")
    if not isinstance(value, list):
        return []
    return [str(sql) for sql in value if str(sql).strip()][: settings.agent_compiler_max_probes]


def _validate_sql(
    sql: str,
    *,
    query_engine: QueryEngine,
    settings: DiracDataSettings,
) -> dict[str, Any]:
    validation = validate_read_only_sql(
        sql,
        available_tables=set(query_engine.list_tables()),
        sql_dialect=settings.sql_dialect,
    )
    if validation.get("status") == "ok":
        dry_run = _dry_run_sql(sql, query_engine=query_engine)
        if dry_run.get("status") != "ok":
            validation = dry_run
    return {"sql": sql, **validation}


def _dry_run_sql(sql: str, *, query_engine: QueryEngine) -> dict[str, Any]:
    try:
        query_engine.query(sql, max_rows=0)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "error": f"SQL dry run failed: {exc}",
        }
    return {"status": "ok"}


def _execute_sql(
    sql: str,
    *,
    query_engine: QueryEngine,
    max_rows: int,
) -> dict[str, Any]:
    try:
        result = query_engine.query(sql, max_rows=max_rows)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "error": str(exc),
            "sql": sql,
        }
    return {
        "status": "ok",
        "sql": sql,
        "columns": result.columns,
        "rows": [
            {
                column: to_jsonable(value)
                for column, value in zip(result.columns, row, strict=False)
            }
            for row in result.rows
        ],
        "row_count": len(result.rows),
        "max_rows": max_rows,
        "possibly_truncated": len(result.rows) >= max_rows,
    }


def _compact_truth_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "business_matches": context.get("business_matches", [])[:5],
        "joinable_pairs": context.get("joinable_pairs", [])[:20],
    }


def _result_facts(sql_result: dict[str, Any]) -> dict[str, Any]:
    rows = sql_result.get("rows")
    columns = sql_result.get("columns")
    if not isinstance(rows, list):
        rows = []
    if not isinstance(columns, list):
        columns = []
    numeric_ranges: dict[str, dict[str, float]] = {}
    for column in columns:
        values = [
            row.get(column)
            for row in rows
            if isinstance(row, dict) and isinstance(row.get(column), (int, float))
        ]
        if not values:
            continue
        numeric_ranges[str(column)] = {
            "min": min(float(value) for value in values),
            "max": max(float(value) for value in values),
        }
    return {
        "status": sql_result.get("status"),
        "row_count": sql_result.get("row_count"),
        "columns": columns,
        "numeric_ranges": numeric_ranges,
        "possibly_truncated": sql_result.get("possibly_truncated"),
    }


def _render_verified_answer(
    *,
    question: str,
    sql_result: dict[str, Any],
    truth_report: dict[str, Any],
) -> str:
    if sql_result.get("status") != "ok":
        return str(truth_report.get("answer", "")).strip()

    rows = sql_result.get("rows")
    columns = sql_result.get("columns")
    if not isinstance(rows, list):
        rows = []
    if not isinstance(columns, list):
        columns = []

    lines = [
        "Result",
        f"Question: {question}",
        f"Verification: {truth_report.get('verification_status', 'unknown')}",
        "",
    ]
    if len(rows) == 1 and len(columns) == 1 and isinstance(rows[0], dict):
        column = str(columns[0])
        lines.append(f"`{column}` = {_format_answer_value(rows[0].get(column))}")
    elif rows and columns:
        lines.extend(_markdown_table(rows, [str(column) for column in columns]))
    elif not rows:
        lines.append("No rows returned.")

    if sql_result.get("possibly_truncated"):
        lines.extend(["", "Result may be truncated by the configured row limit."])

    caveats = truth_report.get("caveats")
    if isinstance(caveats, list) and caveats:
        lines.extend(["", "Caveats"])
        lines.extend(f"- {str(caveat)}" for caveat in caveats)

    checks = truth_report.get("checks_performed")
    if isinstance(checks, list) and checks:
        lines.extend(["", "Checks"])
        lines.extend(f"- {str(check)}" for check in checks[:5])

    return "\n".join(lines).strip()


def _markdown_table(rows: list[Any], columns: list[str]) -> list[str]:
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _column in columns) + " |"
    body = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        body.append(
            "| "
            + " | ".join(_format_answer_value(row.get(column)) for column in columns)
            + " |"
        )
    return [header, separator, *body]


def _format_answer_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    if value is None:
        return ""
    return str(value)


def _model_dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _coerce_structured_response(schema: type[BaseModel], response: object) -> BaseModel:
    if isinstance(response, schema):
        return response
    if isinstance(response, dict):
        return schema.model_validate(response)
    structured = getattr(response, "structured_response", None)
    if structured is not None:
        return _coerce_structured_response(schema, structured)
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    if not isinstance(content, str):
        raise TypeError(f"Cannot coerce {type(response).__name__} into {schema.__name__}")
    return schema.model_validate_json(_extract_json(content))


def _extract_json(text: str) -> str:
    clean = text.strip()
    if clean.startswith("{") and clean.endswith("}"):
        return clean
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean, flags=re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = clean.find("{")
    end = clean.rfind("}")
    if start >= 0 and end > start:
        return clean[start : end + 1]
    raise ValueError("Model did not return valid JSON")
