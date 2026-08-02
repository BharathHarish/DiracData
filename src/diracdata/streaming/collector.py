"""Collector -- drives one model stream through an adapter and folds the canonical events into a
clean result: the answer with reasoning REMOVED (reasoning is its own field), tool calls, and usage.
Falls back to a single buffered `invoke` when the model/provider can't stream (same guarantee as the
legacy `stream_and_collect`). Preserves answer-token streaming to the sink; reasoning is never streamed
into the answer display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from diracdata.streaming.adapters import build_adapter, detect_provider
from diracdata.streaming.events import EventType, StreamEvent, Usage
from diracdata.utils.streaming import Sink, loads_json, null_sink, stream_and_collect


@dataclass
class CollectedResult:
    answer: str                 # the final answer, reasoning stripped out
    reasoning: str              # the model's thinking, kept separate (never in `answer`)
    tool_calls: list[dict]
    usage: Usage
    events: list[StreamEvent] = field(default_factory=list)
    message: Any = None         # the provider message, for the LangChain tool loop to append

    @property
    def text(self) -> str:      # drop-in with the legacy {text, tool_calls, tokens} shape
        return self.answer

    @property
    def tokens(self) -> int:
        return self.usage.total_tokens


class Collector:
    def __init__(self, config: Any = None) -> None:
        self._config = config

    def run(self, *, model: Any, messages: list[Any], stage: str = "analyst",
            provider: str | None = None, sink: Sink = null_sink) -> CollectedResult:
        adapter = build_adapter(provider or detect_provider(model))
        events: list[StreamEvent] = []

        def handle(new_events: list[StreamEvent]) -> None:
            for ev in new_events:
                events.append(StreamEvent(ev.type, ev.seq, ev.data, stage))
                # Emit each channel as its own sink `kind`; the mode filter (mode_sink) decides what
                # is actually shown -- so `messages` sees only the answer, `all` also sees reasoning.
                if ev.type == EventType.ANSWER_DELTA and ev.text:
                    sink(stage, "token", ev.text)
                elif ev.type == EventType.REASONING_DELTA and ev.text:
                    sink(stage, "reasoning", ev.text)
                elif ev.type == EventType.USAGE:
                    u = ev.data.get("usage")
                    if u is not None:
                        sink(stage, "usage", f"in={u.input_tokens} out={u.output_tokens} "
                                             f"reasoning={u.reasoning_tokens}")

        gathered = None
        try:
            for chunk in model.stream(messages):
                gathered = chunk if gathered is None else gathered + chunk
                handle(adapter.translate(chunk))
        except Exception:  # noqa: BLE001 -- streaming unsupported/failed -> one clean invoke
            gathered = None
        if gathered is None:
            response = model.invoke(messages)
            handle(adapter.translate(response))
            gathered = response
        handle(adapter.finalize())
        return self._fold(events, gathered)

    def _fold(self, events: list[StreamEvent], gathered: Any) -> CollectedResult:
        answer = "".join(e.text for e in events if e.type == EventType.ANSWER_DELTA)
        reasoning = "".join(e.text for e in events if e.type == EventType.REASONING_DELTA)
        usage = next((e.data["usage"] for e in reversed(events) if e.type == EventType.USAGE), Usage())
        # Tool calls: prefer LangChain's assembled list on the gathered message (most reliable);
        # fall back to reconstructing from TOOL_CALL_END events.
        tool_calls = list(getattr(gathered, "tool_calls", []) or [])
        if not tool_calls:
            tool_calls = [{"name": e.data.get("name"), "args": loads_json(e.data.get("args") or "{}"),
                           "id": e.data.get("id")}
                          for e in events if e.type == EventType.TOOL_CALL_END and e.data.get("name")]
        return CollectedResult(answer=answer, reasoning=reasoning, tool_calls=tool_calls,
                               usage=usage, events=events, message=gathered)


def collect(*, model: Any, messages: list[Any], stage: str = "analyst", sink: Sink = null_sink,
            config: Any = None, provider: str | None = None) -> dict:
    """Dispatch a model call and return the legacy `{text, tool_calls, tokens, message}` dict (drop-in
    for `stream_and_collect`), plus `reasoning`. Uses the event-envelope Collector when
    `config.stream_envelope_enabled` is on; otherwise the legacy path -- so this is a no-op by default.
    """
    if config is not None and getattr(config, "stream_envelope_enabled", False):
        r = Collector(config).run(model=model, messages=messages, stage=stage, sink=sink, provider=provider)
        return {"text": r.answer, "tool_calls": r.tool_calls, "tokens": r.tokens,
                "message": r.message, "reasoning": r.reasoning}
    return stream_and_collect(model=model, messages=messages, stage=stage, sink=sink)
