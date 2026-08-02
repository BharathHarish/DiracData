"""The canonical stream event taxonomy -- the contract every adapter emits and every consumer reads.
Provider-agnostic: an adapter translates one provider's chunks into these; nothing downstream ever
sees a raw provider chunk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class EventType(StrEnum):
    RUN_START = "run_start"
    ANSWER_START = "answer_start"
    ANSWER_DELTA = "answer_delta"
    ANSWER_END = "answer_end"
    REASONING_START = "reasoning_start"
    REASONING_DELTA = "reasoning_delta"
    REASONING_END = "reasoning_end"
    TOOL_CALL_START = "tool_call_start"
    TOOL_ARGS_DELTA = "tool_args_delta"
    TOOL_CALL_END = "tool_call_end"
    USAGE = "usage"
    MODEL_META = "model_meta"
    ERROR = "error"
    RUN_END = "run_end"


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class StreamEvent:
    """One normalized event. `data` carries the payload: {"text": ...} for deltas, {"name","id"} for a
    tool call, {"usage": Usage} for usage, etc. `phase` tags which stage produced it."""
    type: EventType
    seq: int
    data: dict = field(default_factory=dict)
    phase: str | None = None

    @property
    def text(self) -> str:
        return str(self.data.get("text", ""))
