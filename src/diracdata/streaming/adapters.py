"""Provider stream adapters. Each turns one provider's streamed message chunks (LangChain
`AIMessageChunk`s -- already semi-normalized) into canonical `StreamEvent`s. The only real per-provider
variance is WHERE reasoning/thinking lives (which content-block types, which additional_kwargs keys);
tool calls and usage are read from LangChain's normalized fields. Add a provider = subclass + declare
its reasoning markers + register it. Nothing here imports the agent framework.
"""

from __future__ import annotations

from typing import Any

from diracdata.streaming.events import EventType, StreamEvent, Usage


class StreamAdapter:
    """Base adapter. Stateful across a single model stream: tracks whether the answer/reasoning
    channels are open and accumulates tool-call fragments, emitting *_START on first delta and *_END
    (plus USAGE) at finalize."""

    reasoning_types: tuple[str, ...] = ()   # content-block "type" substrings meaning reasoning
    reasoning_keys: tuple[str, ...] = ()    # additional_kwargs keys carrying reasoning

    def __init__(self) -> None:
        self._seq = 0
        self._started = False
        self._answer_open = False
        self._reasoning_open = False
        self._tools: dict[Any, dict] = {}
        self._usage: Usage | None = None

    def _ev(self, etype: EventType, **data: Any) -> StreamEvent:
        self._seq += 1
        return StreamEvent(etype, self._seq, dict(data))

    # ---- public ---------------------------------------------------------------------------
    def translate(self, chunk: Any) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        if not self._started:
            self._started = True
            out.append(self._ev(EventType.RUN_START))
        text, reasoning = self._split_content(chunk)
        if reasoning:
            if not self._reasoning_open:
                self._reasoning_open = True
                out.append(self._ev(EventType.REASONING_START))
            out.append(self._ev(EventType.REASONING_DELTA, text=reasoning))
        if text:
            if not self._answer_open:
                self._answer_open = True
                out.append(self._ev(EventType.ANSWER_START))
            out.append(self._ev(EventType.ANSWER_DELTA, text=text))
        out.extend(self._tool_events(chunk))
        usage = self._usage_of(chunk)
        if usage is not None:
            self._usage = usage
        return out

    def finalize(self) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        if self._reasoning_open:
            self._reasoning_open = False
            out.append(self._ev(EventType.REASONING_END))
        if self._answer_open:
            self._answer_open = False
            out.append(self._ev(EventType.ANSWER_END))
        for entry in self._tools.values():
            out.append(self._ev(EventType.TOOL_CALL_END, id=entry.get("id"),
                                name=entry.get("name"), args=entry.get("args", "")))
        if self._usage is not None:
            out.append(self._ev(EventType.USAGE, usage=self._usage))
        out.append(self._ev(EventType.RUN_END))
        return out

    # ---- content splitting ----------------------------------------------------------------
    def _split_content(self, chunk: Any) -> tuple[str, str]:
        text, reasoning = "", ""
        content = getattr(chunk, "content", "")
        if isinstance(content, str):
            text += content
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    text += str(block)
                    continue
                btype = str(block.get("type", "")).lower()
                if self._is_reasoning(btype, block):
                    reasoning += self._block_text(block)
                elif "tool" not in btype:
                    reasoning_or_text = block.get("text")
                    if reasoning_or_text is not None:
                        text += str(reasoning_or_text)
        ak = getattr(chunk, "additional_kwargs", {}) or {}
        for key in self.reasoning_keys:
            if ak.get(key):
                reasoning += self._dig(ak[key])
        return text, reasoning

    def _is_reasoning(self, btype: str, block: dict) -> bool:
        if "reason" in btype or "think" in btype:
            return True
        if any(m in btype for m in self.reasoning_types):
            return True
        return any(k in block for k in ("reasoning", "thinking", "reasoning_content", "reasoningText"))

    def _block_text(self, block: dict) -> str:
        for key in ("reasoning", "thinking", "reasoning_content", "reasoningText", "text"):
            if key in block:
                return self._dig(block[key])
        return ""

    def _dig(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return "".join(self._dig(v) for v in value.values())
        if isinstance(value, list):
            return "".join(self._dig(v) for v in value)
        return ""

    # ---- tools (LangChain normalizes to tool_call_chunks) ---------------------------------
    def _tool_events(self, chunk: Any) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        for tc in (getattr(chunk, "tool_call_chunks", None) or []):
            idx = tc.get("index")
            key = idx if idx is not None else (tc.get("id") or len(self._tools))
            entry = self._tools.get(key)
            if entry is None:
                entry = {"id": tc.get("id"), "name": tc.get("name") or "", "args": ""}
                self._tools[key] = entry
                out.append(self._ev(EventType.TOOL_CALL_START, id=entry["id"], name=entry["name"]))
            if tc.get("id") and not entry.get("id"):
                entry["id"] = tc["id"]
            if tc.get("name") and not entry.get("name"):
                entry["name"] = tc["name"]
            frag = tc.get("args") or ""
            if frag:
                entry["args"] += frag
                out.append(self._ev(EventType.TOOL_ARGS_DELTA, id=entry["id"], text=frag))
        return out

    def _usage_of(self, chunk: Any) -> Usage | None:
        um = getattr(chunk, "usage_metadata", None)
        if not isinstance(um, dict):
            return None
        details = um.get("output_token_details") or {}
        return Usage(input_tokens=int(um.get("input_tokens") or 0),
                     output_tokens=int(um.get("output_tokens") or 0),
                     reasoning_tokens=int(details.get("reasoning") or 0))


class AnthropicAdapter(StreamAdapter):
    reasoning_types = ("thinking", "reasoning")
    reasoning_keys = ("reasoning_content",)


class OpenAIAdapter(StreamAdapter):
    """OpenAI, LiteLLM, and any OpenAI-compatible gateway (same delta shape)."""
    reasoning_types = ("reasoning",)
    reasoning_keys = ("reasoning", "reasoning_content")


class BedrockConverseAdapter(StreamAdapter):
    reasoning_types = ("reasoning", "reasoning_content")
    reasoning_keys = ("reasoning_content",)


class GenericAdapter(StreamAdapter):
    reasoning_types = ("reasoning", "thinking")
    reasoning_keys = ("reasoning", "reasoning_content", "thinking")


_REGISTRY: dict[str, type[StreamAdapter]] = {
    "anthropic": AnthropicAdapter,
    "openai": OpenAIAdapter,
    "bedrock_converse": BedrockConverseAdapter,
    "bedrock": BedrockConverseAdapter,
    "generic": GenericAdapter,
}


def build_adapter(provider: str | None) -> StreamAdapter:
    """A fresh adapter for the provider (unknown/None -> the generic superset adapter)."""
    return _REGISTRY.get((provider or "").lower(), GenericAdapter)()


def detect_provider(model: Any) -> str:
    """Best-effort provider from the model's class (so callers need not thread it)."""
    name = f"{type(model).__module__}.{type(model).__name__}".lower()
    if "anthropic" in name:
        return "anthropic"
    if "bedrock" in name:
        return "bedrock_converse"
    if "openai" in name:
        return "openai"
    return "generic"
