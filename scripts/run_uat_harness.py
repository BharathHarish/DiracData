#!/usr/bin/env python3
"""Retail cheap-first harness UAT — Fireworks garden + full agent stack.

Thesis under test
-----------------
Start EVEN cold RCA on DeepSeek V4 Flash (~$0.28/M out). Active garden:
  Flash, GPT-OSS 120B, MiniMax M2.7, MiniMax M3, Nemotron 3 Ultra.
Escalate inside that set only when a cheaper model fails. ~$4 models (Kimi/GLM)
are registered but NOT in the default garden.

Logs verbatim answers + routing/harness traces to the object store:

    debug/<schema>/<run_id>/<case_id>.json
    debug/<schema>/<run_id>/summary.json
    debug/<schema>/<run_id>/summary.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import traceback
import uuid
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diracdata.agent import Agent  # noqa: E402
from diracdata.config import settings_from_env  # noqa: E402
from diracdata.context.fabric import fabric_store_from_settings  # noqa: E402
from diracdata.context.valuecache import ColumnValueCache  # noqa: E402
from diracdata.context.workspace import Workspace  # noqa: E402
from diracdata.engines import SourceRegistry  # noqa: E402
from diracdata.execution import make_executor  # noqa: E402
from diracdata.memory import ExperienceBook  # noqa: E402
from diracdata.checkpoints.conversation import Conversation  # noqa: E402
from diracdata.runtime.results import ResultStore  # noqa: E402
from diracdata.utils.duckdb_engine import DuckDBEngine  # noqa: E402
from diracdata.utils.model_factory import (  # noqa: E402
    FIREWORKS_COST_AWARE_GARDEN,
    ChatModelFactory,
    garden_profiles,
    render_catalog,
)
from diracdata.utils.object_store import object_store_from_settings  # noqa: E402

# Approximate Fireworks list $/1M output for thesis scoring (from Fireworks Model Library).
_OUT_USD_PER_M = {
    "fireworks_deepseek_v4_flash": 0.28,
    "fireworks_gpt_oss_120b": 0.60,
    "fireworks_minimax_m2p7": 1.20,
    "fireworks_minimax_m3": 1.20,
    "fireworks_nemotron_3_ultra": 2.40,
    "fireworks_kimi_k2p7_code": 4.00,
    "fireworks_glm_5p2": 4.40,
}
_CHEAP = {"fireworks_deepseek_v4_flash", "fireworks_gpt_oss_120b"}
_MID = {"fireworks_minimax_m2p7", "fireworks_minimax_m3"}
_STRONG = {"fireworks_nemotron_3_ultra"}
_HIGH = {"fireworks_kimi_k2p7_code", "fireworks_glm_5p2"}


class TraceSink:
    def __init__(self, *, quiet: bool = False) -> None:
        self.quiet = quiet
        self.events: list[dict[str, str]] = []
        self.tools: Counter[str] = Counter()
        self.triage = ""
        self.router: list[str] = []
        self.rejects: list[str] = []
        self.accepts = 0

    def __call__(self, stage: str, kind: str, text: str) -> None:
        t = str(text)
        self.events.append({"stage": stage, "kind": kind, "text": t[:4000]})
        if kind == "tool_call":
            name = t.split("(", 1)[0].strip() or "?"
            self.tools[name] += 1
            if not self.quiet:
                print(f"  >> {name}", file=sys.stderr, flush=True)
        elif kind == "tool_result" and t.upper().startswith("REJECTED"):
            self.rejects.append(t[:800])
        elif kind == "tool_result" and t.upper().startswith("ACCEPTED"):
            self.accepts += 1
        elif kind == "info":
            if stage == "triage":
                self.triage = t
            elif stage == "router":
                self.router.append(t)
            if not self.quiet:
                print(f"[{stage}] {t}", file=sys.stderr, flush=True)


def _router_profile(infos: list[str]) -> str:
    for info in infos:
        m = re.search(r"model=([^\s]+)", info)
        if m:
            return m.group(1)
    return ""


def _route_tier(profile: str) -> str:
    if profile in _CHEAP:
        return "cheap"
    if profile in _MID:
        return "mid"
    if profile in _STRONG:
        return "strong"
    if profile in _HIGH:
        return "high"
    if profile in {"", "(global)", "(none)"}:
        return "global"
    return "other"


def _expected_hits(answer: str, expected: str) -> dict[str, Any]:
    if not (expected or "").strip():
        return {"checked": False, "hits": [], "misses": [], "all_hit": None}
    ans = answer.replace(",", "").lower()
    hits, misses = [], []
    for token in re.split(r"[;/]+", expected):
        tok = token.strip().replace(",", "")
        if not tok:
            continue
        needle = tok.lower().replace("$", "").replace(" ", "")
        ok = needle in ans.replace(" ", "").replace("$", "")
        (hits if ok else misses).append(tok)
    return {"checked": True, "hits": hits, "misses": misses, "all_hit": not misses}


def _check_harness(row: dict[str, str], sink: TraceSink, answer: str, profile: str) -> dict[str, Any]:
    checks = [c.strip() for c in (row.get("checks") or "").split(";") if c.strip()]
    tools = set(sink.tools)
    triage_l = sink.triage.lower()
    tier = _route_tier(profile)
    out: dict[str, Any] = {}
    for c in checks:
        if c == "finish_accept":
            out[c] = sink.accepts > 0
        elif c == "count":
            out[c] = bool(re.search(r"\b\d[\d,]*\b", answer))
        elif c == "define":
            out[c] = "define" in tools
        elif c in {"metric", "ratio", "segment", "two_period", "multi_join", "multi_fact",
                   "aggregation", "filters", "gold_exact", "gold_style", "income_band", "join"}:
            out[c] = "run_sql" in tools or "query_result" in tools or "define" in tools
        elif c == "cohort":
            out[c] = "run_sql" in tools and any(w in answer.lower() for w in ("new", "returning", "cohort"))
        elif c == "mece":
            out[c] = "run_sql" in tools
        elif c == "rca":
            out[c] = "rca" in triage_l or bool(tools & {"attribute", "metric_tree"})
        elif c == "triage_rca":
            out[c] = "task=rca" in triage_l
        elif c == "data_health_or_check":
            out[c] = bool(tools & {"data_health", "data_check", "run_sql"})
        elif c in {"followup", "transcript_or_summary", "summary_reuse"}:
            out[c] = bool(answer.strip())
        elif c == "route_cheap":
            out[c] = tier == "cheap"
        elif c == "route_flash_preferred":
            out[c] = profile == "fireworks_deepseek_v4_flash" or tier == "cheap"
        elif c == "route_not_nemotron_first":
            # First attempt should not jump to Nemotron without prior failure (we only see final pick).
            out[c] = tier in {"cheap", "mid", "global"}
        elif c == "route_lt1":
            out[c] = tier == "cheap"
        elif c == "route_gt4":
            out[c] = tier == "high"
        elif c == "route_not_gt4":
            out[c] = tier != "high"
        elif c == "route_lt1_or_mid":
            out[c] = tier in {"cheap", "mid", "global"}
        else:
            out[c] = None
    # expected_route_tier column (authoritative thesis label)
    want = (row.get("expected_route_tier") or "").strip()
    if want in {"lt1", "flash_or_oss", "flash_first"}:
        out["thesis_route"] = (profile == "fireworks_deepseek_v4_flash" or tier == "cheap"
                               or (want == "flash_first" and tier in {"cheap", "mid"} and profile != "fireworks_nemotron_3_ultra"))
        if want == "flash_first":
            out["thesis_route"] = profile == "fireworks_deepseek_v4_flash"
    elif want in {"lt1_or_mid", "cheap_or_mid"}:
        out["thesis_route"] = tier in {"cheap", "mid", "global"}
    elif want == "gt4":
        out["thesis_route"] = tier == "high"
    return out


def _load_cases(path: Path, *, only: set[str] | None, limit: int | None) -> list[dict[str, str]]:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    if only:
        rows = [r for r in rows if r["id"] in only]
    if limit is not None:
        rows = rows[:limit]
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="retail_analytics")
    ap.add_argument("--cases", default=str(ROOT / "tests" / "uat_retail_harness.csv"))
    ap.add_argument("--model-profile", default="fireworks_deepseek_v4_flash",
                    help="Bootstrap for triage/route/frame/verify (authoring is garden-routed).")
    ap.add_argument("--garden", default=",".join(FIREWORKS_COST_AWARE_GARDEN))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--only", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    run_id = args.run_id or f"uat-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    garden = tuple(p.strip() for p in args.garden.split(",") if p.strip())
    only = {x.strip() for x in args.only.split(",")} if args.only else None
    cases = _load_cases(Path(args.cases), only=only, limit=args.limit)
    if not cases:
        print("no cases", file=sys.stderr)
        return 2

    base = settings_from_env(args.env_file)
    settings = replace(
        base,
        agent_model_profile=args.model_profile,
        router_enabled=True,
        router_garden=garden,
        agentic_memory_enabled=True,
        stream_envelope_enabled=True,
        stream_tokens=False,
        rca_precompute_enabled=True,
        sanity_gate_enabled=True,
    )
    if args.max_steps is not None:
        settings = replace(settings, max_steps=args.max_steps)

    print(f"[uat] run_id={run_id} bootstrap={args.model_profile}", file=sys.stderr)
    print(f"[uat] garden:\n{render_catalog(garden_profiles(garden))}", file=sys.stderr)

    model = ChatModelFactory(settings=settings).create_chat_model(profile_id=args.model_profile)
    engine = DuckDBEngine(data_root=Path(args.data_root), schema_name=args.schema)
    registry = SourceRegistry.of(engine)
    fabric = fabric_store_from_settings(settings)
    obj_store = object_store_from_settings(settings)
    gold = ROOT / "data" / "evals" / "Goldset_retail_queries.csv"
    history = ROOT / "data" / "query_history" / f"{args.schema}_query_history.csv"
    workspace = Workspace.from_store(
        store=fabric, schema=args.schema,
        gold_pairs_path=gold if gold.exists() else None,
        query_history_path=history if history.exists() else None,
    )
    value_cache = ColumnValueCache(fabric, args.schema)
    book = ExperienceBook(args.schema, obj_store)
    book_before = book.read() or ""

    prefix = f"debug/{args.schema}/{run_id}"
    obj_store.write_json(f"{prefix}/manifest.json", {
        "run_id": run_id,
        "schema": args.schema,
        "thesis": "cheap-first: start even cold RCA on DeepSeek Flash; escalate in-garden only on failure",
        "bootstrap_model": args.model_profile,
        "router_garden": list(garden),
        "out_usd_per_m": _OUT_USD_PER_M,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "fabric_keys": fabric.list(args.schema),
        "workspace": {
            "tables": len(workspace.tables()),
            "examples": len(workspace.examples),
            "metrics": list((workspace.semantic_layer or {}).get("metrics") or {}),
            "dimensions": list((workspace.semantic_layer or {}).get("dimensions") or {}),
        },
        "experiences_chars_before": len(book_before),
    })

    conversations: dict[str, Conversation] = {}
    results: list[dict[str, Any]] = []

    def make_agent(sink: TraceSink) -> Agent:
        rs = ResultStore(
            engine=engine, store=obj_store, schema=engine.name, sources=registry,
            preview_rows=settings.preview_rows, preview_all_max=settings.preview_all_max,
            reconciler_memory_limit=settings.reconciler_memory_limit,
            reconciler_temp_dir=settings.reconciler_temp_dir,
            reconciler_threads=settings.reconciler_threads, executor=make_executor(settings),
        )
        return Agent(
            model=model, workspace=workspace, engine=engine, result_store=rs, sink=sink,
            config=settings, value_cache=value_cache, asker=lambda q: "", sources=registry,
            experience_book=book, max_steps=args.max_steps,
        )

    for i, row in enumerate(cases, 1):
        cid = row["id"]
        q = row["question"]
        conv_key = (row.get("conversation_id") or "").strip() or f"solo-{cid}"
        if conv_key not in conversations:
            conversations[conv_key] = Conversation(f"{run_id}-{conv_key}", store=obj_store, config=settings)
        conv = conversations[conv_key]
        sink = TraceSink(quiet=args.quiet)
        print(f"\n===== [{i}/{len(cases)}] {cid}: {q} =====", file=sys.stderr, flush=True)
        t0 = time.time()
        record: dict[str, Any] = {
            "id": cid, "category": row.get("category"), "difficulty": row.get("difficulty"),
            "question": q, "expected": row.get("expected"),
            "expected_route_tier": row.get("expected_route_tier"),
            "checks": row.get("checks"), "conversation_group": conv_key,
            "conversation_id": conv.id,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            agent = make_agent(sink)
            ans = agent.run(q, conversation=conv)
            agent.flush_memory()
            elapsed = round(time.time() - t0, 1)
            profile = _router_profile(sink.router)
            harness = _check_harness(row, sink, ans.answer, profile)
            expected = _expected_hits(ans.answer, row.get("expected") or "")
            record.update({
                "ok": True, "elapsed_s": elapsed, "answer_verbatim": ans.answer,
                "steps": ans.steps, "tokens": ans.tokens,
                "result_ids": list(getattr(ans.memory, "results", {}) or {}),
                "triage": sink.triage, "router": sink.router, "router_profile": profile,
                "router_tier": _route_tier(profile),
                "router_out_usd_per_m": _OUT_USD_PER_M.get(profile),
                "tool_counts": dict(sink.tools),
                "finish_accepts": sink.accepts, "finish_rejects": sink.rejects,
                "harness_checks": harness,
                "harness_pass": all(v is True for v in harness.values() if v is not None) if harness else None,
                "thesis_route_pass": harness.get("thesis_route"),
                "expected_check": expected, "events": sink.events[-200:], "error": None,
            })
            print(f"ANSWER ({elapsed}s steps={ans.steps} route={profile} "
                  f"${_OUT_USD_PER_M.get(profile, '?')}/M):\n{ans.answer[:500]}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            elapsed = round(time.time() - t0, 1)
            profile = _router_profile(sink.router)
            record.update({
                "ok": False, "elapsed_s": elapsed, "answer_verbatim": "",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-4000:],
                "triage": sink.triage, "router": sink.router, "router_profile": profile,
                "router_tier": _route_tier(profile),
                "tool_counts": dict(sink.tools), "finish_accepts": sink.accepts,
                "finish_rejects": sink.rejects, "events": sink.events[-200:],
            })
            print(f"FAIL {cid}: {record['error']}", file=sys.stderr)

        obj_store.write_json(f"{prefix}/{cid}.json", record)
        results.append(record)

    book_after = book.read() or ""
    thesis_fail = [r["id"] for r in results if r.get("thesis_route_pass") is False]
    summary = {
        "run_id": run_id,
        "schema": args.schema,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "n_cases": len(results),
        "n_ok": sum(1 for r in results if r.get("ok")),
        "n_fail": sum(1 for r in results if not r.get("ok")),
        "n_harness_pass": sum(1 for r in results if r.get("harness_pass") is True),
        "n_thesis_route_pass": sum(1 for r in results if r.get("thesis_route_pass") is True),
        "n_thesis_route_fail": len(thesis_fail),
        "thesis_route_failures": thesis_fail,
        "n_expected_all_hit": sum(1 for r in results if (r.get("expected_check") or {}).get("all_hit")),
        "router_profile_counts": dict(Counter(r.get("router_profile") or "(none)" for r in results)),
        "router_tier_counts": dict(Counter(r.get("router_tier") or "?" for r in results)),
        "experiences_chars_before": len(book_before),
        "experiences_chars_after": len(book_after),
        "experiences_grew": len(book_after) > len(book_before),
        "cases": [{
            "id": r["id"], "ok": r.get("ok"), "router_profile": r.get("router_profile"),
            "router_tier": r.get("router_tier"), "out_usd": r.get("router_out_usd_per_m"),
            "steps": r.get("steps"), "tokens": r.get("tokens"), "elapsed_s": r.get("elapsed_s"),
            "harness_pass": r.get("harness_pass"), "thesis_route_pass": r.get("thesis_route_pass"),
            "expected_all_hit": (r.get("expected_check") or {}).get("all_hit"),
            "triage": r.get("triage"), "error": r.get("error"),
        } for r in results],
    }
    obj_store.write_json(f"{prefix}/summary.json", summary)
    buf = io.StringIO()
    fields = ["id", "ok", "router_profile", "router_tier", "out_usd", "steps", "tokens",
              "elapsed_s", "harness_pass", "thesis_route_pass", "expected_all_hit", "triage", "error"]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for row in summary["cases"]:
        w.writerow(row)
    obj_store.write_text(f"{prefix}/summary.csv", buf.getvalue(), content_type="text/csv")

    print("\n" + "=" * 64, file=sys.stderr)
    print(f"UAT {summary['n_ok']}/{summary['n_cases']} ok | thesis_route_pass="
          f"{summary['n_thesis_route_pass']} fail={summary['thesis_route_failures']} | "
          f"routes={summary['router_profile_counts']}", file=sys.stderr)
    print(f"Logged: {prefix}/", file=sys.stderr)
    print(json.dumps(summary["cases"], indent=2))
    return 0 if summary["n_fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
