#!/usr/bin/env python3
"""V3 UAT runner. Runs every case against the wired data_analyst on this branch, streams to per-case
logs, grades correctness against a hard-coded ground truth (substring match on the key figures),
records steps/tokens/tool-mix, writes UAT_learning_agent_v3_context-pass.csv."""
from __future__ import annotations
import concurrent.futures as cf, csv, re, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
LOG = ROOT / "scratch_logs" / "uat_v3"
LOG.mkdir(parents=True, exist_ok=True)

# case_id, schema, capability, ground_truth_substrings (all must appear), forbidden_substrings, extra kwargs
CASES = [
    ("U1", "retail_analytics", "cold-simple",
     "Which US states have the most preferred clients? Show me the top 5.",
     ["TX", "2304"], [], {}),
    ("U2", "retail_analytics", "cold-medium-join",
     "What's the average online net_paid per line by client gender?",
     ["F", "M", "2295", "2296"], [], {}),
    ("U3", "retail_complex", "nested-map",
     "What is the split of merchandise sales by product material? Show the top 5.",
     ["wood", "steel", "plastic", "leather", "cotton"], [], {}),
    ("U4", "retail_complex", "nested-array",
     "How many products currently have at least one variant that is out of stock?",
     ["7359"], [], {}),
    ("U5a", "retail_complex", "continuity-turn1",
     "Show me total online net revenue by traffic source.",
     ["meta", "tiktok", "google", "direct", "email"], [],
     {"conversation_id": "uat-cont-1"}),
    ("U5b", "retail_complex", "continuity-turn2",
     "For the top source, break it down by browser.",
     ["chrome", "edge", "firefox", "safari"], [],
     {"conversation_id": "uat-cont-1"}),  # same convo -> must resolve 'top source' from turn1
    ("U6", "fintech_complex", "deep-nested",
     "Across all fulfillment shipments, how many distinct product SKUs were shipped?",
     ["4993", "4,993"], [], {}),
    ("U8", "ecommerce", "obscured-join",
     "Which country has the highest average order value? Show the top 3.",
     ["ALGERIA", "JORDAN", "UNITED STATES"], [], {}),
    ("U9", "ecommerce", "composite-join-margin",
     "Which product types give us the fattest gross margins? Show the top 5.",
     ["BURNISHED", "0.65", "TIN", "STEEL", "COPPER"], [], {}),
    ("U10", "ecommerce", "rca-yoy",
     "Which market had the weakest year-over-year revenue growth from 1997 to 1998, and is it a volume or a price problem? (Note: 1998 is partial.)",
     ["1997", "1998", "partial"], [], {}),
]


def run_case(cid, schema, cap, question, must_have, must_not, kwargs):
    from diracdata.agents import data_analyst
    from diracdata.model_providers import FireworksAI
    log = LOG / f"{cid}.log"
    t0 = time.time()
    a = data_analyst(schema=schema, model=FireworksAI("deepseek-v4-flash"),
                     env_file=str(ROOT / ".env"), router=False, stream=False,
                     conversation=kwargs.get("conversation_id"))
    try:
        ans = a.run(question)
        elapsed = time.time() - t0
        text = ans.answer or ""
        log.write_text(f"Q: {question}\n\nA: {text}\n\nPERF steps={ans.steps} tokens={ans.tokens} elapsed={elapsed:.0f}s\n")
        hits = sum(1 for s in must_have if s.lower() in text.lower())
        misses = [s for s in must_have if s.lower() not in text.lower()]
        bads = [s for s in must_not if s.lower() in text.lower()]
        acc = "PASS" if hits == len(must_have) and not bads else ("PARTIAL" if hits >= len(must_have)//2 else "FAIL")
        return {"case": cid, "schema": schema, "capability": cap,
                "verdict": acc, "hits": f"{hits}/{len(must_have)}", "missing": ";".join(misses)[:100],
                "steps": ans.steps, "tokens": ans.tokens, "elapsed_s": round(elapsed, 1),
                "answer_preview": text[:180].replace("\n", " ")}
    except Exception as exc:  # noqa: BLE001
        elapsed = time.time() - t0
        log.write_text(f"Q: {question}\n\nEXCEPTION: {type(exc).__name__}: {exc}\n")
        return {"case": cid, "schema": schema, "capability": cap, "verdict": "ERROR",
                "hits": "0", "missing": f"{type(exc).__name__}: {exc}"[:100],
                "steps": 0, "tokens": 0, "elapsed_s": round(elapsed, 1), "answer_preview": ""}


def main():
    # U5a must complete BEFORE U5b (same conversation id must accumulate)
    sequential = [c for c in CASES if c[0] in ("U5a", "U5b")]
    parallel = [c for c in CASES if c[0] not in ("U5a", "U5b")]

    print(f"[UAT] running {len(parallel)} parallel + {len(sequential)} sequential cases", flush=True)
    results = {}
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(run_case, *c): c[0] for c in parallel}
        for fu in cf.as_completed(futs):
            r = fu.result()
            results[r["case"]] = r
            print(f"  {r['case']} {r['verdict']}  steps={r['steps']} tokens={r['tokens']} elapsed={r['elapsed_s']}s", flush=True)
    for c in sequential:
        r = run_case(*c)
        results[r["case"]] = r
        print(f"  {r['case']} {r['verdict']}  steps={r['steps']} tokens={r['tokens']} elapsed={r['elapsed_s']}s", flush=True)

    out = ROOT / "UAT_learning_agent_v3_context-pass.csv"
    cols = ["case", "schema", "capability", "verdict", "hits", "missing",
            "steps", "tokens", "elapsed_s", "answer_preview"]
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for cid, _, _, _, _, _, _ in CASES:
            if cid in results: w.writerow({k: results[cid].get(k, "") for k in cols})
    print(f"\n[UAT] wrote {out}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
