#!/usr/bin/env python3
"""Query agent V6 -- consumes the LEARNED governed semantic_model.yaml (grain + join cardinality +
complex-column access recipes) via AgentV6. The current agent (scripts/ask.py) is untouched.

    PYTHONPATH=src .venv/bin/python scripts/ask_v6.py --schema fintech_complex \
        --model-profile fireworks_deepseek_v4_flash --no-router --question "..."
"""
from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import replace
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diracdata.utils.model_factory import ChatModelFactory  # noqa: E402
from diracdata.utils.duckdb_engine import DuckDBEngine  # noqa: E402
from diracdata.engines import SourceRegistry  # noqa: E402
from diracdata.config import settings_from_env  # noqa: E402
from diracdata.utils.object_store import object_store_from_settings  # noqa: E402
from diracdata.context.fabric import fabric_store_from_settings  # noqa: E402
from diracdata.memory import ExperienceBook  # noqa: E402
from diracdata.context.valuecache import ColumnValueCache  # noqa: E402
from diracdata.context.workspace import Workspace  # noqa: E402
from diracdata.agent_v6 import AgentV6  # noqa: E402
from diracdata.checkpoints.conversation import Conversation  # noqa: E402
from diracdata.runtime.results import ResultStore  # noqa: E402
from diracdata.execution import make_executor  # noqa: E402
from diracdata.streaming import mode_sink  # noqa: E402

# reuse ask.py's exact streaming sink
sys.path.insert(0, str(ROOT / "scripts"))
from ask import _make_sink  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--question", default=None)
    ap.add_argument("--schema", default="fintech_complex")
    ap.add_argument("--model-profile", default="fireworks_deepseek_v4_flash")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--conversation-id", default=None)
    ap.add_argument("--stream-mode", default="updates", choices=["off", "messages", "updates", "all"])
    ap.add_argument("--no-router", action="store_true")
    ap.add_argument("--cold", action="store_true", help="cold start: no example bank (find_examples off)")
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    _base = settings_from_env(args.env_file)
    settings = replace(_base, agent_model_profile=args.model_profile, stream_tokens=True,
                       router_enabled=(False if args.no_router else _base.router_enabled),
                       examples_enabled=(False if args.cold else _base.examples_enabled))
    model = ChatModelFactory(settings=settings).create_chat_model(profile_id=args.model_profile)
    engine = DuckDBEngine.from_settings(settings, args.schema)
    registry = SourceRegistry.of(engine)
    fabric = fabric_store_from_settings(settings)
    obj_store = object_store_from_settings(settings)

    # the V6 difference: load the governed semantic model
    sm = {}
    if fabric.has(args.schema, "semantic_model.yaml"):
        sm = yaml.safe_load(fabric.read_text(args.schema, "semantic_model.yaml")) or {}
    workspace = Workspace.from_store(store=fabric, schema=args.schema)
    value_cache = ColumnValueCache(fabric, args.schema)
    result_store = ResultStore(engine=engine, store=obj_store, schema=args.schema, sources=registry,
                               preview_rows=settings.preview_rows, preview_all_max=settings.preview_all_max,
                               reconciler_memory_limit=settings.reconciler_memory_limit,
                               reconciler_temp_dir=settings.reconciler_temp_dir,
                               reconciler_threads=settings.reconciler_threads,
                               executor=make_executor(settings))
    sink = mode_sink(_make_sink(args.quiet), "off" if args.quiet else args.stream_mode)
    experience_book = ExperienceBook(args.schema, obj_store)
    agent = AgentV6(semantic_model=sm, model=model, workspace=workspace, engine=engine,
                    result_store=result_store, sink=sink, config=settings, max_steps=args.max_steps,
                    value_cache=value_cache, sources=registry, experience_book=experience_book)

    conv_id = args.conversation_id or f"v6-{uuid.uuid4().hex[:8]}"
    conversation = Conversation(conv_id, store=obj_store, config=settings)
    print(f"[v6] schema={args.schema} | semantic_model={'loaded' if sm else 'MISSING'} "
          f"| model={args.model_profile}", file=sys.stderr)

    def ask(q: str) -> None:
        ans = agent.run(q, conversation=conversation)
        print("\n" + "═" * 64)
        print(f"QUESTION: {q}\nANSWER: {ans.answer}")
        print(f"PERFORMANCE: steps={ans.steps} | tokens={ans.tokens} | results={len(ans.memory.results)}")
        print("═" * 64)

    if args.question:
        ask(args.question)
        agent.flush_memory()
        return 0
    print("v6 analyst REPL -- Ctrl-C to exit.", file=sys.stderr)
    try:
        while True:
            q = input("\nQ> ").strip()
            if q:
                ask(q)
    except (EOFError, KeyboardInterrupt):
        print("\nbye", file=sys.stderr)
    finally:
        agent.flush_memory()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
