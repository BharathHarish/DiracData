"""Intent framing -- the tooled front door, run BEFORE any analysis.

v3's framing was blind: it saw only one-line table descriptions, so it guessed. Here framing is a
small agentic phase with the schema-nav tools (get_columns, profile_column), the business glossary
(define), and ask_user. It binds every concept to a real defined term or a schema derivation it
CONFIRMS against the data, and asks the user exactly one question only when two readings would give
materially different numbers. The result is a structured `confirmed_intent` written into
WorkingMemory, so the main loop (and the Phase 3 verify) build to a meaning that was pinned up
front -- no mid-investigation oscillation.
"""

from __future__ import annotations

from typing import Any

from diracdata.agents.loop import run_loop
from diracdata.config import Config
from diracdata.runtime.working_memory import WorkingMemory
from diracdata.prompts import load_prompt
from diracdata.utils.streaming import loads_json

_DEFAULTS = Config()

_FRAMING_TOOLS = {"get_tables", "describe_tables", "get_columns", "describe_columns",
                  "profile_column", "define", "find_examples", "ask_user", "read_transcript"}

_FRAMING_PROMPT = load_prompt("framing")
_FRAMING_TASK = load_prompt("framing_task")


def frame_intent(*, model: Any, tools: list[Any], memory: WorkingMemory, sink: Any,
                 definitions: str = "", recent_turns: str = "", learned: str = "",
                 max_steps: int = _DEFAULTS.framing_max_steps, observe: Any = None) -> int:
    """Run the framing phase; write `confirmed_intent` into `memory`. `definitions` = the workspace's
    index of defined terms/metrics; `recent_turns` = the conversation memory (running summary or prior
    turns) so a follow-up can be resolved into a standalone intent; `learned` = the curated schema
    memory (experiences.md). `observe` captures tool calls for the transcript. Returns tokens spent."""
    subset = [t for t in tools if t.name in _FRAMING_TOOLS]
    task = _FRAMING_TASK
    if recent_turns:
        task += "\n\nRECENT CONVERSATION (most recent last):\n" + recent_turns
    if learned:
        task += "\n\nLEARNED KNOWLEDGE FOR THIS SCHEMA (proven bindings/patterns/gotchas to reuse):\n" + learned
    if definitions:
        task += "\n\n" + definitions + "\n(Call `define` for a term's full SQL; bind to it verbatim.)"
    out = run_loop(model=model, tools=subset, system_prompt=_FRAMING_PROMPT, memory=memory,
                   sink=sink, max_steps=max_steps, stage="framing", task=task, observe=observe)
    parsed = loads_json(out["text"])
    if isinstance(parsed, dict) and (parsed.get("intent") or parsed.get("concepts")):
        memory.confirmed_intent = {
            "intent": str(parsed.get("intent") or "").strip(),
            "concepts": [c for c in (parsed.get("concepts") or []) if isinstance(c, dict)],
            "assumptions": [str(a) for a in (parsed.get("assumptions") or []) if a],
        }
        for a in memory.confirmed_intent["assumptions"]:
            memory.add_fact(f"ASSUMPTION: {a}")
    return out["tokens"]
