"""The single agent loop -- one brain, one growing conversation, tools for capability.

This replaces v3's two-brain orchestrator/analyst split. There is no lossy findings handoff: the
same context that ran a tool sees its raw result and writes the answer. Durability comes from the
WorkingMemory block, which is re-injected at the TOP of the prompt every turn (so the goal, intent,
facts, plan, and result index are always fresh) -- the conversation below it carries the narrative.

Phase 1 is the bare loop: explore with tools, then commit a final answer. Framing, the finish gate,
subagents, and summarization are layered on in later phases without changing this spine.
"""

from __future__ import annotations

import json
from typing import Any

from diracdata.utils.streaming import Sink, null_sink, to_ai_message

from diracdata.config import Config
from diracdata.memory.working_memory import WorkingMemory
from diracdata.streaming import collect

_DEFAULTS = Config()


def run_loop(*, model: Any, tools: list[Any], system_prompt: str, memory: WorkingMemory,
             sink: Sink = null_sink, max_steps: int = _DEFAULTS.max_steps, stage: str = "analyst",
             task: str | None = None, finish_gate: Any = None, observe: Any = None,
             config: Config = _DEFAULTS) -> dict:
    """Run the ReAct loop until the finish gate accepts an answer, the model stops calling tools
    with no gate, or the budget is hit. `finish_gate` (if given) has `.submit(answer, result_ids)`
    and a `.result` set on acceptance -- both an explicit `finish` tool call and a bare-text final
    answer are routed through it, so nothing terminates unverified. `task` overrides the opening
    instruction; `observe(name, args, result_str)` is a post-tool hook."""
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    bound = model.bind_tools(tools)
    by_name = {t.name: t for t in tools}
    conversation: list[Any] = [HumanMessage(content=task or f"Answer this question:\n{memory.goal}")]
    tokens = 0

    for i in range(max_steps):
        last = i == max_steps - 1
        sys_text = f"{system_prompt}\n\n## WORKING MEMORY (authoritative)\n{memory.render()}"
        if last:  # final turn: force a finish instead of withdrawing tools (withdrawing them makes some
                  # providers -- e.g. Bedrock Kimi/qwen3 -- degrade tool history to text and stall)
            sys_text += ("\n\n## STEP LIMIT REACHED -- do NOT start new exploration. Give your FINAL "
                         "answer now: call `finish` with your answer + result_ids, or reply in plain text.")
        out = collect(model=bound, messages=[SystemMessage(content=sys_text)] + conversation, stage=stage,
                      sink=sink, config=config)
        tokens += out["tokens"]
        conversation.append(out.get("message") or to_ai_message(out["text"], out["tool_calls"]))

        # On the final turn, only a `finish` call is actionable -- no new exploration.
        calls = [c for c in (out["tool_calls"] or []) if not last or c.get("name") == "finish"]
        if calls:
            for call in calls:
                name, args = call.get("name", ""), call.get("args", {}) or {}
                sink(stage, "tool_call", f"{name}({json.dumps(args, default=str)[:config.tool_call_display]})")
                tool = by_name.get(name)
                if tool is None:
                    obs = f"no such tool: {name}"
                else:
                    try:
                        obs = str(tool.invoke(args))
                    except Exception as exc:  # noqa: BLE001 -- a malformed tool call is feedback, not a crash
                        obs = f"tool '{name}' errored: {type(exc).__name__}: {exc}. Fix the arguments and retry."
                sink(stage, "tool_result", obs[:config.tool_result_display])
                if observe is not None:
                    observe(name, args, obs)
                conversation.append(ToolMessage(content=obs[:config.obs_cap], tool_call_id=call.get("id", name)))
            if finish_gate is not None and finish_gate.result is not None:  # finish tool accepted
                return _done(finish_gate, tokens, i + 1)
            if not last:
                continue

        # No actionable tool call, or the last turn: a final-answer attempt.
        text = out["text"]
        if last or finish_gate is None:
            return {"text": text, "tokens": tokens, "steps": i + 1,
                    "verdict": (finish_gate.result or {}).get("verdict") if finish_gate else None}
        msg = finish_gate.submit(text, [])                     # gate a bare-text finish
        if finish_gate.result is not None:
            return _done(finish_gate, tokens, i + 1)
        conversation.append(HumanMessage(content=f"Your answer was NOT accepted. {msg}"))

    return {"text": "", "tokens": tokens, "steps": max_steps, "verdict": None}


def _done(gate: Any, tokens: int, steps: int) -> dict:
    return {"text": gate.result["answer"], "tokens": tokens, "steps": steps,
            "verdict": gate.result.get("verdict"), "result_ids": gate.result.get("result_ids", [])}
