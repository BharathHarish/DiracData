#!/usr/bin/env python3
"""A/B rig: run the SAME question through V4Agent and V5Agent side by side and compare.

    PYTHONPATH=src .venv/bin/python scripts/ab_v4_v5.py \
        --schema retail_analytics --model-profile openai_gpt_5_4_mini --question "..."

Each agent gets its own fresh ResultStore (so result_ids don't collide) but shares the workspace +
engine. A capturing sink records the tool-call histogram so we can see, per agent: did it fire DQ
(data_health/data_check), the metric tree, sub-agents; how many steps/tokens; and whether the finish
gate accepted cleanly or with a reviewer caveat. Runs v4 then v5 sequentially (safer for a low-TPM
model). Prints a side-by-side table + both answers.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diracdata.agent import V4Agent  # noqa: E402
from diracdata.agent_v5 import V5Agent  # noqa: E402
from diracdata.config import settings_from_env  # noqa: E402
from diracdata.context.fabric import fabric_store_from_settings  # noqa: E402
from diracdata.context.valuecache import ColumnValueCache  # noqa: E402
from diracdata.context.workspace import Workspace  # noqa: E402
from diracdata.engines import SourceRegistry  # noqa: E402
from diracdata.execution import make_executor  # noqa: E402
from diracdata.memory.results import ResultStore  # noqa: E402
from diracdata.utils.duckdb_engine import DuckDBEngine  # noqa: E402
from diracdata.utils.model_factory import ChatModelFactory  # noqa: E402
from diracdata.utils.object_store import object_store_from_settings  # noqa: E402


class CapSink:
    """Records the tool-call histogram + info lines, and (quietly) echoes progress to stderr."""

    def __init__(self, tag: str, quiet: bool):
        self.tag, self.quiet = tag, quiet
        self.tools: dict[str, int] = {}

    def __call__(self, stage: str, kind: str, text: str) -> None:
        if kind == "tool_call":
            name = str(text).split("(", 1)[0].strip() or "?"
            self.tools[name] = self.tools.get(name, 0) + 1
            if not self.quiet:
                print(f"  [{self.tag}] >> {name}", file=sys.stderr, flush=True)
        elif kind == "info" and not self.quiet:
            print(f"  [{self.tag}] [{stage}] {text}", file=sys.stderr, flush=True)


def _run(AgentClass, tag, *, model, workspace, engine, registry, settings, obj_store, value_cache,
         question, quiet):
    sink = CapSink(tag, quiet)
    rs = ResultStore(engine=engine, store=obj_store, schema=engine.name, sources=registry,
                     preview_rows=settings.preview_rows, preview_all_max=settings.preview_all_max,
                     reconciler_memory_limit=settings.reconciler_memory_limit,
                     reconciler_temp_dir=settings.reconciler_temp_dir,
                     reconciler_threads=settings.reconciler_threads, executor=make_executor(settings))
    agent = AgentClass(model=model, workspace=workspace, engine=engine, result_store=rs, sink=sink,
                       config=settings, value_cache=value_cache, asker=lambda q: "", sources=registry)
    t0 = time.perf_counter()
    try:
        ans = agent.run(question)
        err = None
    except Exception as exc:  # noqa: BLE001 -- a crash IS a data point (e.g. a 429 TPM limit)
        return {"tag": tag, "error": f"{type(exc).__name__}: {str(exc)[:120]}", "sec": round(time.perf_counter() - t0, 1),
                "tools": sink.tools}
    caveat = bool((ans.verdict or {}).get("accepted_with_caveat"))
    return {"tag": tag, "error": err, "answer": ans.answer, "steps": ans.steps, "tokens": ans.tokens,
            "caveat": caveat, "tools": sink.tools, "sec": round(time.perf_counter() - t0, 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--question", required=True)
    ap.add_argument("--schema", default="retail_analytics")
    ap.add_argument("--model-profile", default="openai_gpt_5_4_mini")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    settings = replace(settings_from_env(args.env_file), agent_model_profile=args.model_profile,
                       stream_tokens=False)
    model = ChatModelFactory(settings=settings).create_chat_model(profile_id=args.model_profile)
    engine = DuckDBEngine(data_root=Path(args.data_root), schema_name=args.schema)
    registry = SourceRegistry.of(engine)
    fabric = fabric_store_from_settings(settings)
    obj_store = object_store_from_settings(settings)
    gold = ROOT / "data" / "evals" / "Goldset_retail_queries.csv"
    history = ROOT / "data" / "query_history" / f"{args.schema}_query_history.csv"
    workspace = Workspace.from_store(store=fabric, schema=args.schema,
                                     gold_pairs_path=gold if gold.exists() else None,
                                     query_history_path=history if history.exists() else None)
    value_cache = ColumnValueCache(fabric, args.schema)

    print(f"\nA/B on {args.schema} @ {args.model_profile}\nQ: {args.question}\n" + "=" * 72)
    common = dict(model=model, workspace=workspace, engine=engine, registry=registry, settings=settings,
                  obj_store=obj_store, value_cache=value_cache, question=args.question, quiet=args.quiet)
    print("--- running V4 ---", file=sys.stderr)
    v4 = _run(V4Agent, "v4", **common)
    print("--- running V5 ---", file=sys.stderr)
    v5 = _run(V5Agent, "v5", **common)

    def fired(r, *names):
        return "yes" if any(r["tools"].get(n) for n in names) else "no"

    rows = [
        ("steps",            str(v4.get("steps", "-")),            str(v5.get("steps", "-"))),
        ("tokens",           f"{v4.get('tokens', 0):,}",           f"{v5.get('tokens', 0):,}"),
        ("wall (s)",         str(v4.get("sec")),                   str(v5.get("sec"))),
        ("DQ fired",         fired(v4, "data_health", "data_check"), fired(v5, "data_health", "data_check")),
        ("metric_tree",      fired(v4, "metric_tree"),             fired(v5, "metric_tree")),
        ("sub-agents",       fired(v4, "spawn_subagents", "spawn_subagent"), fired(v5, "spawn_subagents", "spawn_subagent")),
        ("caveat/crash",     v4.get("error") or ("caveat" if v4.get("caveat") else "clean"),
                             v5.get("error") or ("caveat" if v5.get("caveat") else "clean")),
    ]
    w = max(len(r[0]) for r in rows)
    print(f"\n{'dimension':<{w}} | {'V4':<28} | {'V5':<28}")
    print("-" * (w + 62))
    for name, a, b in rows:
        print(f"{name:<{w}} | {str(a):<28} | {str(b):<28}")
    print("\n--- V4 answer ---\n" + (v4.get("answer") or v4.get("error") or "(none)")[:900])
    print("\n--- V5 answer ---\n" + (v5.get("answer") or v5.get("error") or "(none)")[:900])
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
