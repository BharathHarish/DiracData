"""Subagents -- delegate a self-contained sub-analysis to a fresh analyst with the SAME tools but
its OWN clean context.

A subagent is not a special narrow thing: it is a full v4 analyst loop (same data tools, same
plan + finish gate + independent verify) run on a focused task, with an isolated WorkingMemory. It
returns a DISTILLED result (answer + the result_ids it produced) -- its verbose exploration never
enters the parent's context. The parent merges the sub's result_ids and numbers into its own index
so it can cite and re-verify them at finish.

Why this matters for a data agent (earns its keep on):
- fan-out across entities: "do this for each state/segment" -> one subagent per entity, each
  isolated, each returning a clean number;
- RCA driver trees: one subagent per independent driver, parent reconciles;
- independent cross-check: recompute a number a different way in a fresh context.

Nesting is depth-capped (default 1: the main agent spawns; subagents do not), a runaway backstop.
"""

from __future__ import annotations

import json
from typing import Any

from diracdata.agents.loop import run_loop
from diracdata.config import Config
from diracdata.memory.working_memory import WorkingMemory
from diracdata.tools import build_control_tools, build_tools
from diracdata.agents.verify import FinishGate, make_verifier

_DEFAULTS = Config()


def run_subagent(*, task: str, context: str, model: Any, workspace: Any, engine: Any,
                 result_store: Any, value_cache: Any, confirmed_intent: dict, system_prompt: str,
                 sink: Any, asker: Any, max_steps: int, depth: int, max_depth: int,
                 dialect_note: str = "", config: Config = _DEFAULTS, sources: Any = None) -> dict:
    """Run one subagent to completion and return its distilled result. Shares the engine + result
    store (so result_ids are globally unique and persisted once) but has an isolated context."""
    memory = WorkingMemory(goal=task)
    if confirmed_intent:
        memory.confirmed_intent = confirmed_intent           # inherit the parent's framed meaning
    if context:
        memory.add_fact(context)

    data_tools = build_tools(workspace=workspace, engine=engine, result_store=result_store,
                             memory=memory, value_cache=value_cache, asker=asker, sources=sources,
                             max_rows=config.query_max_rows)
    gate = FinishGate(memory=memory,
                      verifier=make_verifier(model, sink=sink, workspace=workspace,
                                             dialect_note=dialect_note, config=config))
    tools = data_tools + build_control_tools(memory=memory, gate=gate)
    if depth < max_depth:                                    # allow one more level, capped
        tools.append(build_subagent_tool(
            model=model, workspace=workspace, engine=engine, result_store=result_store,
            value_cache=value_cache, parent_memory=memory, system_prompt=system_prompt, sink=sink,
            asker=asker, max_steps=max_steps, depth=depth, max_depth=max_depth, on_tokens=None,
            dialect_note=dialect_note, config=config, sources=sources))

    out = run_loop(model=model, tools=tools, system_prompt=system_prompt, memory=memory,
                   sink=sink, max_steps=max_steps, stage="subagent", finish_gate=gate, config=config)
    return {
        "answer": out["text"],
        "result_ids": list(memory.results.keys()),
        "results": memory.results,
        "seen_numbers": list(memory.seen_numbers),
        "facts": memory.facts,
        "verdict": out.get("verdict"),
        "tokens": out["tokens"] + gate.tokens,
    }


def build_subagent_tool(*, model: Any, workspace: Any, engine: Any, result_store: Any,
                        value_cache: Any, parent_memory: WorkingMemory, system_prompt: str,
                        sink: Any, asker: Any, max_steps: int, depth: int, max_depth: int,
                        on_tokens: Any = None, dialect_note: str = "", config: Config = _DEFAULTS,
                        sources: Any = None) -> Any:
    """The `spawn_subagent` tool. On return it MERGES the sub's result index + seen numbers + a few
    facts into the parent's memory, so the parent can cite and re-verify the sub's numbers."""
    from langchain.tools import tool

    @tool("spawn_subagent")
    def spawn_subagent(task: str, context: str = "") -> str:
        """Delegate a self-contained sub-analysis to a fresh analyst that has the SAME tools but its
        OWN clean context. Use it to REPEAT an analysis across entities (one call per state/segment),
        to quantify one independent DRIVER of an RCA, or to CROSS-CHECK a number a different way. Give
        it a COMPLETE, standalone task; put any bindings/definitions it must honor in `context`. It
        returns a distilled result (answer + result_ids you can cite); its exploration stays out of
        your context. Resolve any user ambiguity via framing BEFORE you fan out."""
        sink("subagent", "info", f"spawning: {task[:config.subagent_task_display]}")
        res = run_subagent(task=task, context=context, model=model, workspace=workspace,
                           engine=engine, result_store=result_store, value_cache=value_cache,
                           confirmed_intent=parent_memory.confirmed_intent, system_prompt=system_prompt,
                           sink=sink, asker=asker, max_steps=max_steps, depth=depth + 1,
                           max_depth=max_depth, dialect_note=dialect_note, config=config, sources=sources)
        parent_memory.results.update(res["results"])          # sub's result_ids become citable
        parent_memory.seen_numbers.update(res["seen_numbers"])  # sub's numbers become faithful
        for fact in res["facts"][:config.subagent_facts_merge]:
            parent_memory.add_fact(fact)
        if on_tokens is not None:
            on_tokens(res["tokens"])
        sink("subagent", "info", f"returned: {len(res['result_ids'])} result(s)")
        return json.dumps({"answer": res["answer"], "result_ids": res["result_ids"]}, default=str)

    return spawn_subagent
