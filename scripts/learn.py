#!/usr/bin/env python3
"""Learning agent (Phase 1): compile the context fabric for a schema.

Profiles every table/column (measured SQL facts) and describes them (LLM, grounded in those
facts), writing <schema>_metadata_descriptions.json and <schema>_value_domains.json -- the same
files the query agent (ask.py) loads. Model is chosen via the factory, like the query agent.

    PYTHONPATH=v2/src:v3/src .venv/bin/python v3/scripts/learn.py --schema fintech_schema
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diracdata.utils.model_factory import ChatModelFactory  # noqa: E402
from diracdata.utils.duckdb_engine import DuckDBEngine  # noqa: E402
from diracdata.engines import SourceRegistry  # noqa: E402
from diracdata.config import settings_from_env  # noqa: E402

from diracdata.context.fabric import fabric_store_from_settings  # noqa: E402
from diracdata.learning import LearningAgent, write_artifacts  # noqa: E402
from diracdata.learning.bindings import discover_bindings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default=None, help="Learn a local DuckDB schema (single-source).")
    ap.add_argument("--source", default=None, help="Learn ONE source from DIRACDATA_SOURCES (any engine).")
    ap.add_argument("--estate", action="store_true",
                    help="Learn EVERY source in DIRACDATA_SOURCES + discover cross-source bindings.")
    ap.add_argument("--model-profile", default="bedrock_zai_glm_5_ap_south_1")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--quiet", action="store_true", help="Only progress lines, no streamed thinking.")
    args = ap.parse_args()

    _state = {"stage": None}

    def sink(stage: str, kind: str, text: str) -> None:
        if args.quiet:
            if kind == "info":
                print(f"\033[2m[{stage}] {text}\033[0m", file=sys.stderr, flush=True)
            return
        if kind == "token":
            if _state["stage"] != stage:
                print(f"\n\033[1m──────── {stage.upper()} (thinking) ────────\033[0m", file=sys.stderr, flush=True)
                _state["stage"] = stage
            print(text, end="", file=sys.stderr, flush=True)
        elif kind == "info":
            print(f"\n\033[2m[{stage}] {text}\033[0m", file=sys.stderr, flush=True)
            _state["stage"] = None
        elif kind == "tool_call":
            print(f"\n  \033[36m>> TOOL:\033[0m {text}", file=sys.stderr, flush=True)
            _state["stage"] = None
        elif kind == "tool_result":
            print(f"  \033[2m<< {text}\033[0m", file=sys.stderr, flush=True)

    settings = replace(settings_from_env(args.env_file), agent_model_profile=args.model_profile, stream_tokens=True)
    model = ChatModelFactory(settings=settings).create_chat_model(profile_id=args.model_profile)
    store = fabric_store_from_settings(settings)

    def learn_one(engine, key: str) -> None:
        result = LearningAgent(engine=engine, model=model, sink=sink).compile()
        keys = write_artifacts(result, schema=key, store=store)
        print(f"\ncompiled {key}: {len(result.tables)} tables ({result.tokens} tokens) -> {', '.join(keys)}",
              file=sys.stderr)

    if args.estate or args.source:
        registry = SourceRegistry.from_env()
        if registry is None:
            sys.exit("--source/--estate need DIRACDATA_SOURCES set (see docs/postgres-setup.md)")
        names = registry.names() if args.estate else [args.source]
        for name in names:
            sink("learn", "info", f"learning source: {name}")
            learn_one(registry.get(name), name)
        if args.estate:
            sink("learn", "info", "discovering cross-source bindings")
            bindings = discover_bindings(registry)
            store.put("estate", "bindings.json", bindings)
            print(f"\nestate bindings ({len(bindings)}): " +
                  "; ".join(f"{b['left']}={b['right']} ({b['overlap_pct']}%)" for b in bindings), file=sys.stderr)
    else:
        if not args.schema:
            sys.exit("give --schema (local DuckDB), --source <name>, or --estate")
        learn_one(DuckDBEngine(data_root=Path(args.data_root), schema_name=args.schema), args.schema)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
