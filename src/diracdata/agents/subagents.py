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
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from diracdata.agents.loop import run_loop
from diracdata.config import Config
from diracdata.memory.working_memory import WorkingMemory
from diracdata.tools import build_control_tools, build_tools
from diracdata.agents.verify import FinishGate, make_verifier

_DEFAULTS = Config()


def _run_parallel(thunks: list, max_workers: int) -> list:
    """Run zero-arg callables concurrently on a bounded thread pool; results returned in input order.
    Sub-agents are I/O-bound on model calls (the GIL is released during the wait), so threads give real
    parallelism. One thunk is run inline (no pool overhead)."""
    if len(thunks) <= 1:
        return [t() for t in thunks]
    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), len(thunks)))) as pool:
        return list(pool.map(lambda t: t(), thunks))


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
                             max_rows=config.query_max_rows, config=config)
    gate = FinishGate(memory=memory,
                      verifier=make_verifier(model, sink=sink, workspace=workspace,
                                             dialect_note=dialect_note, config=config))
    tools = data_tools + build_control_tools(memory=memory, gate=gate)
    if depth < max_depth:                                    # allow one more level, capped
        tools.extend(build_subagent_tool(
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
    """The delegation tools: `spawn_subagent` (one) and `spawn_subagents` (several, CONCURRENT). Both run
    a full isolated analyst per task and MERGE its result index + seen numbers + a few facts into the
    parent, so the parent can cite and re-verify them at finish."""
    from langchain.tools import tool

    def _run_one(task: str, context: str):
        try:
            return run_subagent(task=task, context=context, model=model, workspace=workspace,
                                engine=engine, result_store=result_store, value_cache=value_cache,
                                confirmed_intent=parent_memory.confirmed_intent, system_prompt=system_prompt,
                                sink=sink, asker=asker, max_steps=max_steps, depth=depth + 1,
                                max_depth=max_depth, dialect_note=dialect_note, config=config, sources=sources)
        except Exception as exc:  # noqa: BLE001 -- a failed branch is fed back, not fatal to the fan-out
            return f"sub-agent failed: {type(exc).__name__}: {exc}"

    def _merge(res: dict) -> None:
        """Fold a sub's result index + numbers + a few facts into the parent (called AFTER the concurrent
        run, sequentially -- so the parent is never mutated by two threads at once)."""
        parent_memory.results.update(res["results"])           # sub's result_ids become citable
        parent_memory.seen_numbers.update(res["seen_numbers"])  # sub's numbers become faithful
        for fact in res["facts"][:config.subagent_facts_merge]:
            parent_memory.add_fact(fact)
        if on_tokens is not None:
            on_tokens(res["tokens"])

    @tool("spawn_subagent")
    def spawn_subagent(task: str, context: str = "") -> str:
        """Delegate ONE self-contained sub-analysis to a fresh analyst with the SAME tools but its OWN
        clean context. Use it to quantify one independent DRIVER of an RCA, repeat an analysis for one
        entity, or CROSS-CHECK a number a different way. Give a COMPLETE, standalone task; put any
        bindings it must honor in `context`. Returns a distilled result (answer + result_ids you can
        cite). For SEVERAL independent branches at once, prefer spawn_subagents. Frame FIRST, then fan."""
        sink("subagent", "info", f"spawning: {task[:config.subagent_task_display]}")
        res = _run_one(task, context)
        if not isinstance(res, dict):
            return res
        _merge(res)
        sink("subagent", "info", f"returned: {len(res['result_ids'])} result(s)")
        return json.dumps({"answer": res["answer"], "result_ids": res["result_ids"]}, default=str)

    @tool("spawn_subagents")
    def spawn_subagents(tasks: list) -> str:
        """Fan out SEVERAL independent sub-analyses AT ONCE and run them CONCURRENTLY -- use this instead
        of calling spawn_subagent one at a time when the branches are independent: RCA drivers, a
        data-sanity/DQ check per source or key column, or the same analysis per entity. Pass a list of
        {"task": "...", "context": "..."}. Each runs as a fresh isolated analyst; you get back ALL their
        distilled results (answer + result_ids to cite). Frame/resolve ambiguity FIRST, then fan out."""
        items = [t for t in (tasks or []) if isinstance(t, dict) and t.get("task")]
        if not items:
            return "spawn_subagents needs a non-empty list of {task, context} objects."
        sink("subagent", "info", f"fanning out {len(items)} sub-agents (<= {config.subagent_max_parallel} at once)")
        results = _run_parallel([(lambda it=it: _run_one(it["task"], it.get("context", ""))) for it in items],
                                config.subagent_max_parallel)
        out = []
        for it, res in zip(items, results):
            label = it["task"][:config.subagent_task_display]
            if isinstance(res, dict):
                _merge(res)
                out.append({"task": label, "answer": res["answer"], "result_ids": res["result_ids"]})
            else:
                out.append({"task": label, "error": str(res)})
        sink("subagent", "info", f"fan-out returned {len(out)} result(s)")
        return json.dumps(out, default=str)

    return [spawn_subagent, spawn_subagents]
