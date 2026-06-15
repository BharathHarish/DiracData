"""Deterministic gates for the primitive analyst-led workflow."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from diracdata_v2.primitive.contracts import GateDecision, HarnessStage
from diracdata_v2.primitive.runner import PrimitiveAgentRunner, PrimitiveRunResult, PrimitiveTraceEvent


@dataclass(frozen=True)
class StatusPacket:
    component: str
    status: str
    sections: dict[str, str] = field(default_factory=dict)

    @property
    def ok_for_final_answer(self) -> bool:
        return (
            (self.component in {"intent", "sql_author", "analyst"} and self.status == "OK")
            or (
                self.component == "steward"
                and self.status in {"PASS", "PASS_WITH_ASSUMPTIONS"}
            )
            or (self.component == "data_engineering" and self.status in {"OPTIMIZED", "UNCHANGED"})
        )


@dataclass(frozen=True)
class SubagentRun:
    name: str
    result: PrimitiveRunResult
    packet: StatusPacket | None


class GatedPrimitiveWorkflow:
    """Code-level orchestration for analyst, steward, and optional DE gates."""

    def __init__(
        self,
        *,
        analyst: PrimitiveAgentRunner,
        steward: PrimitiveAgentRunner,
        data_engineer: PrimitiveAgentRunner,
        intent: PrimitiveAgentRunner | None = None,
        sql_author: PrimitiveAgentRunner | None = None,
        final_execute_tool: Any | None = None,
        context_compiler: Callable[[str], Any] | None = None,
        max_correction_rounds: int = 1,
        enable_data_engineering: bool = True,
    ) -> None:
        self.analyst = analyst
        self.steward = steward
        self.data_engineer = data_engineer
        self.intent = intent
        self.sql_author = sql_author
        self.final_execute_tool = final_execute_tool
        self.context_compiler = context_compiler
        self.max_correction_rounds = max(0, max_correction_rounds)
        self.enable_data_engineering = enable_data_engineering

    def run(
        self,
        question: str,
        *,
        clarification: str | None = None,
        previous_context: str | None = None,
        event_sink: Callable[[PrimitiveTraceEvent], None] | None = None,
    ) -> PrimitiveRunResult:
        events: list[PrimitiveTraceEvent] = []
        subagent_calls = 0
        _append_event(
            events,
            event_sink,
            _event(
                "gated_workflow",
                "gated_start",
                {
                    "question": question,
                    "has_clarification": bool(clarification),
                    "has_previous_context": bool(previous_context),
                },
            )
        )

        if self.intent is not None and self.sql_author is not None and self.final_execute_tool is not None:
            return self._run_staged(
                question=question,
                clarification=clarification,
                previous_context=previous_context,
                events=events,
                event_sink=event_sink,
            )

        analyst_task = _initial_analyst_task(
            question=question,
            clarification=clarification,
            previous_context=previous_context,
        )
        analyst_run = self._run_subagent(
            name="analyst_subagent",
            runner=self.analyst,
            task=analyst_task,
            events=events,
            event_sink=event_sink,
        )
        subagent_calls += 1
        analyst_packet = analyst_run.packet
        if analyst_packet is None or analyst_packet.component != "analyst":
            return _finish(
                output_text=_blocked_text(
                    "The Analyst did not return a parseable `ANALYST_STATUS` packet.",
                    analyst_run.result.output_text,
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )
        if analyst_packet.status == "NEEDS_CLARIFICATION":
            return _clarification_result(
                packet=analyst_packet,
                source="analyst_subagent",
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                previous_context=analyst_run.result.output_text,
            )
        if analyst_packet.status != "OK":
            return _finish(
                output_text=_blocked_text(
                    f"Analyst returned `ANALYST_STATUS: {analyst_packet.status}`.",
                    analyst_run.result.output_text,
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )
        definition_clarification = _definition_clarification_for_analyst_packet(analyst_packet)
        if definition_clarification is not None:
            return _clarification_result(
                packet=definition_clarification,
                source="semantic_definition_guard",
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                previous_context=analyst_run.result.output_text,
            )

        final_analyst_run = analyst_run
        final_steward_run: SubagentRun | None = None
        correction_round = 0
        while correction_round <= self.max_correction_rounds:
            steward_run = self._run_subagent(
                name="data_steward_subagent",
                runner=self.steward,
                task=_steward_task(question=question, analyst_output=final_analyst_run.result.output_text),
                events=events,
                event_sink=event_sink,
            )
            subagent_calls += 1
            final_steward_run = steward_run
            steward_packet = steward_run.packet
            if steward_packet is None or steward_packet.component != "steward":
                return _finish(
                    output_text=_blocked_text(
                        "The Data Steward did not return a parseable `STEWARD_STATUS` packet.",
                        steward_run.result.output_text,
                    ),
                    events=events,
                    event_sink=event_sink,
                    iterations=subagent_calls,
                    stop_reason="blocked",
                )
            if steward_packet.status in {"PASS", "PASS_WITH_ASSUMPTIONS"}:
                return self._maybe_run_de_and_finish(
                    question=question,
                    analyst_run=final_analyst_run,
                    steward_run=steward_run,
                    events=events,
                    subagent_calls=subagent_calls,
                    event_sink=event_sink,
                )
            if steward_packet.status == "NEEDS_CLARIFICATION":
                return _clarification_result(
                    packet=steward_packet,
                    source="data_steward_subagent",
                    events=events,
                    event_sink=event_sink,
                    iterations=subagent_calls,
                    previous_context=_context_for_resume(
                        analyst=final_analyst_run.result.output_text,
                        steward=steward_run.result.output_text,
                    ),
                )
            if steward_packet.status == "FAIL" and correction_round < self.max_correction_rounds:
                correction_round += 1
                final_analyst_run = self._run_subagent(
                    name="analyst_subagent",
                    runner=self.analyst,
                    task=_analyst_correction_task(
                        question=question,
                        analyst_output=final_analyst_run.result.output_text,
                        steward_output=steward_run.result.output_text,
                    ),
                    events=events,
                    event_sink=event_sink,
                )
                subagent_calls += 1
                final_analyst_packet = final_analyst_run.packet
                if final_analyst_packet is None or final_analyst_packet.status != "OK":
                    return _finish(
                        output_text=_blocked_text(
                            "The Analyst could not produce a corrected verified SQL packet.",
                            final_analyst_run.result.output_text,
                        ),
                        events=events,
                        event_sink=event_sink,
                        iterations=subagent_calls,
                        stop_reason="blocked",
                    )
                continue
            return _finish(
                output_text=_blocked_text(
                    f"Data Steward returned `STEWARD_STATUS: {steward_packet.status}`.",
                    steward_run.result.output_text,
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )

        return _finish(
            output_text=_blocked_text(
                "The gated workflow exhausted correction rounds before Steward approval.",
                final_steward_run.result.output_text if final_steward_run else "",
            ),
            events=events,
            event_sink=event_sink,
            iterations=subagent_calls,
            stop_reason="blocked",
        )

    def _run_staged(
        self,
        *,
        question: str,
        clarification: str | None,
        previous_context: str | None,
        events: list[PrimitiveTraceEvent],
        event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    ) -> PrimitiveRunResult:
        assert self.intent is not None
        assert self.sql_author is not None
        assert self.final_execute_tool is not None
        subagent_calls = 0
        compiled_context = self._compile_context(
            question=question,
            events=events,
            event_sink=event_sink,
        )
        if compiled_context.get("status") == "error":
            return _finish(
                output_text=_blocked_text(
                    "Semantic context compilation failed.",
                    str(compiled_context),
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )
        if _compiled_context_needs_clarification(compiled_context) and not clarification:
            return _clarification_result(
                packet=_compiled_context_clarification_packet(compiled_context),
                source="semantic_catalog_compiler",
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                previous_context=_compiled_context_text(compiled_context),
            )

        intent_run = self._run_subagent(
            name="intent_subagent",
            runner=self.intent,
            task=_initial_intent_task(
                question=question,
                clarification=clarification,
                previous_context=previous_context,
                compiled_context=compiled_context,
            ),
            events=events,
            event_sink=event_sink,
        )
        subagent_calls += 1
        intent_packet = intent_run.packet
        if intent_packet is None or intent_packet.component != "intent":
            return _finish(
                output_text=_blocked_text(
                    "The Intent Agent did not return a parseable `INTENT_STATUS` packet.",
                    intent_run.result.output_text,
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )
        intent_gate = _definition_gate_for_packet(intent_packet)
        if intent_packet.status == "NEEDS_CLARIFICATION" or intent_gate is not None:
            return _clarification_result(
                packet=intent_gate or intent_packet,
                source="definition_gate",
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                previous_context=intent_run.result.output_text,
            )
        if intent_packet.status != "OK":
            return _finish(
                output_text=_blocked_text(
                    f"Intent Agent returned `INTENT_STATUS: {intent_packet.status}`.",
                    intent_run.result.output_text,
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )

        sql_author_run = self._run_subagent(
            name="sql_author_subagent",
            runner=self.sql_author,
            task=_sql_author_task(
                question=question,
                intent_output=intent_run.result.output_text,
                compiled_context=compiled_context,
            ),
            events=events,
            event_sink=event_sink,
        )
        subagent_calls += 1
        sql_packet = sql_author_run.packet
        if sql_packet is None or sql_packet.component != "sql_author":
            return _finish(
                output_text=_blocked_text(
                    "The SQL Author did not return a parseable `SQL_AUTHOR_STATUS` packet.",
                    sql_author_run.result.output_text,
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )
        author_gate = _definition_gate_for_packet(sql_packet)
        if sql_packet.status == "NEEDS_CLARIFICATION" or author_gate is not None:
            return _clarification_result(
                packet=author_gate or sql_packet,
                source="sql_author_subagent",
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                previous_context=_context_for_staged_resume(
                    intent=intent_run.result.output_text,
                    sql_author=sql_author_run.result.output_text,
                ),
            )
        if sql_packet.status != "OK":
            return _finish(
                output_text=_blocked_text(
                    f"SQL Author returned `SQL_AUTHOR_STATUS: {sql_packet.status}`.",
                    sql_author_run.result.output_text,
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )

        steward_run = self._run_subagent(
            name="data_steward_subagent",
            runner=self.steward,
            task=_staged_steward_task(
                question=question,
                intent_output=intent_run.result.output_text,
                sql_author_output=sql_author_run.result.output_text,
                compiled_context=compiled_context,
            ),
            events=events,
            event_sink=event_sink,
        )
        subagent_calls += 1
        steward_packet = steward_run.packet
        if steward_packet is None or steward_packet.component != "steward":
            return _finish(
                output_text=_blocked_text(
                    "The Data Steward did not return a parseable `STEWARD_STATUS` packet.",
                    steward_run.result.output_text,
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )
        steward_gate = _definition_gate_for_packet(steward_packet)
        if (
            steward_packet.status == "NEEDS_CLARIFICATION"
            or steward_packet.status == "PASS_WITH_ASSUMPTIONS"
            or steward_gate is not None
        ):
            return _clarification_result(
                packet=steward_gate or steward_packet,
                source="data_steward_subagent",
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                previous_context=_context_for_staged_resume(
                    intent=intent_run.result.output_text,
                    sql_author=sql_author_run.result.output_text,
                    steward=steward_run.result.output_text,
                ),
            )
        if steward_packet.status != "PASS":
            return _finish(
                output_text=_blocked_text(
                    f"Data Steward returned `STEWARD_STATUS: {steward_packet.status}`.",
                    steward_run.result.output_text,
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )

        final_sql = sql_packet.sections.get("FINAL_SQL", "").strip()
        if not final_sql:
            return _finish(
                output_text=_blocked_text("SQL Author packet has no `FINAL_SQL`.", sql_author_run.result.output_text),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )
        value_gate = _value_grounding_gate(sql_packet=sql_packet, sql_author_run=sql_author_run)
        if value_gate is not None:
            _append_event(
                events,
                event_sink,
                _event(
                    "gated_workflow",
                    "value_grounding_blocked",
                    {
                        "reason": value_gate,
                        "sql_preview": _truncate(final_sql, 1600),
                    },
                ),
            )
            return _finish(
                output_text=_blocked_text(value_gate, sql_author_run.result.output_text),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )
        execution = _execute_final_sql(
            tool=self.final_execute_tool,
            sql=final_sql,
            events=events,
            event_sink=event_sink,
        )
        if execution.get("status") != "ok":
            return _finish(
                output_text=_blocked_text("Harness final SQL execution failed.", str(execution)),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls + 1,
                stop_reason="blocked",
            )
        return _finish(
            output_text=_render_staged_final_answer(
                intent_run=intent_run,
                sql_author_run=sql_author_run,
                steward_run=steward_run,
                execution=execution,
            ),
            events=events,
            event_sink=event_sink,
            iterations=subagent_calls + 1,
            stop_reason="final",
        )

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
            _event(
                "gated_workflow",
                "context_compiled",
                _compiled_context_event_payload(payload),
            ),
        )
        return payload

    def _maybe_run_de_and_finish(
        self,
        *,
        question: str,
        analyst_run: SubagentRun,
        steward_run: SubagentRun,
        events: list[PrimitiveTraceEvent],
        subagent_calls: int,
        event_sink: Callable[[PrimitiveTraceEvent], None] | None = None,
    ) -> PrimitiveRunResult:
        analyst_packet = analyst_run.packet
        if (
            not self.enable_data_engineering
            or analyst_packet is None
            or analyst_packet.sections.get("DATA_ENGINEERING_REVIEW", "").strip().lower()
            != "needed"
        ):
            return _finish(
                output_text=_render_final_answer(analyst_run=analyst_run, steward_run=steward_run),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="final",
            )

        de_run = self._run_subagent(
            name="data_engineer_subagent",
            runner=self.data_engineer,
            task=_data_engineering_task(
                question=question,
                analyst_output=analyst_run.result.output_text,
                steward_output=steward_run.result.output_text,
            ),
            events=events,
            event_sink=event_sink,
        )
        subagent_calls += 1
        de_packet = de_run.packet
        if de_packet is None or de_packet.component != "data_engineering":
            return _finish(
                output_text=_blocked_text(
                    "Data Engineering did not return a parseable `DE_STATUS` packet.",
                    de_run.result.output_text,
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )
        if de_packet.status == "UNCHANGED":
            return _finish(
                output_text=_render_final_answer(
                    analyst_run=analyst_run,
                    steward_run=steward_run,
                    de_run=de_run,
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="final",
            )
        if de_packet.status != "OPTIMIZED":
            return _finish(
                output_text=_blocked_text(
                    f"Data Engineering returned `DE_STATUS: {de_packet.status}`.",
                    de_run.result.output_text,
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )

        optimized_analyst = self._run_subagent(
            name="analyst_subagent",
            runner=self.analyst,
            task=_optimized_execution_task(
                question=question,
                analyst_output=analyst_run.result.output_text,
                data_engineering_output=de_run.result.output_text,
            ),
            events=events,
            event_sink=event_sink,
        )
        subagent_calls += 1
        if optimized_analyst.packet is None or optimized_analyst.packet.status != "OK":
            return _finish(
                output_text=_blocked_text(
                    "Analyst did not execute the optimized SQL successfully.",
                    optimized_analyst.result.output_text,
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )
        final_steward = self._run_subagent(
            name="data_steward_subagent",
            runner=self.steward,
            task=_steward_task(question=question, analyst_output=optimized_analyst.result.output_text),
            events=events,
            event_sink=event_sink,
        )
        subagent_calls += 1
        if final_steward.packet is None or final_steward.packet.status not in {
            "PASS",
            "PASS_WITH_ASSUMPTIONS",
        }:
            return _finish(
                output_text=_blocked_text(
                    "Steward did not approve the optimized Analyst packet.",
                    final_steward.result.output_text,
                ),
                events=events,
                event_sink=event_sink,
                iterations=subagent_calls,
                stop_reason="blocked",
            )
        return _finish(
            output_text=_render_final_answer(
                analyst_run=optimized_analyst,
                steward_run=final_steward,
                de_run=de_run,
            ),
            events=events,
            event_sink=event_sink,
            iterations=subagent_calls,
            stop_reason="final",
        )

    def _run_subagent(
        self,
        *,
        name: str,
        runner: PrimitiveAgentRunner,
        task: str,
        events: list[PrimitiveTraceEvent],
        event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    ) -> SubagentRun:
        _append_event(
            events,
            event_sink,
            _event("gated_workflow", "subagent_start", {"name": name, "task": task}),
        )
        result = _run_runner_with_optional_streaming(
            runner=runner,
            task=task,
            event_sink=event_sink,
            events=events,
        )
        packet = parse_status_packet(result.output_text)
        _append_event(
            events,
            event_sink,
            _event(
                "gated_workflow",
                "subagent_done",
                {
                    "name": name,
                    "stop_reason": result.stop_reason,
                    "status": packet.status if packet else None,
                    "component": packet.component if packet else None,
                    "output_preview": _truncate(result.output_text, 2400),
                    "trace_event_count": len(result.trace_events),
                },
            )
        )
        return SubagentRun(name=name, result=result, packet=packet)


def _run_runner_with_optional_streaming(
    *,
    runner: PrimitiveAgentRunner,
    task: str,
    event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    events: list[PrimitiveTraceEvent],
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


def parse_status_packet(text: str) -> StatusPacket | None:
    match = re.search(
        r"^\s*(INTENT_STATUS|SQL_AUTHOR_STATUS|ANALYST_STATUS|STEWARD_STATUS|DE_STATUS)\s*:\s*([A-Z_]+)\b",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not match:
        return None
    key = match.group(1).upper()
    status = match.group(2).upper()
    component = {
        "INTENT_STATUS": "intent",
        "SQL_AUTHOR_STATUS": "sql_author",
        "ANALYST_STATUS": "analyst",
        "STEWARD_STATUS": "steward",
        "DE_STATUS": "data_engineering",
    }[key]
    return StatusPacket(component=component, status=status, sections=_extract_sections(text))


def _extract_sections(text: str) -> dict[str, str]:
    headers = [
        "INTERPRETATION",
        "INTENT_SUMMARY",
        "CLAUSE_BINDINGS",
        "BUSINESS_TERMS",
        "GROUNDED_MAPPINGS",
        "UNRESOLVED_TERMS",
        "CONTEXT_USED",
        "FINAL_SQL",
        "RESULT_PREVIEW",
        "ROW_COUNT",
        "VERIFICATION",
        "DRY_RUN",
        "VALUE_PROBES",
        "NULL_SENSITIVE_CHECKS",
        "GRAIN_JOIN_CHECKS",
        "ASSUMPTIONS",
        "DATA_ENGINEERING_REVIEW",
        "CLARIFICATION_QUESTION",
        "MCQ_OPTIONS",
        "ISSUES",
        "REQUIRED_ANALYST_CORRECTION",
        "EVIDENCE",
        "OPTIMIZED_SQL",
        "CHANGES",
        "EXPLAIN",
        "SEMANTIC_PRESERVATION",
        "REQUIRED_ANALYST_ACTION",
    ]
    pattern = re.compile(
        r"^\s*("
        + "|".join(re.escape(header) for header in headers)
        + r")\s*:\s*$",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1).upper()] = text[start:end].strip().strip("`").strip()
    return sections


def _definition_clarification_for_analyst_packet(packet: StatusPacket) -> StatusPacket | None:
    text = "\n".join(
        [
            packet.sections.get("INTERPRETATION", ""),
            packet.sections.get("ASSUMPTIONS", ""),
            packet.sections.get("VERIFICATION", ""),
        ]
    ).lower()
    unsupported_definition_markers = [
        "no explicit",
        "no learned definition",
        "not defined",
        "standard interpretation",
        "typically means",
        "most likely",
        "likely means",
        "assumed to mean",
        "approximated",
        "proxy for",
    ]
    if not any(marker in text for marker in unsupported_definition_markers):
        return None
    return StatusPacket(
        component="analyst",
        status="NEEDS_CLARIFICATION",
        sections={
            "CLARIFICATION_QUESTION": (
                "A SQL-affecting business term was not defined in the learned context, "
                "and the Analyst used an inferred definition. Please define the term or "
                "confirm the intended SQL logic before I run a final answer."
            ),
            "ISSUES": "The Analyst packet relies on an inferred business definition.",
        },
    )


def _definition_gate_for_packet(packet: StatusPacket) -> StatusPacket | None:
    unresolved = packet.sections.get("UNRESOLVED_TERMS", "")
    assumptions = packet.sections.get("ASSUMPTIONS", "")
    if _has_blocking_section(unresolved):
        return _clarification_packet(
            "A SQL-affecting business term is unresolved. Please define it before I write or execute SQL.",
            details_label="Unresolved details",
            details=unresolved,
        )
    if _has_blocking_section(assumptions):
        return _clarification_packet(
            "The agent introduced a SQL-affecting assumption. Please confirm or correct it before I execute SQL.",
            details_label="Assumption details",
            details=assumptions,
        )
    inferred = _definition_clarification_for_analyst_packet(packet)
    if inferred is not None:
        return inferred
    status_text = "\n".join(packet.sections.values()).lower()
    if re.search(r"\b(status|resolution_status)\s*:\s*(unresolved|conflicting|inferred)\b", status_text):
        return _clarification_packet(
            "A business term was marked unresolved, conflicting, or inferred. Please define it before I execute SQL.",
            details_label="Packet details",
            details="\n\n".join(packet.sections.values()),
        )
    return None


def _clarification_packet(
    question: str,
    *,
    details_label: str | None = None,
    details: str | None = None,
) -> StatusPacket:
    detailed_question = question
    clean_details = _clarification_details(details)
    if clean_details:
        label = details_label or "Details"
        detailed_question = f"{question}\n\n{label}:\n{clean_details}"
    return StatusPacket(
        component="intent",
        status=GateDecision.NEEDS_CLARIFICATION.value.upper(),
        sections={
            "CLARIFICATION_QUESTION": detailed_question,
            "ISSUES": detailed_question,
        },
    )


def _has_blocking_section(text: str) -> bool:
    clean = text.strip().lower().replace("<none>", "none").replace("< none >", "none")
    clean = re.sub(r"^[\s`\-*>]+", "", clean, flags=re.MULTILINE).strip()
    if not clean:
        return False
    if re.match(r"^none\b", clean) and not re.search(
        r"\b(except|but|however|assum|interpret|infer|proxy|approx)\b",
        clean,
    ):
        return False
    non_blocking = {
        "none",
        "none.",
        "no",
        "no.",
        "n/a",
        "not needed",
        "no assumptions",
        "no unresolved terms",
        "empty",
    }
    lines = [line.strip(" -`.\t<>") for line in clean.splitlines() if line.strip(" -`.\t<>")]
    return any(not _is_non_blocking_line(line, non_blocking=non_blocking) for line in lines)


def _is_non_blocking_line(line: str, *, non_blocking: set[str]) -> bool:
    clean = line.strip(" -`.\t<>").lower()
    if clean in non_blocking:
        return True
    if re.match(r"^(none|no unresolved terms|no assumptions)\b", clean) and not re.search(
        r"\b(except|but|however|interpret|infer|inferred|proxy|approx)\b",
        clean,
    ):
        return True
    return False


def _clarification_details(details: str | None, *, max_chars: int = 1600) -> str:
    if not details:
        return ""
    clean = details.strip().strip("`").strip()
    if not clean:
        return ""
    return _truncate(clean, max_chars)


def _initial_analyst_task(
    *,
    question: str,
    clarification: str | None,
    previous_context: str | None,
) -> str:
    parts = [
        "Answer this analytics question with a verified Analyst packet.",
        f"USER_QUESTION:\n{question}",
    ]
    if previous_context:
        parts.append(f"PREVIOUS_CONTEXT:\n{previous_context}")
    if clarification:
        parts.append(f"USER_CLARIFICATION:\n{clarification}")
        parts.append("Use the clarification to resolve the prior ambiguity, then probe, EXPLAIN, execute, and return a fresh Analyst packet.")
    return "\n\n".join(parts)


def _initial_intent_task(
    *,
    question: str,
    clarification: str | None,
    previous_context: str | None,
    compiled_context: dict[str, Any] | None = None,
) -> str:
    parts = [
        "Create an intent packet. Do not write SQL and do not call SQL tools.",
        f"USER_QUESTION:\n{question}",
    ]
    if compiled_context:
        parts.append(f"COMPILED_SEMANTIC_CONTEXT:\n{_compiled_context_text(compiled_context)}")
    if previous_context:
        parts.append(f"PREVIOUS_CONTEXT:\n{previous_context}")
    if clarification:
        parts.append(f"USER_CLARIFICATION:\n{clarification}")
        parts.append("Use the clarification only to resolve prior unresolved terms, then return a fresh intent packet.")
    return "\n\n".join(parts)


def _sql_author_task(
    *,
    question: str,
    intent_output: str,
    compiled_context: dict[str, Any] | None = None,
) -> str:
    parts = [
        "Write a SQL Author packet from this approved intent. Use sql_dry_run only; do not execute final SQL.",
        "The approved intent packet is the executable contract. The original question is provenance only.",
        f"ORIGINAL_USER_QUESTION:\n{question}",
        f"APPROVED_INTENT_PACKET:\n{intent_output}",
    ]
    if compiled_context:
        parts.append(f"COMPILED_SEMANTIC_CONTEXT:\n{_compiled_context_text(compiled_context)}")
    return "\n\n".join(parts)


def _staged_steward_task(
    *,
    question: str,
    intent_output: str,
    sql_author_output: str,
    compiled_context: dict[str, Any] | None = None,
) -> str:
    parts = [
        "Validate this SQL Author packet as a semantic unit test. Use sql_dry_run only; do not execute final SQL.",
        "The approved intent packet is the executable contract. The original question is provenance only.",
        f"ORIGINAL_USER_QUESTION:\n{question}",
        f"APPROVED_INTENT_PACKET:\n{intent_output}",
        f"SQL_AUTHOR_PACKET:\n{sql_author_output}",
        "Return PASS only when the SQL can be executed exactly by the harness with no SQL-affecting assumptions.",
    ]
    if compiled_context:
        parts.append(f"COMPILED_SEMANTIC_CONTEXT:\n{_compiled_context_text(compiled_context)}")
    return "\n\n".join(parts)


def _steward_task(*, question: str, analyst_output: str) -> str:
    return "\n\n".join(
        [
            "Validate this Analyst packet as a semantic/data-quality gate.",
            f"USER_QUESTION:\n{question}",
            f"ANALYST_PACKET:\n{analyst_output}",
            "Return exactly one Steward packet. Use PASS_WITH_ASSUMPTIONS when the SQL is safe but the final answer must disclose SQL-affecting assumptions.",
        ]
    )


def _analyst_correction_task(*, question: str, analyst_output: str, steward_output: str) -> str:
    return "\n\n".join(
        [
            "Rewrite and execute a corrected Analyst packet using Steward feedback.",
            f"USER_QUESTION:\n{question}",
            f"PREVIOUS_ANALYST_PACKET:\n{analyst_output}",
            f"STEWARD_FEEDBACK:\n{steward_output}",
            "Address the exact correction, run EXPLAIN, execute final SQL, and return a fresh Analyst packet.",
        ]
    )


def _data_engineering_task(*, question: str, analyst_output: str, steward_output: str) -> str:
    return "\n\n".join(
        [
            "Review this approved Analyst SQL for cost/readability optimization only.",
            f"USER_QUESTION:\n{question}",
            f"ANALYST_PACKET:\n{analyst_output}",
            f"STEWARD_PACKET:\n{steward_output}",
        ]
    )


def _optimized_execution_task(
    *,
    question: str,
    analyst_output: str,
    data_engineering_output: str,
) -> str:
    return "\n\n".join(
        [
            "Execute the Data Engineering optimized SQL as the final Analyst packet.",
            f"USER_QUESTION:\n{question}",
            f"PREVIOUS_ANALYST_PACKET:\n{analyst_output}",
            f"DATA_ENGINEERING_PACKET:\n{data_engineering_output}",
            "Use the optimized SQL exactly if DE_STATUS is OPTIMIZED. Run EXPLAIN, execute, and return a fresh Analyst packet.",
        ]
    )


def _clarification_result(
    *,
    packet: StatusPacket,
    source: str,
    events: list[PrimitiveTraceEvent],
    event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    iterations: int,
    previous_context: str,
) -> PrimitiveRunResult:
    question = (
        packet.sections.get("CLARIFICATION_QUESTION")
        or packet.sections.get("REQUIRED_ANALYST_CORRECTION")
        or packet.sections.get("ISSUES")
        or "A SQL-affecting clarification is needed before I can answer safely."
    )
    _append_event(
        events,
        event_sink,
        _event(
            "gated_workflow",
            "clarification_required",
            {
                "source": source,
                "question": question,
                "choices": _clarification_choices(packet),
                "previous_context": _truncate(previous_context, 4000),
            },
        )
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
    if not options:
        return []
    choices: list[str] = []
    for line in options.splitlines():
        clean = line.strip()
        if not clean:
            continue
        clean = re.sub(r"^\s*(?:[-*]|\d+[\).])\s*", "", clean).strip()
        if clean:
            choices.append(clean)
    return choices[:4]


def _render_final_answer(
    *,
    analyst_run: SubagentRun,
    steward_run: SubagentRun,
    de_run: SubagentRun | None = None,
) -> str:
    analyst = analyst_run.packet
    steward = steward_run.packet
    assert analyst is not None
    assert steward is not None
    status = "FINAL_STATUS: PASS"
    if steward.status == "PASS_WITH_ASSUMPTIONS":
        status = "FINAL_STATUS: PASS_WITH_ASSUMPTIONS"
    parts = [
        status,
        "",
        "RESULT:",
        analyst.sections.get("RESULT_PREVIEW", "See Analyst packet result preview."),
        "",
        "HOW_I_INTERPRETED_THIS:",
        analyst.sections.get("INTERPRETATION", "See Analyst packet interpretation."),
        "",
        "ASSUMPTIONS:",
        analyst.sections.get("ASSUMPTIONS", "none"),
        "",
        "VERIFIED:",
        steward.sections.get("EVIDENCE", "Steward approved the final Analyst packet."),
    ]
    if de_run is not None:
        parts.extend(["", "DATA_ENGINEERING:", de_run.result.output_text])
    parts.extend(["", "FINAL_SQL:", analyst.sections.get("FINAL_SQL", "")])
    return "\n".join(parts).strip()


def _execute_final_sql(
    *,
    tool: Any,
    sql: str,
    events: list[PrimitiveTraceEvent],
    event_sink: Callable[[PrimitiveTraceEvent], None] | None,
) -> dict[str, Any]:
    args = {"sql": sql}
    _append_event(
        events,
        event_sink,
        _event(
            "gated_workflow",
            "tool_call",
            {
                "stage": HarnessStage.FINAL_EXECUTION.value,
                "name": "execute_sql",
                "args": args,
            },
        ),
    )
    try:
        payload = tool.invoke(args)
    except Exception as exc:  # noqa: BLE001
        payload = {"status": "error", "error": f"{type(exc).__name__}: {exc}", "sql": sql}
    _append_event(
        events,
        event_sink,
        _event(
            "gated_workflow",
            "tool_result",
            {
                "stage": HarnessStage.FINAL_EXECUTION.value,
                "name": "execute_sql",
                "truncated": False,
                "preview": _truncate(str(payload), 2000),
            },
        ),
    )
    return payload if isinstance(payload, dict) else {"status": "error", "payload": payload}


def _render_staged_final_answer(
    *,
    intent_run: SubagentRun,
    sql_author_run: SubagentRun,
    steward_run: SubagentRun,
    execution: dict[str, Any],
) -> str:
    sql_packet = sql_author_run.packet
    steward = steward_run.packet
    assert sql_packet is not None
    assert steward is not None
    parts = [
        "FINAL_STATUS: PASS",
        "",
        "RESULT:",
        _result_table(execution),
        "",
        "HOW_I_INTERPRETED_THIS:",
        sql_packet.sections.get("INTERPRETATION")
        or intent_run.packet.sections.get("INTENT_SUMMARY", "") if intent_run.packet else "",
        "",
        "ASSUMPTIONS:",
        sql_packet.sections.get("ASSUMPTIONS", "none"),
        "",
        "VERIFIED:",
        steward.sections.get("EVIDENCE", "Steward approved the SQL draft."),
        "",
        "FINAL_SQL:",
        sql_packet.sections.get("FINAL_SQL", ""),
    ]
    return "\n".join(parts).strip()


def _result_table(execution: dict[str, Any]) -> str:
    columns = execution.get("columns")
    rows = execution.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return str(execution)
    if not rows:
        return "| " + " | ".join(str(column) for column in columns) + " |\n| " + " | ".join("---" for _ in columns) + " |"
    output = [
        "| " + " | ".join(str(column) for column in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows[:10]:
        if isinstance(row, dict):
            output.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(output)


def _blocked_text(reason: str, evidence: str) -> str:
    return "\n".join(
        [
            "FINAL_STATUS: BLOCKED",
            reason,
            "",
            "Evidence:",
            _truncate(evidence, 4000),
        ]
    ).strip()


def _context_for_resume(*, analyst: str, steward: str) -> str:
    return "\n\n".join(
        [
            "PREVIOUS_ANALYST_PACKET:",
            analyst,
            "PREVIOUS_STEWARD_PACKET:",
            steward,
        ]
    )


def _context_for_staged_resume(
    *,
    intent: str,
    sql_author: str | None = None,
    steward: str | None = None,
) -> str:
    parts = ["PREVIOUS_INTENT_PACKET:", intent]
    if sql_author:
        parts.extend(["PREVIOUS_SQL_AUTHOR_PACKET:", sql_author])
    if steward:
        parts.extend(["PREVIOUS_STEWARD_PACKET:", steward])
    return "\n\n".join(parts)


def _plain_compiled_context(compiled: Any) -> dict[str, Any]:
    if hasattr(compiled, "to_dict"):
        value = compiled.to_dict()
    elif isinstance(compiled, dict):
        value = compiled
    else:
        value = {"value": str(compiled)}
    return value if isinstance(value, dict) else {"value": value}


def _compiled_context_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    retrieval = payload.get("retrieval") or {}
    intent_frame = retrieval.get("intent_frame") if isinstance(retrieval, dict) else {}
    return {
        "status": payload.get("status", "ok"),
        "needs_clarification": bool(payload.get("needs_clarification")),
        "unresolved_terms": payload.get("unresolved_terms", []),
        "candidate_count": len(payload.get("candidate_cards", []) or []),
        "pattern_count": len(payload.get("sql_patterns", []) or []),
        "join_edge_count": len(payload.get("join_edges", []) or []),
        "required_tables": retrieval.get("required_tables", []),
        "intent_frame": intent_frame if isinstance(intent_frame, dict) else {},
    }


def _compiled_context_needs_clarification(payload: dict[str, Any]) -> bool:
    return bool(payload.get("needs_clarification") and payload.get("unresolved_terms"))


def _compiled_context_clarification_packet(payload: dict[str, Any]) -> StatusPacket:
    unresolved = payload.get("unresolved_terms", [])
    terms = []
    for item in unresolved if isinstance(unresolved, list) else []:
        if isinstance(item, dict):
            terms.append(str(item.get("term") or item.get("name") or item.get("id") or item))
        else:
            terms.append(str(item))
    term_text = ", ".join(term for term in terms if term) or "one or more business terms"
    question = (
        "The learned semantic catalog does not have an explicit SQL definition for "
        f"{term_text}. Please define the term or provide the intended SQL logic before I write SQL."
    )
    return StatusPacket(
        component="intent",
        status=GateDecision.NEEDS_CLARIFICATION.value.upper(),
        sections={
            "CLARIFICATION_QUESTION": question,
            "ISSUES": question,
        },
    )


def _compiled_context_text(payload: dict[str, Any], *, max_chars: int = 16000) -> str:
    return _truncate(json.dumps(payload, indent=2, sort_keys=True, default=str), max_chars)


_STRING_LITERAL = r"'(?:''|[^'])*'"
_STRING_PREDICATE_RE = re.compile(
    rf"(?:=|<>|!=|<=|>=|<|>|\blike\b|\bilike\b)\s*{_STRING_LITERAL}"
    rf"|\bin\s*\(\s*{_STRING_LITERAL}",
    flags=re.IGNORECASE,
)


def _value_grounding_gate(
    *,
    sql_packet: StatusPacket,
    sql_author_run: SubagentRun,
) -> str | None:
    final_sql = sql_packet.sections.get("FINAL_SQL", "")
    if not _sql_has_string_predicate(final_sql):
        return None
    if _has_value_grounding_evidence(sql_packet=sql_packet, sql_author_run=sql_author_run):
        return None
    return (
        "SQL contains string predicates but the SQL Author did not provide value-grounding evidence. "
        "Before final execution, categorical/string predicates must be supported by `column_values` "
        "tool evidence or a concrete `VALUE_PROBES` section."
    )


def _sql_has_string_predicate(sql: str) -> bool:
    return bool(_STRING_PREDICATE_RE.search(_strip_sql_comments(sql)))


def _has_value_grounding_evidence(
    *,
    sql_packet: StatusPacket,
    sql_author_run: SubagentRun,
) -> bool:
    if _value_probes_section_has_evidence(sql_packet.sections.get("VALUE_PROBES", "")):
        return True
    for event in sql_author_run.result.trace_events:
        if event.event_type == "tool_call" and event.payload.get("name") == "column_values":
            return True
    return False


def _value_probes_section_has_evidence(text: str) -> bool:
    clean = " ".join(text.strip().lower().replace("<none>", "none").split())
    if not clean:
        return False
    non_evidence = {
        "none",
        "none.",
        "not needed",
        "not run",
        "not required",
        "no value probes",
        "no string predicates",
        "n/a",
    }
    if clean in non_evidence:
        return False
    if re.search(r"\b(failed|missing|not observed|unverified|unclear|skipped)\b", clean):
        return False
    return True


def _strip_sql_comments(sql: str) -> str:
    without_line_comments = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
    return re.sub(r"/\*.*?\*/", "", without_line_comments, flags=re.DOTALL)


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
            "gated_workflow",
            "gated_done",
            {"stop_reason": stop_reason, "output_preview": _truncate(output_text, 2400)},
        )
    )
    return PrimitiveRunResult(
        output_text=output_text,
        trace_events=events,
        iterations=iterations,
        stop_reason=stop_reason,
    )


def _append_event(
    events: list[PrimitiveTraceEvent],
    event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    event: PrimitiveTraceEvent,
) -> None:
    events.append(event)
    if event_sink is not None:
        event_sink(event)


def _event(agent_name: str, event_type: str, payload: dict[str, Any]) -> PrimitiveTraceEvent:
    return PrimitiveTraceEvent(event_type=event_type, agent_name=agent_name, payload=payload)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 18].rstrip() + "\n...[truncated]"
