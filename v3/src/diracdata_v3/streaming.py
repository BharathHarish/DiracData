"""Token streaming for the agentic stages.

Every stage (cognition, author, steward, curator) is its own model call, and each streams
its thinking to a sink so you can watch it work in the CLI. `stream_and_collect` accumulates
the streamed chunks back into a single message -- including tool calls -- so a stage can both
stream live AND be used programmatically.
"""

from __future__ import annotations

from typing import Any, Callable

# sink(stage, kind, text): kind in {"token", "info", "tool_call", "tool_result", "final"}
Sink = Callable[[str, str, str], None]


def null_sink(stage: str, kind: str, text: str) -> None:  # noqa: D401 - no-op default
    return None


def stream_and_collect(*, model: Any, messages: list[Any], stage: str, sink: Sink) -> dict[str, Any]:
    """Stream a model call, emitting content tokens to the sink, and return the collected
    message: {text, tool_calls, tokens}. Falls back to a plain invoke if the model/provider
    does not support streaming, so a stage never breaks for lack of streaming."""

    gathered = None
    streamed_any = False
    try:
        for chunk in model.stream(messages):
            gathered = chunk if gathered is None else gathered + chunk
            delta = _chunk_text(chunk)
            if delta:
                streamed_any = True
                sink(stage, "token", delta)
    except Exception:  # noqa: BLE001 - streaming unsupported/failed -> one clean invoke
        gathered = None
    if gathered is None:
        response = model.invoke(messages)
        text = _message_text(response)
        if text and not streamed_any:
            sink(stage, "token", text)
        return {"text": text, "tool_calls": list(getattr(response, "tool_calls", []) or []),
                "tokens": _tokens(response)}
    return {"text": _message_text(gathered), "tool_calls": list(getattr(gathered, "tool_calls", []) or []),
            "tokens": _tokens(gathered), "message": gathered}


def _chunk_text(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return ""


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [str(i.get("text") or "") if isinstance(i, dict) else str(i) for i in content]
        return "".join(parts)
    return str(content)


def _tokens(message: Any) -> int:
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        return int(usage.get("total_tokens") or 0)
    return 0
