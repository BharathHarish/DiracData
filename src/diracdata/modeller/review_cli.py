"""dirac-modeller review CLI — human-in-loop over the proposals.

Subcommands:
  list [--status STATUS] [--limit N] [--since-days N]
  show  <proposal_id>
  approve <proposal_id> [--reason "..."]
  reject  <proposal_id> --reason "..."      (reason REQUIRED)
  supersede <old_id> <new_id> [--reason "..."]
  withdraw <proposal_id> [--reason "..."]
  decisions [--since-days N]

All decisions are appended to the proposal's `decisions[]` array via
mark_proposal — no destructive edits. The next modeller round reads
`recent_decisions()` and the curator writes experiences accordingly.

This CLI itself is pure plumbing — no judgement.
"""
from __future__ import annotations
import argparse
import json
import sys
import textwrap
from typing import Optional
from .config import load_config
from .connections import make_s3
from . import read_tools as R
from . import write_tools as W
from . import ledger as L


# ---------- pretty-print helpers ----------

def _fmt_ms(ms):
    if ms is None: return "?"
    if ms >= 1000: return f"{ms/1000:.1f}s"
    return f"{ms}ms"


def _fmt_days(d):
    if d is None: return "?"
    if d < 1: return f"{d*24:.1f}h"
    return f"{d:.1f}d"


def _fmt_bytes(n):
    if n is None: return "?"
    for u in ["B","KB","MB","GB"]:
        if n < 1024: return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def _status_glyph(status: str) -> str:
    return {
        "pending_review": "●",
        "approved":       "✔",
        "rejected":       "✘",
        "superseded":     "→",
        "withdrawn":      "-",
    }.get(status, "?")


# ---------- subcommands ----------

def cmd_list(args):
    cfg = load_config(); s3 = make_s3(cfg)
    idx = L.proposal_index(cfg, s3)
    if args.status:
        idx = [p for p in idx if p.get("status") == args.status]
    if args.since_days is not None:
        idx = [p for p in idx if (p.get("days_ago") or 0) <= args.since_days]
    idx = idx[: args.limit]
    if not idx:
        print("(no matching proposals)"); return 0
    print(f"{'':2} {'proposal_id':<38s} {'status':<15s} {'target':<40s} {'grain':<30s} {'age':>7s} {'conf':>5s} {'saving':>8s}")
    for p in idx:
        print(f"{_status_glyph(p.get('status','?')):>2} "
              f"{p.get('proposal_id',''):<38s} "
              f"{p.get('status',''):<15s} "
              f"{(p.get('target_name') or '')[:40]:<40s} "
              f"{(p.get('grain_key') or '')[:30]:<30s} "
              f"{_fmt_days(p.get('days_ago')):>7s} "
              f"{str(p.get('confidence','?')):>5s} "
              f"{_fmt_ms(p.get('projected_saving_ms')):>8s}")
    print(f"\n{len(idx)} proposals shown.")
    return 0


def cmd_show(args):
    cfg = load_config(); s3 = make_s3(cfg)
    try:
        obj = s3.get_object(Bucket=cfg.bucket, Key=f"{cfg.proposals_prefix}{args.proposal_id}.json")
        p = json.loads(obj["Body"].read())
    except Exception as ex:
        print(f"NOT FOUND: {args.proposal_id} ({ex})", file=sys.stderr); return 1

    if args.json:
        print(json.dumps(p, indent=2, default=str)); return 0

    print(f"\n{'='*80}")
    print(f"  {_status_glyph(p.get('status','?'))} {p.get('proposal_id')}  ({p.get('status')})")
    print(f"{'='*80}")
    print(f"  target:        {p.get('target_name')}")
    print(f"  kind:          {p.get('kind')}")
    print(f"  engine:        {p.get('engine')}")
    print(f"  grain:         {p.get('grain')}")
    print(f"  sources:       {p.get('sources')}")
    print(f"  confidence:    {p.get('confidence')}")
    print(f"  created_at:    {p.get('created_at')}")
    if p.get("layout"):
        print(f"  layout:        {json.dumps(p['layout'])}")
    if p.get("optimisations"):
        print(f"  optimisations: {json.dumps(p['optimisations'])}")
    ev = p.get("evidence") or {}
    if ev:
        print(f"\n  EVIDENCE:")
        for k, v in ev.items():
            if k == "agent_rationale":
                print(f"    {k}:")
                for line in textwrap.wrap(str(v), width=76):
                    print(f"      {line}")
            elif isinstance(v, (list, dict)):
                print(f"    {k}: {json.dumps(v, default=str)}")
            else:
                print(f"    {k}: {v}")
    print(f"\n  SQL:")
    for line in (p.get("sql_body","") or "").splitlines():
        print(f"    {line}")
    decisions = p.get("decisions") or []
    if decisions:
        print(f"\n  DECISIONS ({len(decisions)}):")
        for d in decisions:
            print(f"    - [{d.get('at')}] {d.get('decision')}  reason: {d.get('reason','')}")
    print()
    return 0


def cmd_approve(args):
    cfg = load_config(); s3 = make_s3(cfg)
    r = W.mark_proposal(cfg, s3, args.proposal_id, "approved", args.reason or "")
    if r.get("status") == "ok":
        print(f"✔ approved {args.proposal_id}"); return 0
    print(f"ERROR: {r.get('error')}", file=sys.stderr); return 1


def cmd_reject(args):
    cfg = load_config(); s3 = make_s3(cfg)
    r = W.mark_proposal(cfg, s3, args.proposal_id, "rejected", args.reason)
    if r.get("status") == "ok":
        print(f"✘ rejected {args.proposal_id}\n  reason: {args.reason}\n"
              f"  (the next modeller round will read this via recent_decisions() and "
              f"the curator will persist any lesson to experiences.md)"); return 0
    print(f"ERROR: {r.get('error')}", file=sys.stderr); return 1


def cmd_supersede(args):
    cfg = load_config(); s3 = make_s3(cfg)
    reason = (args.reason or "") + f" (superseded by {args.new_id})"
    r = W.mark_proposal(cfg, s3, args.old_id, "superseded", reason)
    if r.get("status") == "ok":
        print(f"→ superseded {args.old_id} by {args.new_id}"); return 0
    print(f"ERROR: {r.get('error')}", file=sys.stderr); return 1


def cmd_withdraw(args):
    cfg = load_config(); s3 = make_s3(cfg)
    r = W.mark_proposal(cfg, s3, args.proposal_id, "withdrawn", args.reason or "")
    if r.get("status") == "ok":
        print(f"- withdrawn {args.proposal_id}"); return 0
    print(f"ERROR: {r.get('error')}", file=sys.stderr); return 1


def cmd_decisions(args):
    cfg = load_config(); s3 = make_s3(cfg)
    decs = L.recent_decisions(cfg, s3, since_days=args.since_days)
    if not decs:
        print("(no decisions on record)"); return 0
    print(f"{'':2} {'decided_at':<32s} {'decision':<12s} {'target':<40s} {'reason':<30s}")
    for d in decs:
        print(f"{_status_glyph(d.get('decision','?')):>2} "
              f"{(d.get('decided_at') or '')[:26]:<32s} "
              f"{d.get('decision',''):<12s} "
              f"{(d.get('target_name') or '')[:40]:<40s} "
              f"{(d.get('reason') or '')[:30]:<30s}")
    print(f"\n{len(decs)} decisions.")
    return 0


# ---------- entrypoint ----------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                  prog="dirac-modeller review")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="list proposals")
    p.add_argument("--status", default=None,
                   choices=[None, "pending_review", "approved", "rejected", "superseded", "withdrawn"])
    p.add_argument("--limit",  type=int, default=50)
    p.add_argument("--since-days", type=float, default=None)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show", help="show one proposal in full")
    p.add_argument("proposal_id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("approve", help="approve a proposal")
    p.add_argument("proposal_id")
    p.add_argument("--reason", default="")
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("reject", help="reject a proposal (reason REQUIRED)")
    p.add_argument("proposal_id")
    p.add_argument("--reason", required=True, help="why (fed into next round's curator)")
    p.set_defaults(fn=cmd_reject)

    p = sub.add_parser("supersede", help="mark old proposal superseded by a new one")
    p.add_argument("old_id")
    p.add_argument("new_id")
    p.add_argument("--reason", default="")
    p.set_defaults(fn=cmd_supersede)

    p = sub.add_parser("withdraw", help="withdraw a pending proposal")
    p.add_argument("proposal_id")
    p.add_argument("--reason", default="")
    p.set_defaults(fn=cmd_withdraw)

    p = sub.add_parser("decisions", help="list past human decisions")
    p.add_argument("--since-days", type=float, default=None)
    p.set_defaults(fn=cmd_decisions)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
