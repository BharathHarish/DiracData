#!/usr/bin/env python3
"""Interactive v3 agent -- type a query, watch the analyst think, see performance.

Streams the analyst loop (understand -> recall -> probe -> build), the independent verify,
and the durable investigator's orchestrator moves over the v3 workspace (schema map + gold
pairs + query history + semantic layer + learned experiences). With no --question it opens a
REPL: type queries, watch the stages stream their thinking and tool calls live, then get a
performance readout. The experience store persists across the session, so a novel verified
query can be found and reused by a later one.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "v2" / "src"))
sys.path.insert(0, str(ROOT / "v3" / "src"))

from diracdata_v2.llms.model_factory import ChatModelFactory  # noqa: E402
from diracdata_v2.query import DuckDBEngine  # noqa: E402
from diracdata_v2.settings import settings_from_env  # noqa: E402

from diracdata_v3 import ExperienceStore, Investigator, JoinStore, V3Agent, Workspace  # noqa: E402
from diracdata_v3.valuecache import ColumnValueCache  # noqa: E402

_DEFAULTS = {
    "metadata": "v2/context/retail_analytics_metadata_descriptions.json",
    "gold": "v2/evals/Goldset_retail_queries.csv",
    "history": "v2/data/query_history/retail_analytics_query_history.csv",
    "docs": ["v2/context/retail_analytics_metrics.yaml"],
    "value_domains": "v2/context/retail_analytics_value_domains.json",
    "semantic_layer": "v2/context/retail_analytics_semantic_layer.yaml",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--question", default=None, help="Ask one question and exit; omit for an interactive REPL.")
    ap.add_argument("--schema", default="retail_analytics")
    ap.add_argument("--model-profile", default="bedrock_zai_glm_5_ap_south_1")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--data-root", default=str(ROOT / "v2" / "data"))
    ap.add_argument("--experience-store", default=str(ROOT / "v3" / "experience" / "retail_analytics.jsonl"))
    ap.add_argument("--quiet", action="store_true", help="Do not stream the stages.")
    ap.add_argument("--shallow", action="store_true", help="Single-pass answer, no investigative loop.")
    ap.add_argument("--max-steps", type=int, default=6, help="Investigation step budget.")
    args = ap.parse_args()

    settings = replace(settings_from_env(args.env_file), agent_model_profile=args.model_profile, stream_tokens=True)
    factory = ChatModelFactory(settings=settings)
    model = factory.create_chat_model(profile_id=args.model_profile)
    steward_model = factory.create_chat_model(profile_id=args.model_profile)

    _state = {"stage": None}

    def out(text: str = "") -> None:
        print(text, file=sys.stderr, flush=True)

    def sink(stage: str, kind: str, text: str) -> None:
        if args.quiet:
            return
        if kind == "token":
            if _state["stage"] != stage:
                out()
                out(f"\033[1m──────── {stage.upper()} (thinking) ────────\033[0m")
                _state["stage"] = stage
            print(text, end="", file=sys.stderr, flush=True)
        elif kind == "info":
            out()
            out(f"\033[2m[{stage}] {text}\033[0m")
            _state["stage"] = None
        elif kind == "tool_call":
            out()
            out(f"  \033[36m>> TOOL CALL:\033[0m {text}")
            _state["stage"] = None
        elif kind == "tool_result":
            out(f"  \033[2m<< RESULT:\033[0m {text}")

    store = ExperienceStore(args.experience_store)
    join_store = JoinStore(ROOT / "v3" / "experience" / f"{args.schema}_joins.jsonl")
    workspace = Workspace.load(
        metadata_path=ROOT / _DEFAULTS["metadata"],
        gold_pairs_path=ROOT / _DEFAULTS["gold"],
        query_history_path=ROOT / _DEFAULTS["history"],
        docs_paths=[ROOT / d for d in _DEFAULTS["docs"]],
        experience_store=store,
        value_domains_path=ROOT / _DEFAULTS["value_domains"],
        join_store=join_store,
        semantic_layer_path=ROOT / _DEFAULTS["semantic_layer"],
    )
    engine = DuckDBEngine(data_root=Path(args.data_root), schema_name=args.schema)
    value_cache = ColumnValueCache(ROOT / "v3" / "experience" / f"{args.schema}_column_values.json")
    agent = V3Agent(model=model, steward_model=steward_model, workspace=workspace,
                    engine=engine, experience_store=store, join_store=join_store,
                    value_cache=value_cache, sink=sink)

    investigator = Investigator(agent=agent, model=model, sink=sink, max_steps=args.max_steps)

    def run_one(question: str) -> None:
        original = question
        clarifications: list[tuple[str, str]] = []
        while True:
            _state["stage"] = None
            started = time.perf_counter()
            if args.shallow:
                ci = "\n".join(f'The user was asked: "{q}" and answered: "{a}" (authoritative).'
                               for q, a in clarifications)
                ans = agent.answer(original, confirmed_intent=ci)
                clarify, answer, meta = ans.clarify, (ans.result or {}).get("rows"), \
                    f"route={ans.route} | tokens={ans.tokens}"
                sql = ans.final_sql
            else:
                inv = investigator.investigate(original, clarifications=clarifications)
                clarify, answer = inv.needs_clarification, inv.answer
                meta = f"steps={inv.steps} | converged={inv.converged} | tokens={inv.tokens}"
                sql = ""
            elapsed = time.perf_counter() - started
            out()
            out("\033[1m" + "═" * 64 + "\033[0m")
            out(f"\033[1mQUESTION:\033[0m {original}")
            if clarify:
                out(f"\033[1;33mCLARIFYING QUESTION:\033[0m {clarify}")
            else:
                out(f"\033[1mANSWER:\033[0m {answer}")
                if sql:
                    out(f"\033[1mSQL:\033[0m {' '.join(sql.split())}")
            out(f"\033[1mPERFORMANCE:\033[0m {elapsed:.1f}s | {meta}")
            out("\033[1m" + "═" * 64 + "\033[0m")
            print(json.dumps({"question": original, "answer": answer, "clarify": clarify,
                              "meta": meta, "elapsed_s": round(elapsed, 2)}, default=str))

            if not clarify:
                return
            try:
                reply = input("\n  \033[1;33myour answer>\033[0m ").strip()
            except (EOFError, KeyboardInterrupt):
                out("\n(no clarification given)")
                return
            if not reply:
                out("(no clarification given)")
                return
            clarifications.append((clarify, reply))

    if args.question:
        run_one(args.question)
        return 0

    out("\033[1mv3 interactive agent\033[0m -- type a question, or 'exit'. Experiences persist this session.")
    while True:
        try:
            question = input("\n\033[1mask>\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            out("\nbye")
            return 0
        if not question:
            continue
        if question.lower() in {"exit", "quit", ":q"}:
            return 0
        run_one(question)


if __name__ == "__main__":
    raise SystemExit(main())
