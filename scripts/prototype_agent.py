#!/usr/bin/env python3
"""PROTOTYPE: wire one agent from the modular packages and run it against `ecommerce` -- a smoke test
that every package imports and composes. Each package is imported from its NEW home and its object is
built, then the agent answers one question end-to-end (object-store-native data via the engine).

    PYTHONPATH=src .venv/bin/python scripts/prototype_agent.py --question "How many orders were placed?"
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# --- one import per package, from its canonical home ---------------------------------------------
from diracdata.config import settings_from_env               # config
from diracdata.stores import store_from_settings             # stores  (MinIO/S3)
from diracdata.engines import DuckDBEngine, SourceRegistry   # engines (object-store-native)
from diracdata.models import chat_model                      # models  (factory, profile via ENV)
from diracdata.context.fabric import fabric_store_from_settings   # context (learned artifacts)
from diracdata.context.workspace import Workspace
from diracdata.context.valuecache import ColumnValueCache
from diracdata.runtime.results import ResultStore            # runtime (per-turn state)
from diracdata.checkpoints import Conversation               # checkpoints (continuity)
from diracdata.memory import ExperienceBook                  # memory (experiential, optional)
from diracdata.execution import make_executor
from diracdata.streaming import mode_sink
from diracdata.agent import Agent                            # the harness spine


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="ecommerce")
    ap.add_argument("--question", default="How many orders were placed in total?")
    ap.add_argument("--model-profile", default=None, help="None -> DIRACDATA_AGENT_MODEL_PROFILE")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    args = ap.parse_args()

    settings = settings_from_env(args.env_file)
    used: list[str] = []

    obj_store = store_from_settings(settings);                       used.append(f"stores       -> {type(obj_store).__name__}")
    engine = DuckDBEngine.from_settings(settings, args.schema);      used.append(f"engines      -> {type(engine).__name__} (lake_source={settings.lake_source}, {len(engine.list_tables())} tables)")
    registry = SourceRegistry.of(engine)
    model = chat_model(args.model_profile, settings=settings);       used.append(f"models       -> {type(model).__name__} (profile={args.model_profile or settings.agent_model_profile})")
    fabric = fabric_store_from_settings(settings);                   used.append(f"context      -> FabricStore + Workspace + ColumnValueCache")
    workspace = Workspace.from_store(store=fabric, schema=args.schema)
    value_cache = ColumnValueCache(fabric, args.schema)
    result_store = ResultStore(engine=engine, store=obj_store, schema=args.schema, sources=registry,
                               preview_rows=settings.preview_rows, preview_all_max=settings.preview_all_max,
                               reconciler_memory_limit=settings.reconciler_memory_limit,
                               reconciler_temp_dir=settings.reconciler_temp_dir,
                               reconciler_threads=settings.reconciler_threads,
                               executor=make_executor(settings));   used.append(f"runtime      -> ResultStore + WorkingMemory")
    conversation = Conversation(f"proto-{uuid.uuid4().hex[:8]}", store=obj_store, config=settings)
    used.append("checkpoints  -> Conversation (object-store backed)")
    experience_book = ExperienceBook(args.schema, obj_store);        used.append("memory       -> ExperienceBook (experiential)")
    sink = mode_sink(lambda *a: None, "off")

    agent = Agent(model=model, workspace=workspace, engine=engine, result_store=result_store,
                  sink=sink, config=settings, value_cache=value_cache, sources=registry,
                  experience_book=experience_book)
    used.append("agent        -> Agent (harness spine)")

    print("PACKAGE WIRING (each built from its canonical package):")
    for line in used:
        print(f"  ✓ diracdata.{line}")

    print(f"\nRunning on '{args.schema}': {args.question}")
    ans = agent.run(args.question, conversation=conversation)
    agent.flush_memory()
    print("\n" + "═" * 64)
    print(f"ANSWER: {ans.answer}")
    print(f"PERF: steps={ans.steps} | tokens={ans.tokens}")
    print("═" * 64)
    print("\n✅ all packages imported, composed, and produced an answer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
