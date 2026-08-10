"""Learning sub-agents -- the SCALE mechanism. The orchestrator fans out one describe agent per table;
each runs a focused loop (profile every column, verify grain, record descriptions + access recipes)
that writes into the SHARED SemanticModel. Distinct tables -> distinct model keys, so parallel writes
don't race. This is what lets a 24-table estate compile without one agent dropping columns."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from diracdata.harness.loop import run_loop
from diracdata.config import Config
from diracdata.learning.compiler import SemanticModel, build_model_tools
from diracdata.learning.tools import build_learning_tools
from diracdata.runtime.working_memory import WorkingMemory
from diracdata.prompts import load_prompt
from diracdata.utils.streaming import Sink, null_sink

_DEFAULTS = Config()
_DESCRIBE = load_prompt("learn_describe")


def _table_listing(engine: Any, table: str) -> str:
    try:
        rows = engine.query(f'DESCRIBE SELECT * FROM "{table}"', 1000).rows
        typed = [(r[0], r[1]) for r in rows]
    except Exception:  # noqa: BLE001
        typed = [(c, "?") for c in engine.list_columns(table)]
    parts = []
    for c, ty in typed:
        cx = any(k in (ty or "").upper() for k in ("STRUCT", "[]", "MAP", "JSON"))
        parts.append(f"  - {c} {ty}" + ("  <<COMPLEX: profile_column + record access_recipe>>" if cx else ""))
    return f"TABLE {table} ({len(typed)} columns):\n" + "\n".join(parts)


def run_describe_subagent(*, table: str, engine: Any, model: SemanticModel, model_llm: Any,
                          sink: Sink = null_sink, config: Config = _DEFAULTS,
                          max_steps: int = _DEFAULTS.learn_max_steps) -> int:
    """Describe ONE table completely into the shared model. Returns tokens spent."""
    mem = WorkingMemory(goal=f"Describe table {table}")
    system_prompt = _DESCRIBE + "\n\n" + _table_listing(engine, table)
    tools = build_learning_tools(engine=engine) + build_model_tools(model=model)
    task = (f"Describe table '{table}' COMPLETELY: verify its grain with a uniqueness query, then "
            f"profile and describe EVERY column (a complex column MUST carry its access_recipe from "
            f"profile_column). Call describe_table once and describe_column for each column. Then stop.")
    out = run_loop(model=model_llm, tools=tools, system_prompt=system_prompt, memory=mem, sink=sink,
                   max_steps=max_steps, finish_gate=None, config=config, task=task)
    return out.get("tokens", 0)


def build_learning_subagent_tool(*, model: SemanticModel, engine: Any, model_llm: Any,
                                 sink: Sink = null_sink, config: Config = _DEFAULTS,
                                 max_steps: int = _DEFAULTS.learn_max_steps, on_tokens: Any = None) -> list[Any]:
    """`spawn_describe_agents(tables)` -- fan out per-table describe agents in parallel (bounded)."""
    from langchain.tools import tool

    @tool("spawn_describe_agents")
    def spawn_describe_agents(tables: list) -> str:
        """Describe MANY tables in PARALLEL -- one focused sub-agent per table (it profiles + describes
        every column, complex columns with access recipes, verifies grain, into the shared model). Use
        this FIRST on a large schema to describe all tables fast; then record joins + define
        metrics/dimensions + finish yourself. Pass the list of table names to describe."""
        tbls = [t for t in (tables or []) if t]
        if not tbls:
            return "no tables given"
        with ThreadPoolExecutor(max_workers=max(1, config.subagent_max_parallel)) as ex:
            toks = list(ex.map(
                lambda t: run_describe_subagent(table=t, engine=engine, model=model, model_llm=model_llm,
                                                sink=null_sink, config=config, max_steps=max_steps), tbls))
        if on_tokens is not None:
            on_tokens(sum(toks))
        done = [t for t in tbls if t in model.tables]
        sink("learn", "info", f"described {len(done)}/{len(tbls)} tables in parallel")
        return (f"described {len(done)}/{len(tbls)} tables: {', '.join(done)}. "
                f"Now record joins with verified cardinality, define key metrics/dimensions, then finish.")

    return [spawn_describe_agents]
