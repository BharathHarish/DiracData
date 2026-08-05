"""spawn_metric_rca -- the main agent delegates a metric root-cause to a SPECIALIST sub-agent that has
the RCA tools (metric_series / attribute_change / rank_movers) on top of the base analyst tools, plus a
lean specialist skill. The specialist walks the driver tree deterministically and returns a reconciled
decomposition; the main agent then just sanity-checks + narrates. Keeps the generalist loop lean."""

from __future__ import annotations

import json
from typing import Any

from diracdata.agents.subagents import run_subagent
from diracdata.config import Config
from diracdata.prompts import load_prompt
from diracdata.rca.tools import build_rca_tools

_DEFAULTS = Config()
_SPECIALIST_PROMPT = load_prompt("analyst_core") + "\n\n" + load_prompt("rca_specialist")


def build_rca_delegate(*, model: Any, workspace: Any, engine: Any, result_store: Any, value_cache: Any,
                       parent_memory: Any, sink: Any, asker: Any, max_steps: int,
                       dialect_note: str = "", config: Config = _DEFAULTS, sources: Any = None,
                       on_tokens: Any = None) -> list[Any]:
    from langchain.tools import tool

    system_prompt = _SPECIALIST_PROMPT + (("\n\n" + dialect_note) if dialect_note else "")
    rca_tools = build_rca_tools(workspace=workspace, engine=engine, config=config)

    @tool("spawn_metric_rca")
    def spawn_metric_rca(metric: str, period_a: Any, period_b: Any, dimensions: list | None = None) -> str:
        """Delegate a metric root-cause to the RCA specialist. It walks the metric's DRIVER TREE, computes
        each driver's EXACT contribution to the change (reconciled), and ranks the movers across the given
        DIMENSIONS -- returning a structured decomposition you then sanity-check and narrate. Pass the
        DEFINED metric name, the two periods (e.g. 2001, 2002), and the dimensions to attribute across.
        Prefer this over hand-walking the tree yourself."""
        dims = [d for d in (dimensions or []) if d]
        task = (f"Root-cause the change in metric '{metric}' from {period_a} to {period_b}"
                + (f", attributed across these dimensions: {', '.join(dims)}." if dims else ".")
                + " Walk the driver tree and return the reconciled driver decomposition + the top movers per dimension.")
        # Hand the specialist the parent's data-health ledger so it does not re-probe.
        dq = "\n".join(f for f in (parent_memory.facts or []) if str(f).startswith("data_health["))
        res = run_subagent(task=task, context=(f"Data-health already checked:\n{dq}" if dq else ""),
                           model=model, workspace=workspace, engine=engine, result_store=result_store,
                           value_cache=value_cache, confirmed_intent=parent_memory.confirmed_intent,
                           system_prompt=system_prompt, sink=sink, asker=asker, max_steps=max_steps,
                           depth=1, max_depth=1, dialect_note=dialect_note, config=config, sources=sources,
                           extra_tools=rca_tools)
        parent_memory.results.update(res["results"])
        parent_memory.seen_numbers.update(res["seen_numbers"])
        for fact in res["facts"][:config.subagent_facts_merge]:
            parent_memory.add_fact(fact)
        if on_tokens is not None:
            on_tokens(res["tokens"])
        sink("subagent", "info", f"metric-rca specialist returned {len(res['result_ids'])} result(s)")
        return json.dumps({"answer": res["answer"], "result_ids": res["result_ids"]}, default=str)

    return [spawn_metric_rca]
