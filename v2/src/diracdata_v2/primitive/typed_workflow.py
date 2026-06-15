"""Typed workflow kernel for the primitive data-agent harness.

The typed workflow keeps LLM reasoning inside stages, but owns the stage
transitions in code. It is intentionally stricter than the supervisor workflow:
SQL cannot execute just because a model says it will validate next.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from diracdata_v2.primitive.contracts import GateDecision, HarnessStage
from diracdata_v2.primitive.gated import StatusPacket, parse_status_packet
from diracdata_v2.primitive.runner import PrimitiveAgentRunner, PrimitiveRunResult, PrimitiveTraceEvent


@dataclass(frozen=True)
class SemanticAssertion:
    name: str
    decision: GateDecision
    message: str
    evidence: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "decision": self.decision.value,
            "message": self.message,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class TypedStageRun:
    stage: HarnessStage
    name: str
    result: PrimitiveRunResult
    packet: StatusPacket | None


@dataclass(frozen=True)
class TypedWorkflowConfig:
    max_sql_repairs: int = 1
    enable_data_engineering: bool = True
    min_ctes_for_data_engineering: int = 4


@dataclass
class _WorkflowState:
    question: str
    clarification: str | None = None
    previous_context: str | None = None
    compiled_context: dict[str, Any] = field(default_factory=dict)
    intent: TypedStageRun | None = None
    sql_author: TypedStageRun | None = None
    steward: TypedStageRun | None = None
    data_engineer: TypedStageRun | None = None
    final_sql: str = ""
    assertions: list[SemanticAssertion] = field(default_factory=list)
    dry_run: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)


class TypedPrimitiveWorkflow:
    """Deterministic workflow over intent, SQL authoring, assertions, and execution."""

    def __init__(
        self,
        *,
        intent: PrimitiveAgentRunner,
        sql_author: PrimitiveAgentRunner,
        steward: PrimitiveAgentRunner,
        data_engineer: PrimitiveAgentRunner,
        sql_dry_run_tool: Any,
        final_execute_tool: Any,
        context_compiler: Callable[[str], Any] | None = None,
        config: TypedWorkflowConfig | None = None,
    ) -> None:
        self.intent = intent
        self.sql_author = sql_author
        self.steward = steward
        self.data_engineer = data_engineer
        self.sql_dry_run_tool = sql_dry_run_tool
        self.final_execute_tool = final_execute_tool
        self.context_compiler = context_compiler
        self.config = config or TypedWorkflowConfig()

    def run(
        self,
        question: str,
        *,
        clarification: str | None = None,
        previous_context: str | None = None,
        event_sink: Callable[[PrimitiveTraceEvent], None] | None = None,
    ) -> PrimitiveRunResult:
        events: list[PrimitiveTraceEvent] = []
        state = _WorkflowState(
            question=question,
            clarification=clarification,
            previous_context=previous_context,
        )
        _append_event(
            events,
            event_sink,
            _event(
                "typed_workflow",
                "typed_start",
                {
                    "question": question,
                    "has_clarification": bool(clarification),
                    "has_previous_context": bool(previous_context),
                },
            ),
        )
        state.compiled_context = self._compile_context(question=question, events=events, event_sink=event_sink)
        if state.compiled_context.get("status") == "error":
            return _finish_blocked(
                reason="Semantic context compilation failed.",
                evidence=str(state.compiled_context),
                events=events,
                event_sink=event_sink,
                iterations=0,
            )
        if _compiled_context_needs_clarification(state.compiled_context) and not clarification:
            return _clarification_result(
                source="semantic_catalog_compiler",
                question=_compiled_context_clarification_text(state.compiled_context),
                choices=_compiled_context_clarification_choices(state.compiled_context),
                previous_context=_compiled_context_text(state.compiled_context),
                events=events,
                event_sink=event_sink,
                iterations=0,
            )

        intent_run = self._run_stage(
            stage=HarnessStage.INTENT,
            name="intent_subagent",
            runner=self.intent,
            task=_intent_task(state),
            events=events,
            event_sink=event_sink,
        )
        state.intent = intent_run
        if intent_run.packet is None or intent_run.packet.component != "intent":
            return _finish_blocked(
                reason="Intent stage did not return a parseable INTENT_STATUS packet.",
                evidence=intent_run.result.output_text,
                events=events,
                event_sink=event_sink,
                iterations=1,
            )
        if intent_run.packet.status == "NEEDS_CLARIFICATION":
            return _clarification_from_packet(
                packet=intent_run.packet,
                source="intent_subagent",
                previous_context=intent_run.result.output_text,
                events=events,
                event_sink=event_sink,
                iterations=1,
            )
        if intent_run.packet.status != "OK":
            return _finish_blocked(
                reason=f"Intent stage returned INTENT_STATUS: {intent_run.packet.status}.",
                evidence=intent_run.result.output_text,
                events=events,
                event_sink=event_sink,
                iterations=1,
            )

        repair_feedback: str | None = None
        iterations = 1
        for repair_index in range(self.config.max_sql_repairs + 1):
            sql_run = self._run_stage(
                stage=HarnessStage.SQL_AUTHORING,
                name="sql_author_subagent",
                runner=self.sql_author,
                task=_sql_author_task(state=state, repair_feedback=repair_feedback),
                events=events,
                event_sink=event_sink,
            )
            iterations += 1
            state.sql_author = sql_run
            if sql_run.packet is None or sql_run.packet.component != "sql_author":
                return _finish_blocked(
                    reason="SQL authoring stage did not return a parseable SQL_AUTHOR_STATUS packet.",
                    evidence=sql_run.result.output_text,
                    events=events,
                    event_sink=event_sink,
                    iterations=iterations,
                )
            if sql_run.packet.status == "NEEDS_CLARIFICATION":
                return _clarification_from_packet(
                    packet=sql_run.packet,
                    source="sql_author_subagent",
                    previous_context=_resume_context(state),
                    events=events,
                    event_sink=event_sink,
                    iterations=iterations,
                )
            if sql_run.packet.status != "OK":
                return _finish_blocked(
                    reason=f"SQL authoring stage returned SQL_AUTHOR_STATUS: {sql_run.packet.status}.",
                    evidence=sql_run.result.output_text,
                    events=events,
                    event_sink=event_sink,
                    iterations=iterations,
                )

            state.final_sql = _extract_sql_section(sql_run.packet, "FINAL_SQL")
            state.assertions = evaluate_semantic_assertions(
                question=question,
                intent_packet=state.intent.packet,
                sql_packet=sql_run.packet,
                sql=state.final_sql,
                compiled_context=state.compiled_context,
            )
            _append_event(
                events,
                event_sink,
                _event(
                    "typed_workflow",
                    "assertions_evaluated",
                    {
                        "stage": HarnessStage.SQL_AUTHORING.value,
                        "failed": [item.to_dict() for item in state.assertions if item.decision == GateDecision.FAIL],
                        "passed": [item.to_dict() for item in state.assertions if item.decision == GateDecision.PASS],
                    },
                ),
            )
            failed_assertions = [item for item in state.assertions if item.decision == GateDecision.FAIL]
            if failed_assertions:
                if repair_index < self.config.max_sql_repairs:
                    repair_feedback = _assertion_feedback(failed_assertions)
                    continue
                return _finish_blocked(
                    reason="SQL failed deterministic semantic assertions.",
                    evidence=_assertion_feedback(failed_assertions),
                    events=events,
                    event_sink=event_sink,
                    iterations=iterations,
                )

            state.dry_run = self._dry_run_sql(sql=state.final_sql, events=events, event_sink=event_sink)
            if state.dry_run.get("status") != "ok":
                if repair_index < self.config.max_sql_repairs:
                    repair_feedback = "Dry run failed. Repair the SQL using this error:\n" + str(state.dry_run)
                    continue
                return _finish_blocked(
                    reason="SQL dry run failed.",
                    evidence=str(state.dry_run),
                    events=events,
                    event_sink=event_sink,
                    iterations=iterations,
                )

            steward_run = self._run_stage(
                stage=HarnessStage.STEWARD_REVIEW,
                name="data_steward_subagent",
                runner=self.steward,
                task=_steward_task(state),
                events=events,
                event_sink=event_sink,
            )
            iterations += 1
            state.steward = steward_run
            if steward_run.packet is None or steward_run.packet.component != "steward":
                return _finish_blocked(
                    reason="Steward stage did not return a parseable STEWARD_STATUS packet.",
                    evidence=steward_run.result.output_text,
                    events=events,
                    event_sink=event_sink,
                    iterations=iterations,
                )
            if steward_run.packet.status == "NEEDS_CLARIFICATION":
                return _clarification_from_packet(
                    packet=steward_run.packet,
                    source="data_steward_subagent",
                    previous_context=_resume_context(state),
                    events=events,
                    event_sink=event_sink,
                    iterations=iterations,
                )
            if steward_run.packet.status == "FAIL":
                if repair_index < self.config.max_sql_repairs:
                    repair_feedback = "Steward failed the SQL. Repair using this feedback:\n" + steward_run.result.output_text
                    continue
                return _finish_blocked(
                    reason="Steward failed the SQL after repair budget was exhausted.",
                    evidence=steward_run.result.output_text,
                    events=events,
                    event_sink=event_sink,
                    iterations=iterations,
                )
            if steward_run.packet.status not in {"PASS", "PASS_WITH_ASSUMPTIONS"}:
                return _finish_blocked(
                    reason=f"Steward returned STEWARD_STATUS: {steward_run.packet.status}.",
                    evidence=steward_run.result.output_text,
                    events=events,
                    event_sink=event_sink,
                    iterations=iterations,
                )
            break
        else:
            return _finish_blocked(
                reason="SQL repair loop exhausted without steward approval.",
                evidence=_resume_context(state),
                events=events,
                event_sink=event_sink,
                iterations=iterations,
            )

        if self.config.enable_data_engineering and _should_run_data_engineering(state.final_sql):
            de_result = self._run_data_engineering_gate(state=state, events=events, event_sink=event_sink)
            iterations += de_result["iterations"]
            if de_result["status"] == "blocked":
                return _finish_blocked(
                    reason=str(de_result["reason"]),
                    evidence=str(de_result["evidence"]),
                    events=events,
                    event_sink=event_sink,
                    iterations=iterations,
                )

        state.execution = self._execute_sql(sql=state.final_sql, events=events, event_sink=event_sink)
        iterations += 1
        if state.execution.get("status") != "ok":
            return _finish_blocked(
                reason="Final SQL execution failed.",
                evidence=str(state.execution),
                events=events,
                event_sink=event_sink,
                iterations=iterations,
            )
        return _finish(
            output_text=_render_final_answer(state),
            events=events,
            event_sink=event_sink,
            iterations=iterations,
            stop_reason="final",
        )

    def _run_data_engineering_gate(
        self,
        *,
        state: _WorkflowState,
        events: list[PrimitiveTraceEvent],
        event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    ) -> dict[str, Any]:
        de_run = self._run_stage(
            stage=HarnessStage.DATA_ENGINEERING,
            name="data_engineer_subagent",
            runner=self.data_engineer,
            task=_data_engineering_task(state),
            events=events,
            event_sink=event_sink,
        )
        state.data_engineer = de_run
        if de_run.packet is None or de_run.packet.component != "data_engineering":
            return {
                "status": "blocked",
                "reason": "Data Engineering stage did not return a parseable DE_STATUS packet.",
                "evidence": de_run.result.output_text,
                "iterations": 1,
            }
        if de_run.packet.status == "UNCHANGED":
            return {"status": "ok", "iterations": 1}
        if de_run.packet.status != "OPTIMIZED":
            return {
                "status": "blocked",
                "reason": f"Data Engineering returned DE_STATUS: {de_run.packet.status}.",
                "evidence": de_run.result.output_text,
                "iterations": 1,
            }

        optimized_sql = _extract_sql_section(de_run.packet, "OPTIMIZED_SQL")
        assertions = evaluate_semantic_assertions(
            question=state.question,
            intent_packet=state.intent.packet if state.intent else None,
            sql_packet=de_run.packet,
            sql=optimized_sql,
            compiled_context=state.compiled_context,
        )
        _append_event(
            events,
            event_sink,
            _event(
                "typed_workflow",
                "assertions_evaluated",
                {
                    "stage": HarnessStage.DATA_ENGINEERING.value,
                    "failed": [item.to_dict() for item in assertions if item.decision == GateDecision.FAIL],
                    "passed": [item.to_dict() for item in assertions if item.decision == GateDecision.PASS],
                },
            ),
        )
        failed = [item for item in assertions if item.decision == GateDecision.FAIL]
        if failed:
            return {
                "status": "blocked",
                "reason": "Data Engineering SQL failed deterministic semantic assertions.",
                "evidence": _assertion_feedback(failed),
                "iterations": 1,
            }
        dry_run = self._dry_run_sql(sql=optimized_sql, events=events, event_sink=event_sink)
        if dry_run.get("status") != "ok":
            return {
                "status": "blocked",
                "reason": "Data Engineering SQL dry run failed.",
                "evidence": dry_run,
                "iterations": 1,
            }
        previous_sql = state.final_sql
        previous_sql_author = state.sql_author
        state.final_sql = optimized_sql
        state.dry_run = dry_run
        steward_run = self._run_stage(
            stage=HarnessStage.STEWARD_REVIEW,
            name="data_steward_subagent",
            runner=self.steward,
            task=_post_de_steward_task(state=state, previous_sql=previous_sql),
            events=events,
            event_sink=event_sink,
        )
        state.steward = steward_run
        if steward_run.packet is None or steward_run.packet.component != "steward":
            state.final_sql = previous_sql
            state.sql_author = previous_sql_author
            return {
                "status": "blocked",
                "reason": "Steward did not validate Data Engineering SQL.",
                "evidence": steward_run.result.output_text,
                "iterations": 2,
            }
        if steward_run.packet.status not in {"PASS", "PASS_WITH_ASSUMPTIONS"}:
            state.final_sql = previous_sql
            state.sql_author = previous_sql_author
            return {
                "status": "blocked",
                "reason": f"Steward rejected Data Engineering SQL with status {steward_run.packet.status}.",
                "evidence": steward_run.result.output_text,
                "iterations": 2,
            }
        return {"status": "ok", "iterations": 2}

    def _run_stage(
        self,
        *,
        stage: HarnessStage,
        name: str,
        runner: PrimitiveAgentRunner,
        task: str,
        events: list[PrimitiveTraceEvent],
        event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    ) -> TypedStageRun:
        _append_event(
            events,
            event_sink,
            _event("typed_workflow", "stage_start", {"stage": stage.value, "name": name, "task": task}),
        )
        result = _run_runner_with_optional_streaming(
            runner=runner,
            task=task,
            events=events,
            event_sink=event_sink,
        )
        packet = parse_status_packet(result.output_text)
        _append_event(
            events,
            event_sink,
            _event(
                "typed_workflow",
                "stage_done",
                {
                    "stage": stage.value,
                    "name": name,
                    "stop_reason": result.stop_reason,
                    "component": packet.component if packet else None,
                    "status": packet.status if packet else None,
                    "output_preview": _truncate(result.output_text, 2400),
                },
            ),
        )
        return TypedStageRun(stage=stage, name=name, result=result, packet=packet)

    def _compile_context(
        self,
        *,
        question: str,
        events: list[PrimitiveTraceEvent],
        event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    ) -> dict[str, Any]:
        if self.context_compiler is None:
            return {}
        try:
            compiled = self.context_compiler(question)
            payload = _plain_compiled_context(compiled)
        except Exception as exc:  # noqa: BLE001
            payload = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        _append_event(
            events,
            event_sink,
            _event("typed_workflow", "context_compiled", _compiled_context_event_payload(payload)),
        )
        return payload

    def _dry_run_sql(
        self,
        *,
        sql: str,
        events: list[PrimitiveTraceEvent],
        event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    ) -> dict[str, Any]:
        return _invoke_tool(
            tool=self.sql_dry_run_tool,
            name="sql_dry_run",
            stage=HarnessStage.SQL_AUTHORING,
            args={"sql": sql},
            events=events,
            event_sink=event_sink,
        )

    def _execute_sql(
        self,
        *,
        sql: str,
        events: list[PrimitiveTraceEvent],
        event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    ) -> dict[str, Any]:
        return _invoke_tool(
            tool=self.final_execute_tool,
            name="execute_sql",
            stage=HarnessStage.FINAL_EXECUTION,
            args={"sql": sql},
            events=events,
            event_sink=event_sink,
        )


def evaluate_semantic_assertions(
    *,
    question: str,
    intent_packet: StatusPacket | None,
    sql_packet: StatusPacket | None,
    sql: str,
    compiled_context: dict[str, Any] | None = None,
) -> list[SemanticAssertion]:
    assertions: list[SemanticAssertion] = []
    clean_sql = _clean_sql(sql)
    if not clean_sql:
        assertions.append(
            SemanticAssertion(
                name="final_sql_present",
                decision=GateDecision.FAIL,
                message="SQL packet does not contain a FINAL_SQL or OPTIMIZED_SQL statement.",
            )
        )
        return assertions
    assertions.append(
        SemanticAssertion(
            name="final_sql_present",
            decision=GateDecision.PASS,
            message="SQL statement is present.",
        )
    )
    pattern_note = _pattern_support_note(compiled_context)
    if pattern_note:
        assertions.append(
            SemanticAssertion(
                name="gold_pattern_support",
                decision=GateDecision.PASS,
                message="Compiled context supplied gold/query-history pattern evidence.",
                evidence=pattern_note,
            )
        )
    return assertions


def _intent_task(state: _WorkflowState) -> str:
    parts = [
        "Create an intent packet. Do not write SQL and do not call SQL tools.",
        f"USER_QUESTION:\n{state.question}",
    ]
    if state.compiled_context:
        parts.append(f"COMPILED_SEMANTIC_CONTEXT:\n{_compiled_context_text(state.compiled_context)}")
    if state.previous_context:
        parts.append(f"PREVIOUS_CONTEXT:\n{state.previous_context}")
    if state.clarification:
        parts.append(f"USER_CLARIFICATION:\n{state.clarification}")
        parts.append("Use the clarification to resolve only the prior ambiguity, then return a fresh intent packet.")
    return "\n\n".join(parts)


def _sql_author_task(*, state: _WorkflowState, repair_feedback: str | None) -> str:
    assert state.intent is not None
    parts = [
        "Write a SQL Author packet from the approved intent. Use sql_dry_run only; do not execute final SQL.",
        "The approved intent packet is the executable contract.",
        f"ORIGINAL_USER_QUESTION:\n{state.question}",
        f"APPROVED_INTENT_PACKET:\n{state.intent.result.output_text}",
    ]
    if state.compiled_context:
        parts.append(f"COMPILED_SEMANTIC_CONTEXT:\n{_compiled_context_text(state.compiled_context)}")
    if repair_feedback:
        parts.append(f"REPAIR_FEEDBACK_FROM_HARNESS:\n{repair_feedback}")
    return "\n\n".join(parts)


def _steward_task(state: _WorkflowState) -> str:
    assert state.intent is not None
    assert state.sql_author is not None
    return "\n\n".join(
        [
            "Validate this SQL Author packet as a semantic unit test. Use sql_dry_run only; do not execute final SQL.",
            "The approved intent packet is the executable contract.",
            f"ORIGINAL_USER_QUESTION:\n{state.question}",
            f"APPROVED_INTENT_PACKET:\n{state.intent.result.output_text}",
            f"SQL_AUTHOR_PACKET:\n{state.sql_author.result.output_text}",
            f"HARNESS_ASSERTIONS:\n{_assertion_report(state.assertions)}",
            f"HARNESS_DRY_RUN:\n{state.dry_run}",
            "Return PASS only when the SQL can be executed exactly by the harness with no SQL-affecting assumptions.",
        ]
    )


def _data_engineering_task(state: _WorkflowState) -> str:
    assert state.intent is not None
    assert state.sql_author is not None
    assert state.steward is not None
    return "\n\n".join(
        [
            "Optimize this Steward-approved SQL for cost/readability only. Do not change business semantics.",
            f"ORIGINAL_USER_QUESTION:\n{state.question}",
            f"APPROVED_INTENT_PACKET:\n{state.intent.result.output_text}",
            f"SQL_AUTHOR_PACKET:\n{state.sql_author.result.output_text}",
            f"STEWARD_PACKET:\n{state.steward.result.output_text}",
            "Return DE_STATUS: UNCHANGED or DE_STATUS: OPTIMIZED with OPTIMIZED_SQL.",
        ]
    )


def _post_de_steward_task(*, state: _WorkflowState, previous_sql: str) -> str:
    assert state.intent is not None
    assert state.data_engineer is not None
    return "\n\n".join(
        [
            "Validate this Data Engineering optimized SQL as a semantic unit test before execution.",
            "The approved intent packet is the executable contract.",
            f"ORIGINAL_USER_QUESTION:\n{state.question}",
            f"APPROVED_INTENT_PACKET:\n{state.intent.result.output_text}",
            f"PRE_DE_SQL:\n{previous_sql}",
            f"DATA_ENGINEERING_PACKET:\n{state.data_engineer.result.output_text}",
            f"OPTIMIZED_SQL_UNDER_REVIEW:\n{state.final_sql}",
            f"HARNESS_DRY_RUN:\n{state.dry_run}",
            "Return PASS only if the optimized SQL preserves every measure, filter, exclusion, grain, and output dimension.",
        ]
    )


def _run_runner_with_optional_streaming(
    *,
    runner: PrimitiveAgentRunner,
    task: str,
    events: list[PrimitiveTraceEvent],
    event_sink: Callable[[PrimitiveTraceEvent], None] | None,
) -> PrimitiveRunResult:
    if event_sink is None:
        result = runner.run(task)
        for event in result.trace_events:
            _append_event(events, None, event)
        return result
    stream = runner._run_events(task=task, context=None, stream_model=True)
    while True:
        try:
            event = next(stream)
        except StopIteration as stop:
            return stop.value
        _append_event(events, event_sink, event)


def _invoke_tool(
    *,
    tool: Any,
    name: str,
    stage: HarnessStage,
    args: dict[str, Any],
    events: list[PrimitiveTraceEvent],
    event_sink: Callable[[PrimitiveTraceEvent], None] | None,
) -> dict[str, Any]:
    _append_event(
        events,
        event_sink,
        _event("typed_workflow", "tool_call", {"stage": stage.value, "name": name, "args": args}),
    )
    try:
        payload = tool.invoke(args)
    except Exception as exc:  # noqa: BLE001
        payload = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(payload, dict):
        payload = {"status": "error", "payload": payload}
    _append_event(
        events,
        event_sink,
        _event(
            "typed_workflow",
            "tool_result",
            {
                "stage": stage.value,
                "name": name,
                "preview": _truncate(str(payload), 2400),
                "truncated": len(str(payload)) > 2400,
            },
        ),
    )
    return payload


def _extract_sql_section(packet: StatusPacket, section_name: str) -> str:
    value = packet.sections.get(section_name, "")
    return _clean_sql(value)


def _clean_sql(value: str) -> str:
    clean = value.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:sql)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    return clean.strip().rstrip(";")


def _has_anti_join_pattern(lowered_sql: str) -> bool:
    if re.search(r"\bnot\s+exists\s*\(", lowered_sql):
        return True
    if re.search(r"\bexcept\b", lowered_sql):
        return True
    return bool(re.search(r"\bleft\s+join\b[\s\S]+\bis\s+null\b", lowered_sql))


def _pattern_support_note(compiled_context: dict[str, Any] | None) -> str:
    if not compiled_context:
        return ""
    patterns = compiled_context.get("sql_patterns")
    if not isinstance(patterns, list) or not patterns:
        return ""
    names = []
    for pattern in patterns[:5]:
        if isinstance(pattern, dict):
            name = pattern.get("name") or pattern.get("id")
            if name:
                names.append(str(name))
    return "; ".join(names)


def _should_run_data_engineering(sql: str) -> bool:
    lowered = sql.lower()
    cte_count = len(re.findall(r"(?:with|,)\s+[a-zA-Z_][\w]*\s+as\s*\(", lowered))
    has_anti = _has_anti_join_pattern(lowered) or " not in " in lowered
    repeated_fact_scans = len(re.findall(r"\bfrom\s+([a-zA-Z_][\w]*)\b", lowered)) >= 3
    return cte_count >= 4 or has_anti or repeated_fact_scans


def _assertion_feedback(assertions: list[SemanticAssertion]) -> str:
    return "\n".join(
        f"- {item.name}: {item.message}\n  Evidence: {item.evidence}"
        for item in assertions
    )


def _assertion_report(assertions: list[SemanticAssertion]) -> str:
    if not assertions:
        return "<none>"
    return "\n".join(
        f"- {item.name}: {item.decision.value} - {item.message}"
        for item in assertions
    )


def _render_final_answer(state: _WorkflowState) -> str:
    steward_status = state.steward.packet.status if state.steward and state.steward.packet else "PASS"
    status = "FINAL_STATUS: PASS_WITH_ASSUMPTIONS" if steward_status == "PASS_WITH_ASSUMPTIONS" else "FINAL_STATUS: PASS"
    return "\n".join(
        [
            status,
            "",
            "RESULT:",
            _result_table(state.execution),
            "",
            "HOW_I_INTERPRETED_THIS:",
            _interpretation_text(state),
            "",
            "VERIFICATION:",
            _verification_text(state),
            "",
            "ASSUMPTIONS:",
            _assumption_text(state),
            "",
            "SQL_USED:",
            "```sql",
            state.final_sql,
            "```",
        ]
    ).strip()


def _interpretation_text(state: _WorkflowState) -> str:
    if state.intent and state.intent.packet:
        return state.intent.packet.sections.get("INTENT_SUMMARY", state.intent.result.output_text)
    return ""


def _verification_text(state: _WorkflowState) -> str:
    parts = [_assertion_report(state.assertions)]
    if state.steward and state.steward.packet:
        parts.append(state.steward.packet.sections.get("EVIDENCE", state.steward.result.output_text))
    return "\n\n".join(part for part in parts if part).strip()


def _assumption_text(state: _WorkflowState) -> str:
    if state.steward and state.steward.packet:
        return state.steward.packet.sections.get("ASSUMPTIONS", "none")
    if state.sql_author and state.sql_author.packet:
        return state.sql_author.packet.sections.get("ASSUMPTIONS", "none")
    return "none"


def _result_table(execution: dict[str, Any]) -> str:
    columns = execution.get("columns")
    rows = execution.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return str(execution)
    output = [
        "| " + " | ".join(str(column) for column in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows[:10]:
        if isinstance(row, dict):
            output.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(output)


def _resume_context(state: _WorkflowState) -> str:
    parts = []
    if state.intent:
        parts.extend(["INTENT_PACKET:", state.intent.result.output_text])
    if state.sql_author:
        parts.extend(["SQL_AUTHOR_PACKET:", state.sql_author.result.output_text])
    if state.steward:
        parts.extend(["STEWARD_PACKET:", state.steward.result.output_text])
    return "\n\n".join(parts)


def _clarification_from_packet(
    *,
    packet: StatusPacket,
    source: str,
    previous_context: str,
    events: list[PrimitiveTraceEvent],
    event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    iterations: int,
) -> PrimitiveRunResult:
    question = (
        packet.sections.get("CLARIFICATION_QUESTION")
        or packet.sections.get("ISSUES")
        or "A SQL-affecting clarification is needed before I can answer safely."
    )
    return _clarification_result(
        source=source,
        question=question,
        choices=_clarification_choices(packet),
        previous_context=previous_context,
        events=events,
        event_sink=event_sink,
        iterations=iterations,
    )


def _clarification_result(
    *,
    source: str,
    question: str,
    choices: list[str],
    previous_context: str,
    events: list[PrimitiveTraceEvent],
    event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    iterations: int,
) -> PrimitiveRunResult:
    _append_event(
        events,
        event_sink,
        _event(
            "typed_workflow",
            "clarification_required",
            {
                "source": source,
                "question": question,
                "choices": choices,
                "previous_context": _truncate(previous_context, 4000),
            },
        ),
    )
    return _finish(
        output_text="CLARIFICATION_REQUIRED\n" + question,
        events=events,
        event_sink=event_sink,
        iterations=iterations,
        stop_reason="needs_clarification",
    )


def _clarification_choices(packet: StatusPacket) -> list[str]:
    options = packet.sections.get("MCQ_OPTIONS", "")
    choices: list[str] = []
    for line in options.splitlines():
        clean = re.sub(r"^\s*(?:[-*]|\d+[\).])\s*", "", line.strip()).strip()
        if clean:
            choices.append(clean)
    return choices[:4]


def _compiled_context_needs_clarification(compiled_context: dict[str, Any]) -> bool:
    return bool(compiled_context.get("needs_clarification"))


def _compiled_context_clarification_text(compiled_context: dict[str, Any]) -> str:
    unresolved = compiled_context.get("unresolved_terms")
    if not isinstance(unresolved, list) or not unresolved:
        return "Semantic context found a SQL-affecting ambiguity."
    lines = [
        "Semantic context found a SQL-affecting ambiguity before SQL authoring.",
        "Please choose the intended interpretation.",
    ]
    for item in unresolved:
        if not isinstance(item, dict):
            continue
        term = item.get("term") or "term"
        reason = item.get("reason") or "needs clarification"
        lines.append(f"- {term}: {reason}")
    return "\n".join(lines)


def _compiled_context_clarification_choices(compiled_context: dict[str, Any]) -> list[str]:
    choices: list[str] = []
    unresolved = compiled_context.get("unresolved_terms")
    if not isinstance(unresolved, list):
        return choices
    for item in unresolved:
        if not isinstance(item, dict):
            continue
        item_choices = item.get("choices")
        if isinstance(item_choices, list):
            choices.extend(str(choice).strip() for choice in item_choices if str(choice).strip())
    return choices[:4]


def _compiled_context_text(compiled_context: dict[str, Any]) -> str:
    return _truncate(str(compiled_context), 12000)


def _compiled_context_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "needs_clarification": bool(payload.get("needs_clarification")),
        "candidate_count": len(payload.get("candidate_cards", []) or []),
        "pattern_count": len(payload.get("sql_patterns", []) or []),
        "join_edge_count": len(payload.get("join_edges", []) or []),
        "unresolved_terms": payload.get("unresolved_terms", []) or [],
        "intent_frame": (payload.get("retrieval") or {}).get("intent_frame", {}),
    }


def _plain_compiled_context(compiled: Any) -> dict[str, Any]:
    if hasattr(compiled, "to_dict"):
        value = compiled.to_dict()
    elif isinstance(compiled, dict):
        value = compiled
    else:
        value = {"value": str(compiled)}
    return value if isinstance(value, dict) else {"value": value}


def _finish_blocked(
    *,
    reason: str,
    evidence: str,
    events: list[PrimitiveTraceEvent],
    event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    iterations: int,
) -> PrimitiveRunResult:
    return _finish(
        output_text="\n".join(["FINAL_STATUS: BLOCKED", reason, "", "Evidence:", _truncate(evidence, 4000)]).strip(),
        events=events,
        event_sink=event_sink,
        iterations=iterations,
        stop_reason="blocked",
    )


def _finish(
    *,
    output_text: str,
    events: list[PrimitiveTraceEvent],
    event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    iterations: int,
    stop_reason: str,
) -> PrimitiveRunResult:
    _append_event(
        events,
        event_sink,
        _event(
            "typed_workflow",
            "typed_done",
            {
                "stop_reason": stop_reason,
                "output_preview": _truncate(output_text, 2400),
            },
        ),
    )
    return PrimitiveRunResult(output_text=output_text, trace_events=events, iterations=iterations, stop_reason=stop_reason)


def _event(agent_name: str, event_type: str, payload: dict[str, Any]) -> PrimitiveTraceEvent:
    return PrimitiveTraceEvent(agent_name=agent_name, event_type=event_type, payload=payload)


def _append_event(
    events: list[PrimitiveTraceEvent],
    event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    event: PrimitiveTraceEvent,
) -> None:
    events.append(event)
    if event_sink is not None:
        event_sink(event)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"
