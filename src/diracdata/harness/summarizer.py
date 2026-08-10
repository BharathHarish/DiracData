"""The running-summary agent -- one LLM call that folds the latest turn into the conversation's
summary (agents/../prompts/summarize.md). Agentic: the model decides what survives into the memory
the next turn will see; nothing here is a deterministic rule about what to keep.
"""

from __future__ import annotations

from typing import Any, Callable

from diracdata.prompts import load_prompt
from diracdata.streaming import collect
from diracdata.utils.streaming import Sink, null_sink

_SUMMARIZE_PROMPT = load_prompt("summarize")


def make_summarizer(model: Any, *, sink: Sink = null_sink,
                    config: Any = None) -> Callable[[str, str], tuple[str, int]]:
    """Return summarize(prev_summary, turn_transcript) -> (new_summary, tokens)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    def summarize(prev_summary: str, turn_transcript: str) -> tuple[str, int]:
        human = (f"PREVIOUS SUMMARY:\n{prev_summary or '(none -- this is the first turn)'}\n\n"
                 f"LATEST TURN:\n{turn_transcript}")
        out = collect(model=model, stage="summarize", sink=sink, config=config,
                      messages=[SystemMessage(content=_SUMMARIZE_PROMPT),
                                HumanMessage(content=human)])
        text = (out["text"] or "").strip()
        return (text or prev_summary), out["tokens"]

    return summarize
