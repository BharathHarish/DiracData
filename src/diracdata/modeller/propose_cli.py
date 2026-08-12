"""dirac-modeller propose --now — run one modeller round, print trace + summary.

Usage:
  PYTHONPATH=src .venv/bin/python -m diracdata.modeller.propose_cli --now
  PYTHONPATH=src .venv/bin/python -m diracdata.modeller.propose_cli --now --model fireworks_gpt_oss_120b
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from .config import load_config, ModellerConfig
from .agent  import run_round


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--now",   action="store_true", help="run one round immediately (required)")
    ap.add_argument("--model", default=None, help="override chat model profile")
    ap.add_argument("--env-file", default=".env", help="env file for FIREWORKS_API_KEY etc.")
    ap.add_argument("--json",  action="store_true", help="emit summary as raw JSON")
    args = ap.parse_args()

    if not args.now:
        ap.print_help(); return 1

    # Load .env
    if os.path.exists(args.env_file):
        for ln in open(args.env_file):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln: continue
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

    # Verify API key present — .env uses DIRACDATA_FIREWORKS_API_KEY; alias to FIREWORKS_API_KEY
    if not os.environ.get("FIREWORKS_API_KEY"):
        alt = os.environ.get("DIRACDATA_FIREWORKS_API_KEY")
        if alt:
            os.environ["FIREWORKS_API_KEY"] = alt
    if not os.environ.get("FIREWORKS_API_KEY"):
        print("ERROR: FIREWORKS_API_KEY not set (check --env-file)", file=sys.stderr)
        return 2

    cfg = load_config()
    if args.model:
        cfg = ModellerConfig(**{**cfg.__dict__, "chat_model_profile": args.model})

    print(f"[modeller] round starting — model={cfg.chat_model_profile} "
          f"(budget: {cfg.max_run_tokens} tok / {cfg.max_run_seconds}s / "
          f"{cfg.max_proposals_per_run} proposals / {cfg.max_react_steps} steps)", file=sys.stderr, flush=True)

    summary = run_round(cfg)

    if args.json:
        print(json.dumps(summary, indent=2, default=str)); return 0

    print(f"\n=== round complete ({summary.get('status')}) ===")
    print(f"round_id: {summary.get('round_id')}")
    fp = summary.get("framing", {})
    print(f"\nFRAMING:  {fp.get('budget')}")
    hyp = fp.get("hypothesis", {})
    print(f"  focus_patterns: {hyp.get('focus_patterns', [])}")
    print(f"  round_intent:   {hyp.get('round_intent', '?')}")
    print(f"  engine_focus:   {hyp.get('engine_focus', '?')}")
    mp = summary.get("main", {})
    print(f"\nMAIN:     {mp.get('budget')}   finish_reason={mp.get('finish_reason', '?')}")

    props = summary.get("proposals", [])
    print(f"\nPROPOSALS ({len(props)}):")
    for p in props:
        print(f"  - {p.get('proposal_id')}: {p.get('target_name')} "
              f"[confidence={p.get('confidence')}, status={p.get('status')}]")
        if p.get("evidence", {}).get("agent_rationale"):
            print(f"      rationale: {p['evidence']['agent_rationale'][:200]}...")

    cur = summary.get("curator", {})
    print(f"\nCURATOR:  {cur.get('budget')}   finish_reason={cur.get('finish_reason', '?')}")

    print(f"\nAudit log: s3://{cfg.bucket}/{cfg.audit_prefix}{summary['round_id']}.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
