"""Supervisor-led primitive workflow.

This mode keeps specialist subagents, but gives a top-level ReAct supervisor
the authority to inspect packets, route corrections, call Data Engineering,
and execute only after validation passes.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from diracdata_v2.primitive.runner import PrimitiveAgentRunner, PrimitiveRunResult, PrimitiveTraceEvent


class SupervisorPrimitiveWorkflow:
    """A reasoning supervisor over intent, SQL, steward, DE, and final execution tools."""

    def __init__(
        self,
        *,
        supervisor: PrimitiveAgentRunner,
        context_compiler: Callable[[str], Any] | None = None,
    ) -> None:
        self.supervisor = supervisor
        self.context_compiler = context_compiler

    def run(
        self,
        question: str,
        *,
        clarification: str | None = None,
        previous_context: str | None = None,
        event_sink: Callable[[PrimitiveTraceEvent], None] | None = None,
    ) -> PrimitiveRunResult:
        events: list[PrimitiveTraceEvent] = []
        _append_event(
            events,
            event_sink,
            _event(
                "supervisor_workflow",
                "supervisor_start",
                {
                    "question": question,
                    "has_clarification": bool(clarification),
                    "has_previous_context": bool(previous_context),
                },
            ),
        )
        compiled_context = self._compile_context(
            question=question,
            events=events,
            event_sink=event_sink,
        )
        if _compiled_context_needs_clarification(compiled_context) and not clarification:
            question_text = _compiled_context_clarification_text(compiled_context)
            _append_event(
                events,
                event_sink,
                _event(
                    "supervisor_workflow",
                    "clarification_required",
                    {
                        "source": "semantic_catalog_compiler",
                        "question": question_text,
                        "choices": _compiled_context_clarification_choices(compiled_context),
                        "previous_context": _truncate(_compiled_context_text(compiled_context), 4000),
                    },
                ),
            )
            _append_event(
                events,
                event_sink,
                _event(
                    "supervisor_workflow",
                    "supervisor_done",
                    {
                        "stop_reason": "needs_clarification",
                        "output_preview": _truncate(question_text, 2400),
                    },
                ),
            )
            return PrimitiveRunResult(
                output_text="CLARIFICATION_REQUIRED\n" + question_text,
                trace_events=events,
                iterations=0,
                stop_reason="needs_clarification",
            )
        task = _supervisor_task(
            question=question,
            clarification=clarification,
            previous_context=previous_context,
            compiled_context=compiled_context,
        )
        result = _run_runner_with_optional_streaming(
            runner=self.supervisor,
            task=task,
            event_sink=event_sink,
            events=events,
        )
        stop_reason = _supervisor_stop_reason(result.output_text)
        if stop_reason == "needs_clarification":
            _append_event(
                events,
                event_sink,
                _event(
                    "supervisor_workflow",
                    "clarification_required",
                    {
                        "question": _clarification_text(result.output_text),
                        "choices": _clarification_choices_from_text(result.output_text),
                        "previous_context": _truncate(result.output_text, 4000),
                    },
                ),
            )
        _append_event(
            events,
            event_sink,
            _event(
                "supervisor_workflow",
                "supervisor_done",
                {
                    "stop_reason": stop_reason,
                    "output_preview": _truncate(result.output_text, 2400),
                },
            ),
        )
        return PrimitiveRunResult(
            output_text=result.output_text,
            trace_events=events,
            iterations=result.iterations,
            stop_reason=stop_reason,
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
            _event("supervisor_workflow", "context_compiled", _compiled_context_event_payload(payload)),
        )
        return payload


def _supervisor_task(
    *,
    question: str,
    clarification: str | None,
    previous_context: str | None,
    compiled_context: dict[str, Any],
) -> str:
    parts = [
        "Supervise this analytics workflow end to end.",
        f"USER_QUESTION:\n{question}",
    ]
    if compiled_context:
        parts.append(f"COMPILED_SEMANTIC_CONTEXT:\n{_compiled_context_text(compiled_context)}")
    if previous_context:
        parts.append(f"PREVIOUS_SUPERVISOR_CONTEXT:\n{previous_context}")
    if clarification:
        parts.append(f"USER_CLARIFICATION:\n{clarification}")
        parts.append(
            "Use the clarification to repair the prior intent or SQL path. Preserve the original question unless the user explicitly changed it."
        )
    return "\n\n".join(parts)


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


def _supervisor_stop_reason(output_text: str) -> str:
    clean = output_text.strip()
    if re.match(r"^(?:#+\s*)?CLARIFICATION_REQUIRED\b", clean, flags=re.IGNORECASE):
        return "needs_clarification"
    if re.search(r"(?im)^\s*(?:#+\s*)?CLARIFICATION_REQUIRED\b", clean):
        return "needs_clarification"
    if re.match(r"^FINAL_STATUS\s*:\s*BLOCKED\b", clean, flags=re.IGNORECASE):
        return "blocked"
    return "final"


def _clarification_text(output_text: str) -> str:
    clean = output_text.strip()
    match = re.search(r"(?im)^\s*(?:#+\s*)?CLARIFICATION_REQUIRED\b.*$", clean)
    if match:
        return clean[match.start() :].strip()
    return clean.split("\n", 1)[1].strip() if "\n" in clean else clean


def _clarification_choices_from_text(output_text: str) -> list[str]:
    match = re.search(
        r"(?ims)^\s*(?:MCQ_OPTIONS|OPTIONS)\s*:\s*(.+?)(?:\n\s*[A-Z_ ]+\s*:|\Z)",
        output_text,
    )
    if not match:
        return _option_label_choices_from_text(output_text)
    choices: list[str] = []
    for line in match.group(1).splitlines():
        clean = line.strip()
        if not clean:
            continue
        clean = re.sub(r"^\s*(?:[-*]|\d+[\).])\s*", "", clean).strip()
        if clean:
            choices.append(clean)
    return choices[:4]


def _option_label_choices_from_text(output_text: str) -> list[str]:
    choices: list[str] = []
    for match in re.finditer(
        r"(?im)^\s*[-*]\s*\*\*Option\s+[A-Z]\s*:\*\*\s*(.+)$",
        output_text,
    ):
        clean = match.group(1).strip()
        if clean:
            choices.append(clean)
    return choices[:4]


def _event(agent_name: str, event_type: str, payload: dict[str, Any]) -> PrimitiveTraceEvent:
    return PrimitiveTraceEvent(event_type=event_type, agent_name=agent_name, payload=payload)


def _append_event(
    events: list[PrimitiveTraceEvent],
    event_sink: Callable[[PrimitiveTraceEvent], None] | None,
    event: PrimitiveTraceEvent,
) -> None:
    events.append(event)
    if event_sink is not None:
        event_sink(event)


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


def _compiled_context_clarification_text(payload: dict[str, Any]) -> str:
    unresolved = payload.get("unresolved_terms") or []
    lines = [
        "Semantic context found a SQL-affecting ambiguity before SQL authoring.",
        "Please choose the intended interpretation.",
    ]
    for item in unresolved:
        if not isinstance(item, dict):
            lines.append(f"- {item}")
            continue
        term = item.get("term") or "Unresolved term"
        reason = item.get("reason") or "No reason provided."
        lines.append(f"- {term}: {reason}")
    return "\n".join(lines)


def _compiled_context_clarification_choices(payload: dict[str, Any]) -> list[str]:
    choices: list[str] = []
    for item in payload.get("unresolved_terms") or []:
        if not isinstance(item, dict):
            continue
        item_choices = item.get("choices")
        if isinstance(item_choices, list):
            choices.extend(str(choice).strip() for choice in item_choices if str(choice).strip())
        reason = str(item.get("reason") or "")
        choices.extend(_lettered_choices_from_text(reason))
    deduped: list[str] = []
    for choice in choices:
        if choice and choice not in deduped:
            deduped.append(choice)
    return deduped[:4]


def _lettered_choices_from_text(text: str) -> list[str]:
    matches = list(re.finditer(r"\(([a-zA-Z])\)\s*", text))
    if not matches:
        return []
    choices: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        choice = re.sub(r"\s*,?\s+or\s*$", "", text[start:end].strip(" ,.;"), flags=re.IGNORECASE)
        if choice:
            choices.append(choice)
    return choices


def _compiled_context_text(payload: dict[str, Any], *, max_chars: int = 16000) -> str:
    return _truncate(json.dumps(payload, indent=2, sort_keys=True, default=str), max_chars)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 18].rstrip() + "\n...[truncated]"
