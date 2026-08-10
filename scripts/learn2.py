#!/usr/bin/env python3
"""Agentic learning agent: compile a governed SEMANTIC MODEL for a schema, with full streaming.

    PYTHONPATH=src .venv/bin/python scripts/learn2.py --schema fintech_complex \
        --model-profile fireworks_deepseek_v4_flash --stream-mode updates

Reads the schema (incl. complex types), the blessed metrics.yaml, and the query history as context;
builds the model via write tools reviewed by an agentic fabric reviewer; writes semantic_model.yaml
+ coverage_report.json to the object store. Streams the agent's thinking + tool use like ask.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diracdata.models import ChatModelFactory  # noqa: E402
from diracdata.utils.duckdb_engine import DuckDBEngine  # noqa: E402
from diracdata.config import settings_from_env  # noqa: E402
from diracdata.context.fabric import fabric_store_from_settings  # noqa: E402
from diracdata.learning.agent2 import LearningCompiler  # noqa: E402
from diracdata.streaming import mode_sink  # noqa: E402


def _make_sink(quiet: bool):
    state = {"stage": None}

    def sink(stage: str, kind: str, text: str) -> None:
        if quiet:
            return
        if kind == "token":
            if state["stage"] != stage:
                print(f"\n\033[1m──── {stage.upper()} ────\033[0m", file=sys.stderr, flush=True)
                state["stage"] = stage
            print(text, end="", file=sys.stderr, flush=True)
        elif kind == "tool_call":
            print(f"\n  \033[36m>> {text}\033[0m", file=sys.stderr, flush=True); state["stage"] = None
        elif kind == "tool_result":
            print(f"  \033[2m<< {text}\033[0m", file=sys.stderr, flush=True)
        elif kind == "reasoning":
            if state["stage"] != f"reasoning:{stage}":
                print(f"\n\033[2m····· {stage} thinking ·····\033[0m", file=sys.stderr, flush=True)
                state["stage"] = f"reasoning:{stage}"
            print(f"\033[2m{text}\033[0m", end="", file=sys.stderr, flush=True)
        elif kind == "usage":
            print(f"\n\033[2m[{stage} usage] {text}\033[0m", file=sys.stderr, flush=True); state["stage"] = None
        elif kind == "info":
            print(f"\n\033[2m[{stage}] {text}\033[0m", file=sys.stderr, flush=True); state["stage"] = None

    return sink


def _context(fab, schema: str) -> str:
    """Clean handling of the input artifacts: the blessed metrics.yaml + a sample of query history."""
    parts = []
    for name in ("semantic_layer.yaml", "semantic_layer.yml"):
        if fab.has(schema, name):
            parts.append("BLESSED METRICS / DIMENSIONS (metrics.yaml -- align to these, do not contradict):\n"
                         + (fab.read_text(schema, name) or "")[:6000])
            break
    hist = ROOT / "data" / "query_history" / f"{schema}_query_history.csv"
    if hist.exists():
        lines = hist.read_text(errors="ignore").splitlines()[1:60]
        parts.append("HOW ANALYSTS REALLY QUERY (sample of query history -- mine for real joins, "
                     "grains, filters, metrics):\n" + "\n".join(lines)[:4000])
    return "\n\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--model-profile", default="fireworks_deepseek_v4_flash")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--stream-mode", default="updates", choices=["off", "messages", "updates", "all"])
    ap.add_argument("--subagents", dest="subagents", action="store_true", default=True,
                    help="fan out per-table describe sub-agents (scale; default on)")
    ap.add_argument("--no-subagents", dest="subagents", action="store_false",
                    help="single-agent compile (small schemas / debugging)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    settings = replace(settings_from_env(args.env_file), agent_model_profile=args.model_profile,
                       stream_tokens=True)
    model = ChatModelFactory(settings=settings).create_chat_model(profile_id=args.model_profile)
    engine = DuckDBEngine.from_settings(settings, args.schema)
    fab = fabric_store_from_settings(settings)
    sink = mode_sink(_make_sink(args.quiet), "off" if args.quiet else args.stream_mode)

    print(f"[learn2] compiling semantic model for {args.schema} | {len(engine.list_tables())} tables "
          f"| model={args.model_profile}", file=sys.stderr)
    compiler = LearningCompiler(engine=engine, model=model, sink=sink, config=settings,
                                max_steps=args.max_steps, subagents=args.subagents)
    context = _context(fab, args.schema)
    sm, out = compiler.compile(args.schema, context=context)

    cov = sm.coverage({t: engine.list_columns(t) for t in engine.list_tables()})
    # PRIMARY output: the artifacts the base agent consumes ON-DEMAND (metadata_descriptions.json ->
    # describe_columns, value_domains.json). Complex-column ACCESS RECIPES are folded into the long
    # descriptions so the analyst gets nested-access syntax through a channel it already uses. The
    # semantic_model.yaml is kept as the source-of-record (governance), but is not the delivery vehicle.
    meta = sm.to_metadata_descriptions()
    cur_meta = fab.get(args.schema, "metadata_descriptions.json") or {}
    for t, cols in meta["columns"].items():
        cur_meta.setdefault("columns", {}).setdefault(t, {}).update(cols)
    cur_meta.setdefault("tables", {}).update(meta["tables"])
    fab.put(args.schema, "metadata_descriptions.json", cur_meta)
    domains = sm.to_value_domains()
    if domains:
        cur_dom = fab.get(args.schema, "value_domains.json") or {}
        for t, cols in domains.items():
            cur_dom.setdefault(t, {}).update(cols)
        fab.put(args.schema, "value_domains.json", cur_dom)
    fab._store.write_text(fab._fabric_key(args.schema, "semantic_model.yaml"), sm.to_yaml(),
                          content_type="text/yaml")
    fab.put(args.schema, "coverage_report.json", cov)
    n_recipes = sum(1 for cols in meta["columns"].values() for d in cols.values()
                    if "NESTED/COMPLEX" in (d.get("long_description") or ""))
    print("\n" + "═" * 64)
    print(f"COMPILED {args.schema}: metadata_descriptions.json ({sum(len(c) for c in meta['columns'].values())} "
          f"cols, {n_recipes} complex recipes) + value_domains.json + semantic_model.yaml (record)")
    print(f"COVERAGE: {json.dumps(cov, default=str)}")
    print(f"PERFORMANCE: steps={out.get('steps')} | tokens={out.get('tokens')}")
    print("═" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
