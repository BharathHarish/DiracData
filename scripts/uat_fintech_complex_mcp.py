#!/usr/bin/env python3
"""Fintech_complex MCP UAT: learn (if needed) + deterministic gates + multi-question query loop.

    PYTHONPATH=src .venv/bin/python scripts/uat_fintech_complex_mcp.py
    PYTHONPATH=src .venv/bin/python scripts/uat_fintech_complex_mcp.py --skip-learn
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SCHEMA = "fintech_complex"
DATA = str(ROOT / "data" / "fintech_complex" / "parquet")
ENV = str(ROOT / ".env")
MODEL = "fireworks_deepseek_v4_flash"

QUESTIONS = [
    "List all tables and one-line what each is for.",
    "How many users and how many orders are there?",
    "What is total payment amount, and break it down by payment status if available.",
    "Join users to orders: top 5 users by order count with their email or name if present.",
    "Are there nested/complex columns? Show an access recipe and a sample query using it.",
    "What is the date range of orders vs payments? Do they fully overlap?",
    "Find any NULL-heavy join keys between payments and orders and estimate orphan rate.",
    "Compute average order value and median if possible.",
    "Which user attributes correlate with higher payment totals? Keep it simple.",
    "Write a sanity-checked multi-table query for revenue by month (use data_check).",
]


def _build_server():
    from diracdata.mcps import context_mcp
    return context_mcp(schema=SCHEMA, data=DATA, store="s3", model=MODEL, env_file=ENV)


def _tools(server):
    # Prefer public list if available; else use our context_tools via runtime.
    from diracdata.mcps.server import context_mcp as _
    # Rebuild tools map through context_mcp internals: call list from registered server
    # FastMCP-style: server may expose _tool_manager; fall back to context_tools(rt).
    from diracdata.mcps.tools import context_tools
    from diracdata.mcps.server import _Runtime
    from diracdata.config import settings_from_env
    from dataclasses import replace
    from diracdata.mcps.duckdb_source import DuckDBFileEngine

    settings = settings_from_env(ENV)
    settings = replace(settings, object_store="s3", agent_model_profile=MODEL)
    engine = DuckDBFileEngine(path=DATA, name=SCHEMA)
    rt = _Runtime(settings=settings, default_schema=SCHEMA, engine=engine, model=MODEL)
    return {t.__name__: t for t in context_tools(rt)}, rt


def run_learn(rt, tools) -> dict:
    print("=== LEARN: learn_schema ===", flush=True)
    raw = tools["learn_schema"](SCHEMA)
    print(raw, flush=True)
    job = json.loads(raw)
    job_id = job["job_id"]
    t0 = time.time()
    while True:
        st = json.loads(tools["learn_status"](job_id))
        print(f"  status={st.get('status')} elapsed={time.time()-t0:.0f}s", flush=True)
        if st.get("status") in ("done", "error"):
            return st
        if time.time() - t0 > 60 * 45:
            return {"status": "timeout", **st}
        time.sleep(5)


def run_gates(tools) -> list[dict]:
    results = []
    checks = [
        ("get_dialect", lambda: tools["get_dialect"]()),
        ("list_tables", lambda: tools["list_tables"]()),
        ("get_metric", lambda: tools["get_metric"]("")),
        ("completeness_check", lambda: tools["completeness_check"](SCHEMA, False)),
        ("fabric_health", lambda: tools["fabric_health"](SCHEMA)),
        ("clarify", lambda: tools["clarify"]("GMV or net revenue?", "gmv,net")),
        ("detect_boundary_convention", lambda: tools["detect_boundary_convention"]("amount_threshold", "0,10,100")),
    ]
    for name, fn in checks:
        try:
            out = fn()
            ok = True
            if name in ("completeness_check", "fabric_health"):
                # learning may still be incomplete; record but don't fail hard
                pass
            results.append({"check": name, "ok": ok, "preview": str(out)[:300]})
            print(f"[gate] {name} OK :: {str(out)[:160]}", flush=True)
        except Exception as exc:  # noqa: BLE001
            results.append({"check": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            print(f"[gate] {name} FAIL :: {exc}", flush=True)
    # bind-check every metric if listed
    try:
        names = json.loads(tools["get_metric"]("")).get("metric_names") or []
    except Exception:
        names = []
        # get_metric empty may return text list
        raw = tools["get_metric"]("")
        print(f"[gate] get_metric raw: {raw[:200]}", flush=True)
    for n in names[:20]:
        try:
            b = json.loads(tools["metric_bind_check"](n, "", SCHEMA))
            results.append({"check": f"bind:{n}", "ok": bool(b.get("ok")), "preview": str(b)[:200]})
            print(f"[bind] {n} ok={b.get('ok')} err={b.get('parse_error')}", flush=True)
        except Exception as exc:  # noqa: BLE001
            results.append({"check": f"bind:{n}", "ok": False, "error": str(exc)})
    return results


async def run_questions(questions: list[str], max_iters: int = 12) -> list[dict]:
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from openai import OpenAI
    from diracdata.config import settings_from_env
    from diracdata.models.factory import BUILT_IN_MODEL_PROFILES

    s = settings_from_env(ENV)
    api_key = os.environ.get("FIREWORKS_API_KEY") or os.environ.get("DIRACDATA_FIREWORKS_API_KEY") or s.fireworks_api_key
    fw = OpenAI(base_url="https://api.fireworks.ai/inference/v1", api_key=api_key)
    prof = BUILT_IN_MODEL_PROFILES.get(MODEL)
    model_id = prof.model if prof else MODEL

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    server = StdioServerParameters(
        command=str(ROOT / ".venv" / "bin" / "python"),
        args=["-m", "diracdata.mcps", "--schema", SCHEMA, "--data", DATA,
              "--store", "s3", "--model", MODEL, "--env-file", ENV],
        env=env,
    )

    outcomes = []
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            tool_names = [t.name for t in listed.tools]
            print(f"[mcp] tools({len(tool_names)}): {tool_names}", flush=True)
            oai_tools = []
            for t in listed.tools:
                oai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": (t.description or "")[:1024],
                        "parameters": getattr(t, "inputSchema", None)
                        or getattr(t, "input_schema", None)
                        or {"type": "object", "properties": {}},
                    },
                })

            sys_prompt = (
                f"You are a data analyst for '{SCHEMA}'. Use MCP tools. "
                "Call get_dialect before first run_sql. Prefer find_examples, then grounding tools, "
                "then run_sql; use data_check on multi-table SQL; call metric_bind_check before trusting metrics. "
                "If ambiguous, call clarify. Answer with concrete numbers."
            )
            for q in questions:
                print(f"\n=== Q: {q}", flush=True)
                messages = [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": q},
                ]
                answer = None
                tools_used = []
                for step in range(max_iters):
                    resp = fw.chat.completions.create(
                        model=model_id, messages=messages, tools=oai_tools,
                        tool_choice="auto", temperature=0,
                    )
                    msg = resp.choices[0].message
                    if not msg.tool_calls:
                        answer = msg.content or ""
                        print(f"ANSWER: {answer[:500]}", flush=True)
                        break
                    messages.append({
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [{
                            "id": tc.id, "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        } for tc in msg.tool_calls],
                    })
                    for tc in msg.tool_calls:
                        name = tc.function.name
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                        except Exception:
                            args = {}
                        tools_used.append(name)
                        print(f"  >> {name}({json.dumps(args)[:140]})", flush=True)
                        result = await session.call_tool(name, args)
                        text = "".join(getattr(c, "text", "") or "" for c in result.content)[:6000]
                        print(f"     << {text[:180].replace(chr(10),' ')}", flush=True)
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
                outcomes.append({
                    "question": q,
                    "answered": bool(answer),
                    "answer_preview": (answer or "")[:400],
                    "tools_used": tools_used,
                })
    return outcomes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-learn", action="store_true")
    ap.add_argument("--skip-questions", action="store_true")
    ap.add_argument("--max-iters", type=int, default=12)
    ap.add_argument("--limit-questions", type=int, default=0)
    args = ap.parse_args()

    tools, rt = _tools(None)
    print(f"tables={rt.engine.list_tables()}", flush=True)

    learn_result = None
    if not args.skip_learn:
        learn_result = run_learn(rt, tools)
        print("LEARN RESULT:", json.dumps(learn_result, default=str)[:500], flush=True)
        if learn_result.get("status") == "error":
            print("LEARN FAILED", flush=True)
            return 1

    # refresh tool closures after learn (new context)
    tools, rt = _tools(None)
    gates = run_gates(tools)

    outcomes = []
    if not args.skip_questions:
        import asyncio
        qs = QUESTIONS
        if args.limit_questions:
            qs = qs[: args.limit_questions]
        outcomes = asyncio.run(run_questions(qs, max_iters=args.max_iters))

    summary = {
        "learn": learn_result,
        "gates": gates,
        "questions": outcomes,
        "gate_failures": [g for g in gates if not g.get("ok")],
        "unanswered": [o for o in outcomes if not o.get("answered")],
    }
    out_path = ROOT / "scratch_logs" / "uat_fintech_complex_mcp.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nWrote {out_path}", flush=True)
    print(f"gate_failures={len(summary['gate_failures'])} unanswered={len(summary['unanswered'])}", flush=True)
    return 1 if summary["gate_failures"] or summary["unanswered"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
