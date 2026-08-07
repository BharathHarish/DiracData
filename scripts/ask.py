#!/usr/bin/env python3
"""v4 CLI: ask one analytics question (or run a REPL) with the single-loop analyst.

    PYTHONPATH=v2/src:v3/src:v4/src .venv/bin/python v4/scripts/ask.py \
        --schema retail_analytics --question "..."

Reads the compiled fabric from the object store (MinIO), stores query results as parquet in the
object store, and streams the agent's tool use.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diracdata.utils.model_factory import ChatModelFactory  # noqa: E402
from diracdata.utils.duckdb_engine import DuckDBEngine  # noqa: E402
from diracdata.engines import SourceRegistry  # noqa: E402
from diracdata.config import settings_from_env  # noqa: E402
from diracdata.utils.object_store import object_store_from_settings  # noqa: E402

from diracdata.context.fabric import fabric_store_from_settings  # noqa: E402
from diracdata.experiences import ExperienceBook  # noqa: E402
from diracdata.context.valuecache import ColumnValueCache  # noqa: E402
from diracdata.context.workspace import Workspace  # noqa: E402

from diracdata.agent import Agent  # noqa: E402
from diracdata.memory.conversation import Conversation  # noqa: E402
from diracdata.memory.results import ResultStore  # noqa: E402
from diracdata.execution import make_executor  # noqa: E402
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
            print(f"\n  \033[36m>> {text}\033[0m", file=sys.stderr, flush=True)
            state["stage"] = None
        elif kind == "tool_result":
            print(f"  \033[2m<< {text}\033[0m", file=sys.stderr, flush=True)
        elif kind == "reasoning":
            if state["stage"] != f"reasoning:{stage}":
                print(f"\n\033[2m····· {stage} thinking ·····\033[0m", file=sys.stderr, flush=True)
                state["stage"] = f"reasoning:{stage}"
            print(f"\033[2m{text}\033[0m", end="", file=sys.stderr, flush=True)
        elif kind == "usage":
            print(f"\n\033[2m[{stage} usage] {text}\033[0m", file=sys.stderr, flush=True)
            state["stage"] = None
        elif kind == "info":
            print(f"\n\033[2m[{stage}] {text}\033[0m", file=sys.stderr, flush=True)
            state["stage"] = None

    return sink


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--question", default=None, help="Ask one question and exit; omit for a REPL.")
    ap.add_argument("--schema", default="retail_analytics")
    ap.add_argument("--sources", default=None,
                    help="YAML manifest declaring a multi-DB estate (else DIRACDATA_SOURCES, else single).")
    ap.add_argument("--model-profile", default="openai_gpt_5_4_mini",
                    help="Base/bootstrap model. With the router ON (default) the garden auto-routes per "
                         "query: Haiku=complex, Mini=medium, Nano=simple.")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--max-steps", type=int, default=None, help="Override the loop budget (else config).")
    ap.add_argument("--conversation-id", default=None,
                    help="Durable conversation id; resume a prior one to carry its memory across sessions.")
    ap.add_argument("--stream-mode", default=None, choices=["off", "messages", "updates", "all"],
                    help="What to stream live: off | messages | updates | all (default: config/messages).")
    ap.add_argument("--no-stream", action="store_true", help="Alias for --stream-mode off.")
    ap.add_argument("--no-router", action="store_true",
                    help="Pin --model-profile for every stage (turn OFF garden auto-routing). Use with "
                         "--model-profile anthropic_haiku_45 to stay entirely on Anthropic (cached, no OpenAI TPM).")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    _base = settings_from_env(args.env_file)
    settings = replace(_base, agent_model_profile=args.model_profile, stream_tokens=True,
                       router_enabled=(False if args.no_router else _base.router_enabled))
    model = ChatModelFactory(settings=settings).create_chat_model(profile_id=args.model_profile)
    # Estate: --sources <yaml> OR DIRACDATA_SOURCES env -> a SourceRegistry (default = first source).
    # Single-source (default): one DuckDB engine over --schema, wrapped as a one-source registry.
    registry = SourceRegistry.load(args.sources)
    if registry is not None:
        engine = registry.get_default()
        schema = engine.name
    else:
        engine = DuckDBEngine(data_root=Path(args.data_root), schema_name=args.schema)
        registry = SourceRegistry.of(engine)
        schema = args.schema
    fabric = fabric_store_from_settings(settings)
    obj_store = object_store_from_settings(settings)

    gold = ROOT / "data" / "evals" / "Goldset_retail_queries.csv"
    history = ROOT / "data" / "query_history" / f"{schema}_query_history.csv"
    workspace = Workspace.from_store(
        store=fabric, schema=schema,
        gold_pairs_path=gold if gold.exists() else None,
        query_history_path=history if history.exists() else None)
    value_cache = ColumnValueCache(fabric, schema)
    result_store = ResultStore(engine=engine, store=obj_store, schema=schema, sources=registry,
                               preview_rows=settings.preview_rows, preview_all_max=settings.preview_all_max,
                               reconciler_memory_limit=settings.reconciler_memory_limit,
                               reconciler_temp_dir=settings.reconciler_temp_dir,
                               reconciler_threads=settings.reconciler_threads,
                               executor=make_executor(settings))
    # One wrap point governs the whole live stream: model output (token/reasoning/usage) + loop (tools/info).
    mode = "off" if args.no_stream else (args.stream_mode or settings.stream_mode)
    sink = mode_sink(_make_sink(args.quiet), mode)

    def asker(question: str) -> str:
        print(f"\n\033[33m❓ {question}\033[0m", file=sys.stderr, flush=True)
        try:
            return input("   your answer> ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    experience_book = ExperienceBook(schema, obj_store)  # schema-scoped agentic memory (async curator)
    bindings = fabric.get("estate", "bindings.json", None)   # cross-source map (learn.py --estate)
    agent = Agent(model=model, workspace=workspace, engine=engine, result_store=result_store,
                       sink=sink, config=settings, max_steps=args.max_steps, value_cache=value_cache,
                       asker=asker, experience_book=experience_book, sources=registry, bindings=bindings)

    # Durable conversation memory: transcript.md + running summary.md carry follow-ups across turns
    # AND across sessions (resume a prior id). A fresh REPL session gets a new id by default.
    conv_id = args.conversation_id or f"repl-{uuid.uuid4().hex[:8]}"
    conversation = Conversation(conv_id, store=obj_store, config=settings)  # durable in the object store
    print(f"[fabric] default={schema} | sources: {', '.join(registry.names())} "
          f"| {len(workspace.tables())} fabric tables | {len(workspace.examples)} examples "
          f"| {'✓' if workspace.semantic_layer else '—'} definitions "
          f"| conversation {conv_id} ({conversation.turns} prior turns)", file=sys.stderr)

    def ask(q: str) -> None:
        ans = agent.run(q, conversation=conversation)
        print("\n" + "═" * 64)
        print(f"QUESTION: {q}")
        print(f"ANSWER: {ans.answer}")
        print(f"PERFORMANCE: steps={ans.steps} | tokens={ans.tokens} | results={len(ans.memory.results)}")
        print(f"MEMORY: {conversation.location} transcript.md (+summary.md), {conversation.turns} turn(s)")
        print("═" * 64)

    if args.question:
        ask(args.question)
        agent.flush_memory()   # finish async curation before exit
        return 0
    print("analyst REPL -- Ctrl-C to exit.", file=sys.stderr)
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
