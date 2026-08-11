#!/usr/bin/env python3
"""Two targeted UATs:
  A) ROUTER GARDEN — run a query with the router ON over the no-Sonnet garden; report which model the
     router picked, and force an escalation path by pinning a deliberately weak first choice.
  B) EXPERIENTIAL MEMORY READ-BACK — turn 1 in conversation M1 establishes a binding (return rate for
     opted-in shoppers); the async curator writes it to experiences.md; turn 2 in a FRESH conversation
     M2 asks a vaguer version and we check whether the memory was consulted / the binding reused.

Hard 6-min alarm per case so nothing hangs. Writes UAT_router_memory_v3.csv."""
from __future__ import annotations
import csv, signal, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
LOG = ROOT / "scratch_logs" / "uat_v3"
LOG.mkdir(parents=True, exist_ok=True)
GARDEN = "fireworks_deepseek_v4_flash,fireworks_glm_5p2,fireworks_gpt_oss_120b,fireworks_kimi_k2p7_code"


class _TO(Exception): ...
def _alarm(*a): raise _TO("6-min cap")


def _run(label, schema, question, *, router, garden, conversation, memory, cap=360):
    from diracdata.agents import data_analyst
    from diracdata.model_providers import FireworksAI
    log = LOG / f"{label}.log"
    signal.signal(signal.SIGALRM, _alarm); signal.alarm(cap)
    t0 = time.time()
    try:
        a = data_analyst(schema=schema, model=FireworksAI("deepseek-v4-flash"),
                         env_file=str(ROOT / ".env"), router=router,
                         garden=(garden.split(",") if garden else None),
                         conversation=conversation, memory=memory, stream=False)
        ans = a.run(question)
        signal.alarm(0)
        el = time.time() - t0
        text = ans.answer or ""
        # try to surface which model(s) the router used, if the result exposes it
        picked = getattr(ans, "model", None) or getattr(ans, "route", None) or getattr(ans, "models_used", None)
        log.write_text(f"Q: {question}\n\nA: {text}\n\nPERF steps={ans.steps} tokens={ans.tokens} "
                       f"elapsed={el:.0f}s picked={picked}\n")
        return {"case": label, "schema": schema, "steps": ans.steps, "tokens": ans.tokens,
                "elapsed_s": round(el, 1), "picked": str(picked)[:60],
                "answer_preview": text[:200].replace("\n", " ")}
    except _TO:
        signal.alarm(0)
        log.write_text(f"Q: {question}\n\nTIMEOUT after {cap}s\n")
        return {"case": label, "schema": schema, "steps": -1, "tokens": -1,
                "elapsed_s": cap, "picked": "TIMEOUT", "answer_preview": "TIMEOUT"}
    except Exception as exc:  # noqa: BLE001
        signal.alarm(0)
        log.write_text(f"Q: {question}\n\nEXCEPTION: {type(exc).__name__}: {exc}\n")
        return {"case": label, "schema": schema, "steps": -1, "tokens": -1,
                "elapsed_s": round(time.time() - t0, 1), "picked": "ERROR",
                "answer_preview": f"{type(exc).__name__}: {exc}"[:200]}


def main():
    rows = []
    # ---- A) ROUTER GARDEN ----
    print("[A] router garden — router ON, 4-model no-Sonnet garden", flush=True)
    rows.append(_run("A1-router-simple", "ecommerce",
                     "Which market has the most shoppers? Show the top 3.",
                     router=True, garden=GARDEN, conversation=None, memory=False))
    print(f"  A1 picked={rows[-1]['picked']} steps={rows[-1]['steps']} tokens={rows[-1]['tokens']}", flush=True)
    rows.append(_run("A2-router-hard", "ecommerce",
                     "Which product types have the fattest gross margins, and how does that vary by market?",
                     router=True, garden=GARDEN, conversation=None, memory=False))
    print(f"  A2 picked={rows[-1]['picked']} steps={rows[-1]['steps']} tokens={rows[-1]['tokens']}", flush=True)

    # ---- B) EXPERIENTIAL MEMORY READ-BACK ----
    print("[B] experiential memory — turn1 establishes binding, curator writes, turn2 fresh convo", flush=True)
    rows.append(_run("B1-mem-write", "retail_complex",
                     "Which product categories have the highest return rates for shoppers who opted into "
                     "email marketing? Define the return rate you use.",
                     router=False, garden=None, conversation="uat-mem-write-1", memory=True))
    print(f"  B1 steps={rows[-1]['steps']} tokens={rows[-1]['tokens']}", flush=True)
    # give the async curator time to flush to experiences.md
    print("  ...waiting 45s for async curator to write back...", flush=True)
    signal.signal(signal.SIGALRM, lambda *a: None); signal.alarm(0)
    time.sleep(45)
    rows.append(_run("B2-mem-read", "retail_complex",
                     "Remind me which categories have the worst returns among our email subscribers, and "
                     "what definition we settled on.",
                     router=False, garden=None, conversation="uat-mem-read-2", memory=True))
    print(f"  B2 steps={rows[-1]['steps']} tokens={rows[-1]['tokens']}", flush=True)

    out = ROOT / "UAT_router_memory_v3.csv"
    cols = ["case", "schema", "steps", "tokens", "elapsed_s", "picked", "answer_preview"]
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows: w.writerow(r)
    print(f"\n[UAT] wrote {out}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
