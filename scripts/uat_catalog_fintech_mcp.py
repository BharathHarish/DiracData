#!/usr/bin/env python3
"""Catalog MCP UAT on fintech_complex — patches 1 (dead QUERY tools) + 2 (search_fabric coverage).

    PYTHONPATH=src .venv/bin/python scripts/uat_catalog_fintech_mcp.py
    PYTHONPATH=src .venv/bin/python scripts/uat_catalog_fintech_mcp.py --skip-questions

Deterministic gates always run (in-process catalog tools). The LLM loop starts
dirac-catalog-mcp over stdio and answers questions using the server's own
instructions — it must not call schema-only tools.
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

CATALOG = "local"
DB = "fintech_complex"
ENV = str(ROOT / ".env")
MODEL = "fireworks_deepseek_v4_flash"

from diracdata.mcps.instructions import SCHEMA_ONLY_QUERY_TOOLS, catalog_instructions

DEAD = set(SCHEMA_ONLY_QUERY_TOOLS)
# data_check is shared — must be on catalog after Phase 1
assert "data_check" not in DEAD

QUESTIONS = [
    "What are the top fraud rule names triggered across all orders, and for each rule, "
    "what's the average fraud score on those orders and the share of those orders that "
    "ended up with a successful payment?",
    "Search fabric for payment-status or SUCCESS gotchas, then report payment_success_rate "
    "with the blessed definition.",
    "What is the date range of orders vs payments? Do they fully overlap?",
    "Join payments to orders: how many payment rows vs distinct orders, and what is the "
    "orphan rate of payments.order_ref against orders?",
    "Show the nested access recipe for orders.fraud_signals and a working UNNEST query.",
    "List tables in fintech_complex and one-line what each is for.",
]


def _runtime_tools():
    from diracdata.config import settings_from_env
    from diracdata.mcps.catalog_server import _CatalogRuntime, catalog_tools

    settings = settings_from_env(ENV)
    rt = _CatalogRuntime(catalog=CATALOG, settings=settings, model=MODEL)
    tools = {t.__name__: t for t in catalog_tools(rt)}
    return tools, rt


def _parse(raw):
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return raw


def run_deterministic_gates(tools) -> list[dict]:
    results = []

    def rec(name, ok, preview="", error=""):
        results.append({"check": name, "ok": ok, "preview": preview[:400], "error": error})
        tag = "OK" if ok else "FAIL"
        print(f"[gate] {name} {tag} :: {(error or preview)[:180]}", flush=True)

    cat = catalog_instructions()
    leaked = [n for n in DEAD if n in cat]
    rec("catalog_instructions_no_dead_tools", not leaked, preview=f"leaked={leaked}")

    names = set(tools)
    extra = sorted(DEAD & names)
    rec("catalog_tool_list_no_dead_tools", not extra, preview=f"extra={extra}")

    try:
        tools["use_database"](DB)
        rec("use_database", True, preview=DB)
    except Exception as exc:
        rec("use_database", False, error=f"{type(exc).__name__}: {exc}")
        return results

    try:
        dial = _parse(tools["get_dialect"](DB))
        rec("get_dialect", isinstance(dial, dict) and dial.get("dialect") == "duckdb",
            preview=str(dial)[:200])
    except Exception as exc:
        rec("get_dialect", False, error=str(exc))

    # Patch 2: seed a distinctive gotcha if fabric has no experiences yet, then grep.
    try:
        seed_phrase = "UAT_PATCH2_SUCCESS_UNNEST_GOTCHA"
        tools["save_experience"](
            f"{seed_phrase}: payment_status is uppercase SUCCESS; explode rules with "
            "UNNEST(fraud_signals.rules) at order grain.",
            "uat_catalog_fintech_mcp",
            "GOTCHAS",
            DB,
        )
        hits = _parse(tools["search_fabric"](seed_phrase, DB, 25))
        arts = {h.get("artifact") for h in (hits.get("hits") or [])}
        rec(
            "search_fabric_experiences_md",
            hits.get("ok") is True and "experiences.md" in arts,
            preview=f"artifacts={sorted(arts)} hit_count={hits.get('hit_count')}",
        )
    except Exception as exc:
        rec("search_fabric_experiences_md", False, error=str(exc))

    try:
        hits = _parse(tools["search_fabric"]("fraud_signals rules score STRUCT", DB, 25))
        rec("search_fabric_fraud_struct", bool(hits.get("hit_count")),
            preview=str(hits.get("hits", [])[:2])[:300])
    except Exception as exc:
        rec("search_fabric_fraud_struct", False, error=str(exc))

    try:
        b = _parse(tools["metric_bind_check"]("payment_success_rate", "", DB))
        rec("bind_payment_success_rate", bool(b.get("ok")), preview=str(b)[:250])
    except Exception as exc:
        rec("bind_payment_success_rate", False, error=str(exc))

    sql = """
    WITH exploded AS (
      SELECT o.order_ref, o.fraud_signals.score AS fraud_score,
             UNNEST(o.fraud_signals.rules) AS rule_name
      FROM orders o
    ),
    rules AS (
      SELECT DISTINCT order_ref, fraud_score, rule_name FROM exploded
    ),
    order_success AS (
      SELECT order_ref,
             MAX(CASE WHEN payment_status = 'SUCCESS' THEN 1 ELSE 0 END) AS has_success
      FROM payments GROUP BY 1
    )
    SELECT r.rule_name, COUNT(*) AS n_orders,
           ROUND(AVG(r.fraud_score), 4) AS avg_fraud_score,
           ROUND(AVG(COALESCE(s.has_success, 0)::DOUBLE), 4) AS success_share
    FROM rules r
    LEFT JOIN order_success s ON r.order_ref = s.order_ref
    GROUP BY 1 ORDER BY n_orders DESC
    """
    try:
        out = _parse(tools["run_sql"](sql, DB))
        rows = out.get("rows") or []
        ok = len(rows) >= 4 and all(len(r) >= 4 for r in rows[:4])
        rec("run_sql_fraud_rules", ok, preview=json.dumps(rows[:5], default=str))
    except Exception as exc:
        rec("run_sql_fraud_rules", False, error=str(exc))

    try:
        out = _parse(tools["run_sql"](
            "SELECT COUNT(*) n FROM payments p ANTI JOIN orders o ON p.order_ref = o.order_ref",
            DB,
        ))
        rec("run_sql_payment_orphans", True, preview=str(out.get("rows")))
    except Exception as exc:
        rec("run_sql_payment_orphans", False, error=str(exc))

    try:
        dt = _parse(tools["describe_table"]("orders", DB))
        cols = dt.get("columns") or []
        names = {c.get("column_name") for c in cols}
        rec(
            "describe_table_orders",
            isinstance(dt, dict) and "fraud_signals" in names and dt.get("row_count", 0) > 0
            and "max_rows" not in json.dumps(dt),
            preview=str(dt)[:250],
        )
    except Exception as exc:
        rec("describe_table_orders", False, error=str(exc))

    try:
        dc = _parse(tools["describe_column"]("orders", "fraud_signals", DB))
        rec(
            "describe_column_fraud_signals",
            isinstance(dc, dict) and "max_rows" not in json.dumps(dc)
            and ("STRUCT" in str(dc.get("type") or "") or bool(dc.get("long_description") or dc.get("description"))),
            preview=str(dc)[:250],
        )
        rec(
            "describe_column_fraud_has_recipe",
            isinstance(dc, dict)
            and bool(dc.get("access_recipe") or dc.get("access_recipes"))
            and bool(dc.get("runnable_example"))
            and "SELECT" in str(dc.get("runnable_example")),
            preview=(
                f"recipe={dc.get('access_recipe')!r} "
                f"runnable={(dc.get('runnable_example') or '')[:160]!r} "
                f"src={dc.get('access_recipe_source')}/{dc.get('runnable_example_source')}"
            ),
        )
    except Exception as exc:
        rec("describe_column_fraud_signals", False, error=str(exc))

    try:
        sch = _parse(tools["search_schema"]("fraud*", DB, 50))
        rec(
            "search_schema_fraud",
            isinstance(sch, dict) and sch.get("ok") is True
            and not any("max_rows" in str(w) for w in (sch.get("warnings") or []))
            and any(h.get("column") == "fraud_signals" or h.get("table") == "orders" for h in (sch.get("hits") or [])),
            preview=str(sch)[:250],
        )
    except Exception as exc:
        rec("search_schema_fraud", False, error=str(exc))

    try:
        samp = _parse(tools["sample_rows"]("orders", DB, 2))
        rec("sample_rows_orders", isinstance(samp, dict) and bool(samp.get("rows"))
            and "max_rows" not in json.dumps(samp), preview=str(samp)[:200])
    except Exception as exc:
        rec("sample_rows_orders", False, error=str(exc))

    try:
        prof = _parse(tools["profile"]("orders", DB))
        rec("profile_orders", isinstance(prof, dict) and prof.get("ok") is True
            and "max_rows" not in json.dumps(prof), preview=str(prof)[:250])
    except Exception as exc:
        rec("profile_orders", False, error=str(exc))

    # Phase 1: data_check must flag payment fan-out onto order grain, and clear aggregate-then-join.
    BAD_FANOUT = """
    WITH rules AS (
      SELECT o.order_ref, o.fraud_signals.score AS fraud_score,
             UNNEST(o.fraud_signals.rules) AS rule_name
      FROM orders o
    )
    SELECT r.rule_name, AVG(CASE WHEN p.payment_status='SUCCESS' THEN 1.0 ELSE 0 END) AS success_share
    FROM rules r JOIN payments p ON p.order_ref = r.order_ref
    GROUP BY 1
    """
    GOOD_GRAIN = """
    WITH exploded AS (
      SELECT o.order_ref, o.fraud_signals.score AS fraud_score,
             UNNEST(o.fraud_signals.rules) AS rule_name FROM orders o
    ),
    rules AS (SELECT DISTINCT order_ref, fraud_score, rule_name FROM exploded),
    order_success AS (
      SELECT order_ref,
             MAX(CASE WHEN payment_status='SUCCESS' THEN 1 ELSE 0 END) AS has_success
      FROM payments GROUP BY 1
    )
    SELECT r.rule_name, AVG(COALESCE(s.has_success,0)::DOUBLE) AS success_share
    FROM rules r LEFT JOIN order_success s ON r.order_ref = s.order_ref
    GROUP BY 1
    """
    try:
        bad_dc = _parse(tools["data_check"](BAD_FANOUT, DB))
        amp = [f for f in (bad_dc.get("flags") or [])
               if "amplif" in f.lower() or "children" in f.lower()]
        rec("data_check_flags_fanout_sql",
            isinstance(bad_dc, dict) and bad_dc.get("ok") is False and bool(amp),
            preview=str(bad_dc.get("flags"))[:300])
    except Exception as exc:
        rec("data_check_flags_fanout_sql", False, error=str(exc))
    try:
        good_dc = _parse(tools["data_check"](GOOD_GRAIN, DB))
        amp = [f for f in (good_dc.get("flags") or [])
               if "amplif" in f.lower() or "children" in f.lower()]
        rec("data_check_clears_aggregate_then_join",
            isinstance(good_dc, dict) and amp == [],
            preview=str(good_dc.get("flags"))[:300])
    except Exception as exc:
        rec("data_check_clears_aggregate_then_join", False, error=str(exc))
    try:
        assert "data_check" in tools
        rec("data_check_tool_registered", True, preview="data_check present")
    except Exception as exc:
        rec("data_check_tool_registered", False, error=str(exc))

    try:
        jp = _parse(tools["join_path"]("orders", DB))
        text = jp.get("text") or ""
        joins = jp.get("joins") or []
        cpp_ok = any((j.get("children_per_parent_avg") or 0) > 1.5 for j in joins)
        warn_ok = "aggregate-then-join" in text or "children/parent" in text
        rec("join_path_orders_warns_amplification",
            isinstance(jp, dict) and jp.get("n_joins", 0) >= 1 and (cpp_ok or warn_ok),
            preview=text[:300])
    except Exception as exc:
        rec("join_path_orders_warns_amplification", False, error=str(exc))
    try:
        jp_all = _parse(tools["join_path"]("", DB))
        rec("join_path_all_fintech",
            isinstance(jp_all, dict) and jp_all.get("n_joins", 0) >= 4,
            preview=f"n_joins={jp_all.get('n_joins')} cards={len(jp_all.get('cards') or [])}")
    except Exception as exc:
        rec("join_path_all_fintech", False, error=str(exc))

    try:
        v = _parse(tools["verify_join"](
            "payments", "order_ref", "orders", "order_ref", DB,
        ))
        rec("verify_join_payments_orders",
            v.get("verdict") == "accept" and (v.get("children_per_parent_avg") or 0) > 1.5,
            preview=str(v)[:300])
    except Exception as exc:
        rec("verify_join_payments_orders", False, error=str(exc))
    try:
        bad = _parse(tools["verify_join"](
            "orders", "order_ref", "payments", "payment_ref", DB,
        ))
        rec("verify_join_rejects_bad_key",
            bad.get("verdict") == "reject",
            preview=str(bad)[:250])
    except Exception as exc:
        rec("verify_join_rejects_bad_key", False, error=str(exc))
    try:
        cc = _parse(tools["completeness_check"](DB, False))
        # After Phase 3 merge: SM measured edges win over label-only join_facts
        rec("completeness_merges_measured_sm_joins",
            isinstance(cc, dict)
            and "joins_unmeasured" in cc
            and (cc.get("n_joins") or 0) >= 4
            and not any("payments.order_ref" in (u or "") for u in (cc.get("joins_unmeasured") or [])),
            preview=f"ok={cc.get('ok')} unmeasured={cc.get('joins_unmeasured')} "
                    f"n_joins={cc.get('n_joins')}")
    except Exception as exc:
        rec("completeness_merges_measured_sm_joins", False, error=str(exc))

    return results


async def run_questions(questions: list[str], max_iters: int = 14) -> list[dict]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from openai import OpenAI
    from diracdata.config import settings_from_env
    from diracdata.models.factory import BUILT_IN_MODEL_PROFILES

    s = settings_from_env(ENV)
    api_key = (
        os.environ.get("FIREWORKS_API_KEY")
        or os.environ.get("DIRACDATA_FIREWORKS_API_KEY")
        or s.fireworks_api_key
    )
    fw = OpenAI(base_url="https://api.fireworks.ai/inference/v1", api_key=api_key)
    prof = BUILT_IN_MODEL_PROFILES.get(MODEL)
    model_id = prof.model if prof else MODEL

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    server = StdioServerParameters(
        command=str(ROOT / ".venv" / "bin" / "python"),
        args=["-m", "diracdata.mcps.catalog_main", "--catalog", CATALOG, "--env-file", ENV],
        env=env,
    )

    sys_prompt = catalog_instructions() + (
        f"\n\nCurrent catalog is '{CATALOG}'. Call use_database({DB!r}) before per-DB tools. "
        "Answer with concrete numbers. Never call tools that are not in the provided tool list."
    )

    outcomes = []
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            listed = await session.list_tools()
            tool_names = [t.name for t in listed.tools]
            print(f"[mcp] tools({len(tool_names)}): {tool_names}", flush=True)
            leaked = sorted(DEAD & set(tool_names))
            if leaked:
                raise RuntimeError(f"catalog MCP advertised schema-only tools: {leaked}")
            instr = getattr(init, "instructions", None) or ""
            leaked_instr = [n for n in DEAD if n in (instr or "")]
            print(f"[mcp] instructions_len={len(instr or '')} leaked={leaked_instr}", flush=True)

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

            for q in questions:
                print(f"\n=== Q: {q}", flush=True)
                messages = [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": q},
                ]
                answer = None
                tools_used = []
                dead_calls = []
                for _step in range(max_iters):
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
                            "function": {"name": tc.function.name,
                                         "arguments": tc.function.arguments},
                        } for tc in msg.tool_calls],
                    })
                    for tc in msg.tool_calls:
                        name = tc.function.name
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                        except Exception:
                            args = {}
                        tools_used.append(name)
                        if name in DEAD:
                            dead_calls.append(name)
                            print(f"  !! DEAD TOOL {name}", flush=True)
                            messages.append({
                                "role": "tool", "tool_call_id": tc.id,
                                "content": f"unknown tool {name} — not on catalog MCP",
                            })
                            continue
                        print(f"  >> {name}({json.dumps(args)[:140]})", flush=True)
                        result = await session.call_tool(name, args)
                        text = "".join(getattr(c, "text", "") or "" for c in result.content)[:6000]
                        print(f"     << {text[:180].replace(chr(10), ' ')}", flush=True)
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
                outcomes.append({
                    "question": q,
                    "answered": bool(answer),
                    "answer_preview": (answer or "")[:1200],
                    "tools_used": tools_used,
                    "dead_tool_calls": dead_calls,
                    "used_search_fabric": "search_fabric" in tools_used,
                    "used_data_check": "data_check" in tools_used,
                    "used_join_path": "join_path" in tools_used,
                    "used_describe_column": "describe_column" in tools_used,
                    "used_verify_join": "verify_join" in tools_used,
                })
    return outcomes


def _extract_rates(text: str) -> list[float]:
    """Pull plausible rate/share numbers from an answer (0–1 or 0–100%)."""
    import re
    rates = []
    for m in re.finditer(r"(?<![\d.])(0?\.\d{2,4}|0?\.\d)(?![\d%])", text or ""):
        try:
            v = float(m.group(1))
            if 0.05 <= v <= 0.99:
                rates.append(v)
        except ValueError:
            pass
    for m in re.finditer(r"(\d{1,3}(?:\.\d+)?)\s*%", text or ""):
        try:
            v = float(m.group(1)) / 100.0
            if 0.05 <= v <= 0.99:
                rates.append(v)
        except ValueError:
            pass
    return rates


def score_phase5(outcomes: list[dict]) -> dict:
    """Quantify LLM UAT vs known gold: order-grain SUCCESS ≈0.60–0.63; fan-out ≈0.78+.

    Baseline (pre Phase 1–4 catalog gaps): agents often reported ~78–87% SUCCESS by
    joining payments onto order grain before aggregating.
    """
    GOLD_ORDER_GRAIN = (0.55, 0.68)   # high_amount…geo_mismatch band
    BAD_FANOUT = (0.72, 0.95)

    scored = []
    for i, o in enumerate(outcomes):
        ans = o.get("answer_preview") or ""
        rates = _extract_rates(ans)
        in_gold = [r for r in rates if GOLD_ORDER_GRAIN[0] <= r <= GOLD_ORDER_GRAIN[1]]
        in_bad = [r for r in rates if BAD_FANOUT[0] <= r <= BAD_FANOUT[1]]
        row = {
            "i": i,
            "question_head": (o.get("question") or "")[:60],
            "answered": o.get("answered"),
            "tools": {
                "data_check": o.get("used_data_check"),
                "join_path": o.get("used_join_path"),
                "describe_column": o.get("used_describe_column"),
                "search_fabric": o.get("used_search_fabric"),
            },
            "rates_found": rates[:12],
            "order_grain_rates": in_gold,
            "fanout_band_rates": in_bad,
        }
        if i == 0:  # fraud rule SUCCESS shares
            row["success_share_verdict"] = (
                "order_grain_ok" if in_gold and not in_bad else
                "fanout_suspected" if in_bad and not in_gold else
                "mixed_or_unclear"
            )
            row["ok"] = row["success_share_verdict"] == "order_grain_ok"
        elif i == 4:  # nested access recipe
            low = ans.lower()
            row["ok"] = ("unnest" in low) and (
                o.get("used_describe_column") or "fraud_signals" in low
            )
            row["mentions_unnest"] = "unnest" in low
        else:
            row["ok"] = bool(o.get("answered")) and not o.get("dead_tool_calls")
        scored.append(row)

    fraud = next((s for s in scored if s["i"] == 0), None)
    return {
        "baseline_pre_phases": {
            "fraud_success_share_typical": "0.78–0.87 (payment fan-out onto order grain)",
            "describe_column_recipe": "missing (engine type only)",
            "data_check_join_path": "absent on catalog MCP",
            "verify_join_on_propose": "absent; completeness accepted label-only joins",
        },
        "gold_targets": {
            "fraud_success_share_order_grain": "≈0.60–0.63 per rule",
            "describe_column": "access_recipe + verified runnable_example",
            "join_path": "children/parent≈1.73 + aggregate-then-join warning",
            "data_check": "flags fan-out SQL; clears aggregate-then-join",
        },
        "questions_scored": scored,
        "fraud_q_ok": bool(fraud and fraud.get("ok")),
        "n_answered": sum(1 for o in outcomes if o.get("answered")),
        "n_used_data_check": sum(1 for o in outcomes if o.get("used_data_check")),
        "n_used_join_path": sum(1 for o in outcomes if o.get("used_join_path")),
        "n_used_describe_column": sum(1 for o in outcomes if o.get("used_describe_column")),
        "n_dead_tools": sum(1 for o in outcomes if o.get("dead_tool_calls")),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-questions", action="store_true")
    ap.add_argument("--max-iters", type=int, default=14)
    ap.add_argument("--limit-questions", type=int, default=0)
    args = ap.parse_args()

    t0 = time.time()
    tools, _rt = _runtime_tools()
    gates = run_deterministic_gates(tools)

    outcomes = []
    if not args.skip_questions:
        import asyncio
        qs = QUESTIONS
        if args.limit_questions:
            qs = qs[: args.limit_questions]
        outcomes = asyncio.run(run_questions(qs, max_iters=args.max_iters))

    dead_q = [o for o in outcomes if o.get("dead_tool_calls")]
    phase5 = score_phase5(outcomes) if outcomes else {}
    summary = {
        "catalog": CATALOG,
        "database": DB,
        "elapsed_s": round(time.time() - t0, 1),
        "gates": gates,
        "questions": outcomes,
        "phase5_score": phase5,
        "gate_failures": [g for g in gates if not g.get("ok")],
        "unanswered": [o["question"] for o in outcomes if not o.get("answered")],
        "dead_tool_questions": [o["question"] for o in dead_q],
    }
    out_path = ROOT / "scratch_logs" / "uat_catalog_fintech_mcp.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nWrote {out_path}", flush=True)
    print(
        f"gate_failures={len(summary['gate_failures'])} "
        f"unanswered={len(summary['unanswered'])} "
        f"dead_tool_questions={len(summary['dead_tool_questions'])}",
        flush=True,
    )
    if phase5:
        print(
            f"phase5 fraud_q_ok={phase5.get('fraud_q_ok')} "
            f"data_check={phase5.get('n_used_data_check')}/"
            f"{phase5.get('n_answered')} "
            f"join_path={phase5.get('n_used_join_path')} "
            f"describe_column={phase5.get('n_used_describe_column')}",
            flush=True,
        )
    fail = bool(summary["gate_failures"] or summary["unanswered"] or summary["dead_tool_questions"])
    if outcomes and not phase5.get("fraud_q_ok"):
        fail = True
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
