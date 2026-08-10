#!/usr/bin/env python3
"""Pin-matrix bench: run the SAME 5-6 retail queries on EVERY model in the cheap-first
Fireworks garden with the router OFF (so each profile is forced). Logs verbatim answers
to MinIO:

    debug/<schema>/<run_id>/<profile_id>/<case_id>.json
    debug/<schema>/<run_id>/summary.json

    PYTHONPATH=src .venv/bin/python scripts/pin_garden_matrix.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diracdata.agent import Agent  # noqa: E402
from diracdata.config import settings_from_env  # noqa: E402
from diracdata.context.fabric import fabric_store_from_settings  # noqa: E402
from diracdata.context.valuecache import ColumnValueCache  # noqa: E402
from diracdata.context.workspace import Workspace  # noqa: E402
from diracdata.engines import SourceRegistry  # noqa: E402
from diracdata.execution import make_executor  # noqa: E402
from diracdata.experiences import ExperienceBook  # noqa: E402
from diracdata.memory.conversation import Conversation  # noqa: E402
from diracdata.memory.results import ResultStore  # noqa: E402
from diracdata.utils.duckdb_engine import DuckDBEngine  # noqa: E402
from diracdata.utils.model_factory import FIREWORKS_COST_AWARE_GARDEN, ChatModelFactory  # noqa: E402
from diracdata.utils.object_store import object_store_from_settings  # noqa: E402

# Fixed matrix — simple → medium → RCA (same Qs on every pinned model).
CASES = [
    {"id": "P1", "tier": "simple", "q": "How many clients in total?", "expect": "100000"},
    {"id": "P2", "tier": "simple", "q": "total online revenue in 2001", "expect": "339537789.11"},
    {"id": "P3", "tier": "simple", "q": "How many retail locations are there?", "expect": ""},
    {"id": "P4", "tier": "medium",
     "q": "For online buyers in 2002 how many were new vs returning and what was each cohort's online revenue?",
     "expect": "6909;4343;197430325.83;127583020.59"},
    {"id": "P5", "tier": "medium",
     "q": "How many F online shoppers from TX bought Music in 2001?", "expect": ""},
    {"id": "P6", "tier": "rca",
     "q": ("Online revenue dropped in 2002 compared to 2001 — what happened and "
           "which segments impacted this drop the most?"),
     "expect": ""},
]

_OUT_USD = {
    "fireworks_deepseek_v4_flash": 0.28,
    "fireworks_gpt_oss_120b": 0.60,
    "fireworks_minimax_m2p7": 1.20,
    "fireworks_minimax_m3": 1.20,
    "fireworks_nemotron_3_ultra": 2.40,
}


class CapSink:
    def __init__(self) -> None:
        self.tools: dict[str, int] = {}
        self.triage = ""
        self.accepts = 0
        self.rejects: list[str] = []

    def __call__(self, stage: str, kind: str, text: str) -> None:
        t = str(text)
        if kind == "tool_call":
            name = t.split("(", 1)[0].strip() or "?"
            self.tools[name] = self.tools.get(name, 0) + 1
            print(f"  >> {name}", file=sys.stderr, flush=True)
        elif kind == "tool_result" and t.upper().startswith("ACCEPTED"):
            self.accepts += 1
        elif kind == "tool_result" and t.upper().startswith("REJECTED"):
            self.rejects.append(t[:400])
        elif kind == "info" and stage == "triage":
            self.triage = t
            print(f"[triage] {t}", file=sys.stderr, flush=True)


def _expect_hits(answer: str, expect: str) -> dict:
    if not expect.strip():
        return {"checked": False, "all_hit": None, "hits": [], "misses": []}
    ans = answer.replace(",", "").replace("$", "").lower()
    hits, misses = [], []
    for tok in expect.split(";"):
        t = tok.strip().replace(",", "").replace("$", "").lower()
        if not t:
            continue
        (hits if t in ans else misses).append(tok.strip())
    return {"checked": True, "all_hit": not misses, "hits": hits, "misses": misses}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="retail_analytics")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--models", default=",".join(FIREWORKS_COST_AWARE_GARDEN),
                    help="Comma-separated profiles to pin (default: full cheap-first garden).")
    ap.add_argument("--only", default=None, help="Comma-separated case ids, e.g. P1,P2,P6")
    ap.add_argument("--max-steps", type=int, default=None)
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    cases = CASES
    if args.only:
        want = {x.strip() for x in args.only.split(",")}
        cases = [c for c in CASES if c["id"] in want]

    run_id = args.run_id or f"pin-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    base = settings_from_env(args.env_file)
    # Router OFF — pin is absolute. Memory/envelope/RCA/sanity stay on.
    settings = replace(
        base,
        router_enabled=False,
        agentic_memory_enabled=True,
        stream_envelope_enabled=True,
        stream_tokens=False,
        rca_precompute_enabled=True,
        sanity_gate_enabled=True,
    )
    if args.max_steps is not None:
        settings = replace(settings, max_steps=args.max_steps)

    fabric = fabric_store_from_settings(settings)
    obj_store = object_store_from_settings(settings)
    engine = DuckDBEngine(data_root=Path(args.data_root), schema_name=args.schema)
    registry = SourceRegistry.of(engine)
    gold = ROOT / "data" / "evals" / "Goldset_retail_queries.csv"
    history = ROOT / "data" / "query_history" / f"{args.schema}_query_history.csv"
    workspace = Workspace.from_store(
        store=fabric, schema=args.schema,
        gold_pairs_path=gold if gold.exists() else None,
        query_history_path=history if history.exists() else None,
    )
    value_cache = ColumnValueCache(fabric, args.schema)
    book = ExperienceBook(args.schema, obj_store)

    prefix = f"debug/{args.schema}/{run_id}"
    obj_store.write_json(f"{prefix}/manifest.json", {
        "run_id": run_id,
        "mode": "pin_matrix",
        "router_enabled": False,
        "models": models,
        "cases": [{"id": c["id"], "tier": c["tier"], "q": c["q"]} for c in cases],
        "started_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"[pin] run_id={run_id} models={models} cases={[c['id'] for c in cases]}", file=sys.stderr)

    rows: list[dict] = []
    for profile in models:
        print(f"\n######## PIN {profile} (${_OUT_USD.get(profile, '?')}/M) ########", file=sys.stderr)
        try:
            model = ChatModelFactory(
                settings=replace(settings, agent_model_profile=profile)
            ).create_chat_model(profile_id=profile)
        except Exception as exc:  # noqa: BLE001
            print(f"SKIP {profile}: cannot build model: {exc}", file=sys.stderr)
            rows.append({"profile": profile, "id": "(build)", "ok": False, "error": str(exc)})
            continue

        for case in cases:
            cid, q = case["id"], case["q"]
            sink = CapSink()
            conv = Conversation(f"{run_id}-{profile}-{cid}", store=obj_store, config=settings)
            print(f"\n===== {profile} :: {cid} ({case['tier']}) :: {q} =====", file=sys.stderr)
            t0 = time.time()
            rec: dict = {
                "profile": profile, "out_usd_per_m": _OUT_USD.get(profile),
                "id": cid, "tier": case["tier"], "question": q, "expect": case["expect"],
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                rs = ResultStore(
                    engine=engine, store=obj_store, schema=engine.name, sources=registry,
                    preview_rows=settings.preview_rows, preview_all_max=settings.preview_all_max,
                    reconciler_memory_limit=settings.reconciler_memory_limit,
                    reconciler_temp_dir=settings.reconciler_temp_dir,
                    reconciler_threads=settings.reconciler_threads,
                    executor=make_executor(settings),
                )
                agent = Agent(
                    model=model, workspace=workspace, engine=engine, result_store=rs, sink=sink,
                    config=replace(settings, agent_model_profile=profile),
                    value_cache=value_cache, asker=lambda _q: "", sources=registry,
                    experience_book=book, max_steps=args.max_steps,
                )
                ans = agent.run(q, conversation=conv)
                agent.flush_memory()
                elapsed = round(time.time() - t0, 1)
                exp = _expect_hits(ans.answer, case["expect"])
                rec.update({
                    "ok": True, "elapsed_s": elapsed, "answer_verbatim": ans.answer,
                    "steps": ans.steps, "tokens": ans.tokens,
                    "triage": sink.triage, "tool_counts": sink.tools,
                    "finish_accepts": sink.accepts, "finish_rejects": sink.rejects,
                    "expected_check": exp, "error": None,
                })
                print(f"ANSWER ({elapsed}s steps={ans.steps}):\n{ans.answer[:500]}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                elapsed = round(time.time() - t0, 1)
                rec.update({
                    "ok": False, "elapsed_s": elapsed, "answer_verbatim": "",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc()[-3000:],
                    "triage": sink.triage, "tool_counts": sink.tools,
                    "finish_accepts": sink.accepts, "finish_rejects": sink.rejects,
                })
                print(f"FAIL {profile}/{cid}: {rec['error']}", file=sys.stderr)

            obj_store.write_json(f"{prefix}/{profile}/{cid}.json", rec)
            rows.append(rec)

    # Compact summary matrix
    summary = {
        "run_id": run_id,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "n": len(rows),
        "n_ok": sum(1 for r in rows if r.get("ok")),
        "by_model": {},
        "cells": [],
    }
    for profile in models:
        subset = [r for r in rows if r.get("profile") == profile]
        summary["by_model"][profile] = {
            "n_ok": sum(1 for r in subset if r.get("ok")),
            "n": len(subset),
            "avg_steps": round(sum(r.get("steps") or 0 for r in subset if r.get("ok")) /
                               max(1, sum(1 for r in subset if r.get("ok"))), 1),
            "avg_elapsed_s": round(sum(r.get("elapsed_s") or 0 for r in subset if r.get("ok")) /
                                   max(1, sum(1 for r in subset if r.get("ok"))), 1),
            "expected_hits": sum(1 for r in subset if (r.get("expected_check") or {}).get("all_hit")),
        }
    for r in rows:
        summary["cells"].append({
            "profile": r.get("profile"), "id": r.get("id"), "tier": r.get("tier"),
            "ok": r.get("ok"), "steps": r.get("steps"), "tokens": r.get("tokens"),
            "elapsed_s": r.get("elapsed_s"),
            "expected_all_hit": (r.get("expected_check") or {}).get("all_hit"),
            "finish_accepts": r.get("finish_accepts"),
            "error": r.get("error"),
            "answer_preview": (r.get("answer_verbatim") or "")[:240],
        })
    obj_store.write_json(f"{prefix}/summary.json", summary)
    print("\n" + "=" * 64, file=sys.stderr)
    print(json.dumps(summary["by_model"], indent=2), file=sys.stderr)
    print(f"Logged: {prefix}/", file=sys.stderr)
    print(json.dumps(summary["cells"], indent=2))
    return 0 if summary["n_ok"] == summary["n"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
