"""dirac-modeller inspect — the Phase 7A read-side CLI.

Prints:
  - lineage summary (reference/raw/silver/gold counts + edges)
  - top-N expensive patterns from query_history
  - layer_mix distribution (where analysts spend cost)
  - existing proposals (if any)

No writes. No LLM calls. Deterministic. Meant to verify the read path works
end-to-end before we add the drafter (Phase 7C).
"""
from __future__ import annotations
import argparse
import json
import sys
from .config import load_config
from .connections import make_s3, make_duckdb
from .read_tools import (list_lineage, list_query_patterns, get_pattern_cost,
                         get_layer_mix_distribution, list_prior_proposals, gold_table_names)


def _fmt_ms(ms: int) -> str:
    if ms >= 1000: return f"{ms/1000:.1f}s"
    return f"{ms}ms"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=10, help="show top-N patterns by total cost")
    ap.add_argument("--archetype", default=None, help="filter to one archetype (bi/analyst/ops/rca)")
    ap.add_argument("--since-days", type=int, default=None, help="only queries in the last N days")
    ap.add_argument("--min-cost-ms", type=int, default=None, help="only patterns with avg cost >= this")
    ap.add_argument("--detail", default=None, help="template_id — show full cost profile for one pattern")
    ap.add_argument("--json", action="store_true", help="emit raw JSON instead of pretty print")
    args = ap.parse_args()

    cfg = load_config()
    s3  = make_s3(cfg)
    con = make_duckdb(cfg)

    if args.detail:
        profile = get_pattern_cost(cfg, con, args.detail)
        if args.json:
            print(json.dumps(profile, indent=2, default=str)); return 0
        print(f"\n=== {args.detail} ===")
        for k, v in profile.items():
            if k == "sample_sql":
                print(f"  sample_sql:\n    {v[:600]}...")
            elif k == "tables_touched":
                print(f"  tables_touched:")
                for t in v: print(f"    - {t}")
            elif k == "layer_mix":
                print(f"  layer_mix: raw={v['raw']:.1f} silver={v['silver']:.1f} gold={v['gold']:.1f}")
            else:
                print(f"  {k:20s} {v}")
        return 0

    # ------ lineage summary ------
    lineage = list_lineage(cfg, s3)
    ref  = len(lineage.get("reference", {}))
    raw  = len(lineage.get("raw", {}))
    silv = len(lineage.get("silver", {}))
    gold = len(lineage.get("gold", {}))
    edges = len(lineage.get("edges", []))
    generated = lineage.get("generated_at", "?")
    print(f"\n=== lineage @ {generated} ===")
    print(f"  reference: {ref:>3d}   raw: {raw:>3d}   silver: {silv:>3d}   gold: {gold:>3d}   edges: {edges}")
    if gold:
        print(f"  existing gold: {', '.join(gold_table_names(cfg, s3))}")

    # ------ workload distribution ------
    mix = get_layer_mix_distribution(cfg, con)
    if mix and mix.get("total_queries"):
        print(f"\n=== workload distribution ===")
        tq = mix["total_queries"] or 1
        tc = mix["total_cost_ms"] or 1
        print(f"  queries: {tq:,}   total cost: {_fmt_ms(tc)}")
        print(f"  hits — raw: {mix['queries_touching_raw']:>4d} ({100*mix['queries_touching_raw']/tq:.0f}%)"
              f"  silver: {mix['queries_touching_silver']:>4d} ({100*mix['queries_touching_silver']/tq:.0f}%)"
              f"  gold: {mix['queries_touching_gold']:>4d} ({100*mix['queries_touching_gold']/tq:.0f}%)")
        print(f"  cost  — raw: {_fmt_ms(mix['cost_ms_on_raw']):>7s} ({100*mix['cost_ms_on_raw']/tc:.0f}%)"
              f"  silver: {_fmt_ms(mix['cost_ms_on_silver']):>7s} ({100*mix['cost_ms_on_silver']/tc:.0f}%)"
              f"  gold: {_fmt_ms(mix['cost_ms_on_gold']):>7s} ({100*mix['cost_ms_on_gold']/tc:.0f}%)")

    # ------ top-N expensive patterns ------
    patterns = list_query_patterns(cfg, con, archetype=args.archetype,
                                   since_days=args.since_days, min_cost_ms=args.min_cost_ms)
    top = patterns[:args.top]
    if args.json:
        print(json.dumps(top, indent=2, default=str)); return 0

    if not top:
        print(f"\n(no query patterns matched)")
    else:
        print(f"\n=== top {len(top)} patterns by total cost ===")
        print(f"  {'template':<40s} {'arch':<8s} {'runs':>5s} {'avg':>7s} {'p95':>7s} {'total':>9s}  layers(r/s/g)")
        for p in top:
            lm = f"{int(p['avg_raw'])}/{int(p['avg_silver'])}/{int(p['avg_gold'])}"
            print(f"  {p['template_id']:<40s} {p['archetype']:<8s} "
                  f"{p['n_runs']:>5d} {_fmt_ms(p['avg_ms']):>7s} {_fmt_ms(p['p95_ms']):>7s} "
                  f"{_fmt_ms(p['total_ms']):>9s}  {lm}")

    # ------ existing proposals ------
    props = list_prior_proposals(cfg, s3)
    print(f"\n=== proposals ({len(props)}) ===")
    if not props:
        print("  none yet (Phase 7C will start writing them)")
    else:
        for p in props[:10]:
            print(f"  {p.get('proposal_id','?'):<25s} {p.get('status','?'):<20s} {p.get('target_name','?')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
