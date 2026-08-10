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
from diracdata.runtime.working_memory import WorkingMemory
from diracdata.prompts import load_prompt
from diracdata.streaming import collect

_DEFAULTS = Config()
_FINALIZE_PROMPT = load_prompt("finalize")

# M5 anti-churn: read-only, idempotent lookups whose EXACT repeat within a turn is pure waste (the
# schema/data does not change mid-turn). Repeating one is short-circuited with feedback instead of
# re-executed. Everything else (run_sql, query_result, plan_update, finish, spawn_*, ask_user, remember,
# combine_results, data_check) may legitimately repeat and is never deduped.
_DEDUP_TOOLS = frozenset({"get_tables", "get_columns", "describe_columns", "profile_column",
                          "join_path", "define", "metric_tree", "find_examples", "data_health"})


def _progress_sig(memory: WorkingMemory) -> tuple:
    """A cheap fingerprint of forward progress: how many results, facts, and VERIFIED plan items exist.
    Unchanged across steps == the agent is churning (re-probing / re-verifying), not advancing."""
    verified = sum(1 for it in getattr(memory.plan, "items", []) if getattr(it, "status", "") == "verified")
    return (len(memory.results), len(memory.facts), verified)


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
    seen_calls: set[str] = set()                 # M5: exact read-only calls already made this turn
    prev_sig, last_progress_step, stalled = _progress_sig(memory), 0, False

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
                call_key = f"{name}:{json.dumps(args, default=str, sort_keys=True)}"
                if tool is None:
                    obs = f"no such tool: {name}"
                elif name in _DEDUP_TOOLS and call_key in seen_calls:
                    obs = (f"SKIPPED (already done): you ran {name} with these EXACT args earlier this turn "
                           f"-- the result is unchanged and already in your context / WORKING MEMORY. Do NOT "
                           f"repeat read-only lookups; reuse the stored result or take a NEW action.")
                else:
                    seen_calls.add(call_key)
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
            # M5 progress sentinel: if tool turns stop advancing (no new result/fact/verified item), the
            # agent is churning (re-probing, re-verifying). Nudge it ONCE to stop and finish; the
            # force-finalize backstop covers the terminal case.
            sig = _progress_sig(memory)
            if sig != prev_sig:
                prev_sig, last_progress_step, stalled = sig, i, False
            elif finish_gate is not None and not stalled and (i - last_progress_step) >= config.no_progress_nudge_steps:
                conversation.append(HumanMessage(content=(
                    "PROGRESS STALL: the last few steps produced no new result or verified plan item -- you are "
                    "repeating work you have already done. Do NOT re-probe or re-verify finished items. Either "
                    "call `finish` NOW with the answer you already have, or take ONE concretely different action.")))
                stalled = True
            if not last:
                continue

        # No actionable tool call, or the last turn: a final-answer attempt.
        text = out["text"]
        if finish_gate is None:
            if last or text.strip():
                return {"text": text, "tokens": tokens, "steps": i + 1, "verdict": None}
            continue
        if last:
            break                                              # budget spent -> synthesise from memory below
        if text.strip():                                       # gate a bare-text final answer (non-last turn)
            msg = finish_gate.submit(text, [])
            if finish_gate.result is not None:
                return _done(finish_gate, tokens, i + 1)
            conversation.append(HumanMessage(content=f"Your answer was NOT accepted. {msg}"))
        else:
            conversation.append(HumanMessage(content="Continue: use a tool, or call finish with your answer."))

    # Budget exhausted. Never return blank if the analyst produced a good answer earlier that a gate
    # rejected (or that got lost in an empty-finish loop) -- surface the best one with a caveat.
    if finish_gate is not None and finish_gate.finalize_best("step budget exhausted mid-analysis"):
        return _done(finish_gate, tokens, max_steps)
    # Nothing composed (e.g. the orchestrator fanned out, got sub-results, but ran out before synthesising
    # a final answer). FORCE a best-effort synthesis from working memory so we never return blank and the
    # accumulated results/sub-agent findings are not wasted.
    if finish_gate is not None and memory.results:
        text, ftok = _compose_from_memory(model, memory, system_prompt, stage, sink, config)
        tokens += ftok
        if text.strip():
            finish_gate.result = {
                "answer": text.strip() + "\n\n> ⚠︎ Composed from working memory after the step budget was "
                          "exhausted -- best-effort synthesis; some requested cuts may be incomplete.",
                "result_ids": list(memory.results.keys()),
                "verdict": {"ok": True, "accepted_with_caveat": True}}
            return _done(finish_gate, tokens, max_steps)
    return {"text": "", "tokens": tokens, "steps": max_steps, "verdict": None}


def _compose_from_memory(model: Any, memory: WorkingMemory, system_prompt: str, stage: str,
                         sink: Sink, config: Config) -> tuple[str, int]:
    """One tool-free model call that writes the best-effort final answer from WORKING MEMORY alone --
    the last-resort synthesiser when the loop exhausts its budget mid-analysis. Guarantees the user gets
    the accumulated results (incl. merged sub-agent findings), never a blank."""
    from langchain_core.messages import HumanMessage, SystemMessage
    try:
        out = collect(model=model, stage=stage, sink=sink, config=config, messages=[
            SystemMessage(content=system_prompt + "\n\n" + _FINALIZE_PROMPT),
            HumanMessage(content=f"ORIGINAL GOAL: {memory.goal}\n\n## WORKING MEMORY\n{memory.render()}")])
        return out["text"], out["tokens"]
    except Exception:  # noqa: BLE001 -- the fallback must never itself crash the turn
        return "", 0


def _done(gate: Any, tokens: int, steps: int) -> dict:
    return {"text": gate.result["answer"], "tokens": tokens, "steps": steps,
            "verdict": gate.result.get("verdict"), "result_ids": gate.result.get("result_ids", [])}
