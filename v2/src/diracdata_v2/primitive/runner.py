"""Small provider-agnostic ReAct loop with explicit trace events."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Generator, Iterable

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class PrimitiveTraceEvent:
    event_type: str
    agent_name: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "agent_name": self.agent_name,
            "created_at": self.created_at,
            "payload": _plain(self.payload),
        }


@dataclass(frozen=True)
class PrimitiveRunResult:
    output_text: str
    trace_events: list[PrimitiveTraceEvent]
    iterations: int
    stop_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_text": self.output_text,
            "iterations": self.iterations,
            "stop_reason": self.stop_reason,
            "trace_events": [event.to_dict() for event in self.trace_events],
        }


class SubAgentInput(BaseModel):
    task: str = Field(description="The specific task for the subagent.")
    context: str | None = Field(default=None, description="Optional compact context for the task.")


class PrimitiveAgentRunner:
    """A minimal ReAct runner over LangChain-compatible chat models and tools."""

    def __init__(
        self,
        *,
        name: str,
        model: Any,
        tools: list[Any],
        system_prompt: str,
        max_iterations: int = 8,
        max_tool_result_chars: int = 12000,
    ) -> None:
        self.name = name
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_iterations = max(1, max_iterations)
        self.max_tool_result_chars = max(1000, max_tool_result_chars)
        self._tools_by_name = {tool.name: tool for tool in tools if hasattr(tool, "name")}

    def run(self, task: str, *, context: str | None = None) -> PrimitiveRunResult:
        stream = self._run_events(task=task, context=context, stream_model=False)
        while True:
            try:
                next(stream)
            except StopIteration as stop:
                return stop.value

    def stream(self, task: str, *, context: str | None = None) -> Iterable[PrimitiveTraceEvent]:
        yield from self._run_events(task=task, context=context, stream_model=True)

    def _run_events(
        self,
        *,
        task: str,
        context: str | None,
        stream_model: bool,
    ) -> Generator[PrimitiveTraceEvent, None, PrimitiveRunResult]:
        events: list[PrimitiveTraceEvent] = []
        output_text = ""
        stop_reason = "max_iterations"
        messages = self._initial_messages(task=task, context=context)
        model = self._bind_tools(self.model)
        yield self._record(
            events,
            self._event(
                "agent_start",
                {
                    "task": task,
                    "has_context": bool(context),
                    "tools": sorted(self._tools_by_name),
                },
            )
        )
        for iteration in range(1, self.max_iterations + 1):
            yield self._record(events, self._event("model_start", {"iteration": iteration}))
            if stream_model:
                response = yield from self._stream_model_response(
                    model=model,
                    messages=messages,
                    iteration=iteration,
                    events=events,
                )
            else:
                response = model.invoke(messages)
            messages.append(response)
            output_text = _message_text(response)
            tool_calls = list(getattr(response, "tool_calls", []) or [])
            yield self._record(
                events,
                self._event(
                    "model_message",
                    {
                        "iteration": iteration,
                        "text_preview": _truncate(output_text, 1600),
                        "tool_calls": [
                            {
                                "id": call.get("id"),
                                "name": call.get("name"),
                                "args": call.get("args", {}),
                            }
                            for call in tool_calls
                        ],
                    },
                )
            )
            if not tool_calls:
                stop_reason = "final"
                yield self._record(
                    events,
                    self._event(
                        "agent_done",
                        {"iteration": iteration, "output_preview": _truncate(output_text, 2400)},
                    )
                )
                return PrimitiveRunResult(
                    output_text=output_text,
                    trace_events=events,
                    iterations=iteration,
                    stop_reason=stop_reason,
                )
            for call in tool_calls:
                tool_message = yield from self._execute_tool_call(
                    call=call,
                    events=events,
                    stream_model=stream_model,
                )
                messages.append(tool_message)
        final_result = yield from self._finalize_after_tool_budget(
            messages=messages,
            events=events,
            stream_model=stream_model,
        )
        if final_result is not None:
            return final_result
        yield self._record(
            events,
            self._event(
                "agent_stopped",
                {"reason": stop_reason, "output_preview": _truncate(output_text, 2400)},
            )
        )
        return PrimitiveRunResult(
            output_text=output_text,
            trace_events=events,
            iterations=self.max_iterations,
            stop_reason=stop_reason,
        )

    def _finalize_after_tool_budget(
        self,
        *,
        messages: list[Any],
        events: list[PrimitiveTraceEvent],
        stream_model: bool,
    ) -> Generator[PrimitiveTraceEvent, None, PrimitiveRunResult | None]:
        if not _last_message_is_tool_message(messages):
            return None

        from langchain_core.messages import HumanMessage

        iteration = self.max_iterations + 1
        messages.append(
            HumanMessage(
                content=(
                    "Tool budget is exhausted. Do not call any more tools. "
                    "Return the required final packet using only the evidence already observed. "
                    "If the evidence is insufficient or a SQL-affecting business term is unresolved, "
                    "return FAIL or NEEDS_CLARIFICATION according to your required response shape."
                )
            )
        )
        yield self._record(
            events,
            self._event("model_start", {"iteration": iteration, "finalization": True}),
        )
        if stream_model:
            response = yield from self._stream_model_response(
                model=self.model,
                messages=messages,
                iteration=iteration,
                events=events,
            )
        else:
            response = self.model.invoke(messages)
        messages.append(response)
        output_text = _message_text(response)
        tool_calls = list(getattr(response, "tool_calls", []) or [])
        yield self._record(
            events,
            self._event(
                "model_message",
                {
                    "iteration": iteration,
                    "finalization": True,
                    "text_preview": _truncate(output_text, 1600),
                    "tool_calls": [
                        {
                            "id": call.get("id"),
                            "name": call.get("name"),
                            "args": call.get("args", {}),
                        }
                        for call in tool_calls
                    ],
                },
            ),
        )
        if tool_calls:
            return None
        yield self._record(
            events,
            self._event(
                "agent_done",
                {"iteration": iteration, "finalization": True, "output_preview": _truncate(output_text, 2400)},
            ),
        )
        return PrimitiveRunResult(
            output_text=output_text,
            trace_events=events,
            iterations=iteration,
            stop_reason="final",
        )

    def _initial_messages(self, *, task: str, context: str | None) -> list[Any]:
        from langchain_core.messages import HumanMessage, SystemMessage

        user_content = task if not context else f"{task}\n\nContext:\n{context}"
        return [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_content),
        ]

    def _bind_tools(self, model: Any) -> Any:
        if self.tools and hasattr(model, "bind_tools"):
            return model.bind_tools(self.tools)
        return model

    def _stream_model_response(
        self,
        *,
        model: Any,
        messages: list[Any],
        iteration: int,
        events: list[PrimitiveTraceEvent],
    ) -> Generator[PrimitiveTraceEvent, None, Any]:
        if not hasattr(model, "stream"):
            return model.invoke(messages)

        from langchain_core.messages import message_chunk_to_message

        accumulated = None
        saw_chunk = False
        try:
            for chunk in model.stream(messages):
                saw_chunk = True
                accumulated = chunk if accumulated is None else accumulated + chunk
                text = _message_text(chunk)
                if text:
                    yield self._record(
                        events,
                        self._event(
                            "model_delta",
                            {
                                "iteration": iteration,
                                "text": text,
                            },
                        ),
                    )
                tool_call_chunks = _tool_call_chunks(chunk)
                if tool_call_chunks:
                    yield self._record(
                        events,
                        self._event(
                            "tool_call_delta",
                            {
                                "iteration": iteration,
                                "chunks": tool_call_chunks,
                            },
                        ),
                    )
        except NotImplementedError:
            if saw_chunk:
                raise
            return model.invoke(messages)

        if accumulated is None:
            return model.invoke(messages)
        return message_chunk_to_message(accumulated)

    def _execute_tool_call(
        self,
        *,
        call: dict[str, Any],
        events: list[PrimitiveTraceEvent],
        stream_model: bool,
    ) -> Generator[PrimitiveTraceEvent, None, Any]:
        from langchain_core.messages import ToolMessage

        name = str(call.get("name") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        call_id = str(call.get("id") or name)
        yield self._record(events, self._event("tool_call", {"id": call_id, "name": name, "args": args}))
        tool = self._tools_by_name.get(name)
        if tool is None:
            payload = {"status": "error", "error": f"Unknown tool: {name}"}
        elif stream_model and hasattr(tool, "_primitive_runner"):
            payload = yield from self._execute_subagent_tool(tool=tool, args=args)
        else:
            try:
                payload = tool.invoke(args)
            except Exception as exc:  # noqa: BLE001
                payload = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        content, truncated = _serialize_tool_result(payload, max_chars=self.max_tool_result_chars)
        yield self._record(
            events,
            self._event(
                "tool_result",
                {
                    "id": call_id,
                    "name": name,
                    "truncated": truncated,
                    "preview": _truncate(content, 2000),
                },
            )
        )
        return ToolMessage(content=content, tool_call_id=call_id, name=name)

    def _execute_subagent_tool(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
    ) -> Generator[PrimitiveTraceEvent, None, dict[str, Any]]:
        runner = getattr(tool, "_primitive_runner")
        result = yield from runner._run_events(
            task=str(args.get("task") or ""),
            context=args.get("context") if isinstance(args.get("context"), str) else None,
            stream_model=True,
        )
        return _subagent_payload(
            runner=runner,
            result=result,
            max_output_chars=int(getattr(tool, "_primitive_max_output_chars", 12000)),
        )

    def _event(self, event_type: str, payload: dict[str, Any]) -> PrimitiveTraceEvent:
        return PrimitiveTraceEvent(event_type=event_type, agent_name=self.name, payload=payload)

    def _record(
        self,
        events: list[PrimitiveTraceEvent],
        event: PrimitiveTraceEvent,
    ) -> PrimitiveTraceEvent:
        events.append(event)
        return event


def build_subagent_tool(
    *,
    name: str,
    description: str,
    runner: PrimitiveAgentRunner,
    max_output_chars: int = 12000,
) -> Any:
    from langchain_core.tools import StructuredTool

    def _run_subagent(task: str, context: str | None = None) -> dict[str, Any]:
        result = runner.run(task=task, context=context)
        return _subagent_payload(runner=runner, result=result, max_output_chars=max_output_chars)

    tool = StructuredTool.from_function(
        func=_run_subagent,
        name=name,
        description=description,
        args_schema=SubAgentInput,
    )
    setattr(tool, "_primitive_runner", runner)
    setattr(tool, "_primitive_max_output_chars", max_output_chars)
    return tool


def _subagent_payload(
    *,
    runner: PrimitiveAgentRunner,
    result: PrimitiveRunResult,
    max_output_chars: int,
) -> dict[str, Any]:
    if result.stop_reason != "final":
        return {
            "status": "stopped",
            "subagent": runner.name,
            "stop_reason": result.stop_reason,
            "iterations": result.iterations,
            "output": "",
            "error": (
                "Subagent stopped before producing a final answer. "
                "Partial text is not valid evidence for downstream SQL or validation."
            ),
            "partial_output_preview": _truncate(result.output_text, 1000),
            "trace_summary": _trace_summary(result.trace_events),
        }
    return {
        "status": "ok",
        "subagent": runner.name,
        "stop_reason": result.stop_reason,
        "iterations": result.iterations,
        "output": _truncate(result.output_text, max_output_chars),
        "trace_summary": _trace_summary(result.trace_events),
    }


def _trace_summary(events: list[PrimitiveTraceEvent]) -> list[dict[str, Any]]:
    summary = []
    for event in events:
        if event.event_type not in {"tool_call", "tool_result", "agent_done", "agent_stopped"}:
            continue
        payload = dict(event.payload)
        if "preview" in payload:
            payload["preview"] = _truncate(str(payload["preview"]), 500)
        summary.append({"event_type": event.event_type, "payload": payload})
    return summary


def _serialize_tool_result(value: Any, *, max_chars: int) -> tuple[str, bool]:
    content = json.dumps(_plain(value), default=str)
    if len(content) <= max_chars:
        return content, False
    return _truncate(content, max_chars), True


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content)


def _tool_call_chunks(message: Any) -> list[dict[str, Any]]:
    chunks = getattr(message, "tool_call_chunks", None) or []
    return [_plain(chunk) for chunk in chunks]


def _last_message_is_tool_message(messages: list[Any]) -> bool:
    if not messages:
        return False
    return messages[-1].__class__.__name__ == "ToolMessage"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 18].rstrip() + "\n...[truncated]"


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    return value
