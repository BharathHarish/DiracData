"""Stream modes -- a user-selectable filter over what reaches the display sink. The same canonical
event stream drives every mode; a mode just decides which sink `kind`s are shown. `mode_sink` wraps
any base sink so a single wrap point (the CLI) governs BOTH model-output kinds (token/reasoning/usage
from the Collector) and loop kinds (tool_call/tool_result/info).
"""

from __future__ import annotations

from enum import StrEnum

from diracdata.utils.streaming import Sink


class StreamMode(StrEnum):
    OFF = "off"              # nothing live; the final answer is still printed by the caller
    MESSAGES = "messages"    # the answer stream + tool activity (default end-user view)
    UPDATES = "updates"      # coarse progress (phase + tool start/end), no token spam
    ALL = "all"             # everything: tokens, reasoning, tool i/o, phase info, usage


_ALLOWED: dict[StreamMode, frozenset[str]] = {
    StreamMode.OFF: frozenset(),
    StreamMode.MESSAGES: frozenset({"token", "tool_call", "tool_result"}),
    StreamMode.UPDATES: frozenset({"info", "tool_call", "tool_result"}),
    StreamMode.ALL: frozenset({"token", "reasoning", "tool_call", "tool_result", "info", "usage", "final"}),
}


def coerce_mode(value: object) -> StreamMode:
    """Parse a mode string; anything unrecognized falls back to MESSAGES."""
    try:
        return StreamMode(str(value).lower())
    except ValueError:
        return StreamMode.MESSAGES


def allowed_kinds(mode: object) -> frozenset[str]:
    return _ALLOWED[coerce_mode(mode)]


def mode_sink(base: Sink, mode: object) -> Sink:
    """Wrap `base` so only the sink kinds allowed by `mode` pass through."""
    allowed = allowed_kinds(mode)

    def sink(stage: str, kind: str, text: str) -> None:
        if kind in allowed:
            base(stage, kind, text)

    return sink
