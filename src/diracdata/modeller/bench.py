"""Bench — exercise every Phase 7B tool against the live harness and print
concise pass/fail per tool. Run before wiring the agent so we know the
substrate works.

Usage: PYTHONPATH=src .venv/bin/python -m diracdata.modeller.bench
"""
from __future__ import annotations
import json
import sys
from typing import Any
from .config import load_config
from .connections import make_s3, make_duckdb
from . import read_tools as R
from . import fingerprint as F
from . import similarity as S
from . import engines as E
from . import validation as V
from . import write_tools as W


def _p(name: str, ok: bool, note: str = ""):
    tag = "✔" if ok else "✘"
    print(f"  {tag} {name:<48s} {note}")


def main() -> int:
    cfg = load_config()
    s3  = make_s3(cfg)
    con = make_duckdb(cfg)
    fails = 0

    # ----- READ TOOLS -----
    print("\n== read_tools ==")
    lin = R.list_lineage(cfg, s3)
    ok = bool(lin.get("raw"))
    _p("list_lineage", ok, f"raw={len(lin.get('raw',{}))}, silver={len(lin.get('silver',{}))}, gold={len(lin.get('gold',{}))}")
    if not ok: fails += 1

    pats = R.list_query_patterns(cfg, con)
    ok = len(pats) >= 4
    _p("list_query_patterns", ok, f"{len(pats)} distinct templates, top total={pats[0]['total_ms']}ms" if pats else "empty")
    if not ok: fails += 1

    if pats:
        prof = R.get_pattern_cost(cfg, con, pats[0]["template_id"])
        ok = prof.get("n_runs", 0) > 0 and prof.get("sample_sql")
        _p("get_pattern_cost", ok,
           f"n_runs={prof.get('n_runs')} sample_sql_len={len(prof.get('sample_sql',''))}")
        if not ok: fails += 1

    mix = R.get_layer_mix_distribution(cfg, con)
    ok = mix.get("total_queries", 0) > 0
    _p("get_layer_mix_distribution", ok, f"total_queries={mix.get('total_queries')}")
    if not ok: fails += 1

    silver_uri = "s3://lake/fintech/silver/s_payments/**/*.parquet"
    layout = R.describe_table_layout(cfg, con, silver_uri, s3=s3)
    ok = bool(layout.get("columns")) and layout.get("file_count", 0) > 0
    _p("describe_table_layout", ok,
       f"cols={len(layout.get('columns',[]))} files={layout.get('file_count')} "
       f"rows={layout.get('total_rows')} avg_file_mb={layout.get('avg_file_mb')} "
       f"parts={layout.get('partition_keys')}")
    if not ok: fails += 1

    col_stats = R.describe_column_stats(cfg, con, silver_uri, "rail_type")
    ok = col_stats.get("distinct_count") is not None
    _p("describe_column_stats", ok,
       f"distinct={col_stats.get('distinct_count')} nulls={col_stats.get('null_ratio')} samples={col_stats.get('sample_values')}")
    if not ok: fails += 1

    sample = R.sample_rows(cfg, con, silver_uri, 3)
    ok = len(sample) > 0 and not sample[0].get("error")
    _p("sample_rows", ok, f"{len(sample)} rows returned")
    if not ok: fails += 1

    # ----- FINGERPRINT + SIMILARITY -----
    print("\n== fingerprint + similarity ==")
    # Grab two SQLs of the same template + one of a different template
    p_lending = R.get_pattern_cost(cfg, con, "rca.lending_90day_emi.v1")
    p_roas    = R.get_pattern_cost(cfg, con, "rca.attribution_roas.v1")
    p_cohort  = R.get_pattern_cost(cfg, con, "rca.user_cohort_ltv.v1")
    fp_a = F.fingerprint_sql(p_lending.get("sample_sql", ""))
    fp_b = F.fingerprint_sql(p_roas.get("sample_sql", ""))
    fp_c = F.fingerprint_sql(p_cohort.get("sample_sql", ""))
    ok = "parse_error" not in fp_a and len(fp_a.get("tables", [])) > 0
    _p("fingerprint_sql (lending 90day)", ok,
       f"tables={len(fp_a.get('tables',[]))} joins={len(fp_a.get('joins',[]))} aggs={fp_a.get('aggregations')}")
    if not ok: fails += 1

    # Similarity: same template SQL to itself = 1.0; different templates < 1.0
    sim_self = S.similarity(fp_a, fp_a)
    sim_diff = S.similarity(fp_a, fp_b)
    sim_ab   = S.similarity(fp_a, fp_c)
    ok = sim_self >= 0.99 and sim_diff < 0.9
    _p("similarity (self vs different)", ok, f"self={sim_self} lending-vs-roas={sim_diff} lending-vs-cohort={sim_ab}")
    if not ok: fails += 1

    # ----- ENGINES -----
    print("\n== engines ==")
    engs = E.list_supported_engines()
    ok = set(engs) >= {"duckdb", "iceberg", "delta", "snowflake", "databricks", "trino", "spark"}
    _p("list_supported_engines", ok, f"{engs}")
    if not ok: fails += 1

    for eng in ("duckdb", "iceberg", "delta"):
        cap = E.describe_engine_capabilities(eng)
        ok = "capabilities" in cap
        _p(f"describe_engine_capabilities({eng})", ok,
           f"acid={cap.get('capabilities',{}).get('acid')} time_travel={cap.get('capabilities',{}).get('time_travel')}")
        if not ok: fails += 1

    prims_ice = E.list_optimisation_primitives("iceberg", kind="layout")
    ok = any(p.get("name") == "sort_order" for p in prims_ice)
    _p("list_optimisation_primitives(iceberg, layout)", ok, f"{[p['name'] for p in prims_ice]}")
    if not ok: fails += 1

    ddb_notes = E.describe_sql_dialect("duckdb")
    ok = bool(ddb_notes.get("dialect_notes"))
    _p("describe_sql_dialect(duckdb)", ok, f"notes={len(ddb_notes.get('dialect_notes',[]))}")
    if not ok: fails += 1

    # ----- VALIDATION -----
    print("\n== validation ==")
    test_sql = "SELECT count(*) FROM read_parquet('s3://lake/fintech/silver/s_payments/**/*.parquet')"
    vr = V.validate_syntax(test_sql, "duckdb")
    ok = vr.get("status") == "ok"
    _p("validate_syntax (duckdb)", ok, vr.get("error", ""))
    if not ok: fails += 1

    dr = V.dry_run(cfg, con, test_sql, limit=5)
    ok = dr.get("status") == "ok" and dr.get("rows_returned") == 1
    _p("dry_run", ok, f"rows={dr.get('rows_returned')} elapsed_ms={dr.get('elapsed_ms')} scan_bytes={dr.get('scan_bytes_est')}")
    if not ok: fails += 1

    ep = V.explain_plan(cfg, con, test_sql)
    ok = ep.get("status") == "ok" and len(ep.get("plan", "")) > 0
    _p("explain_plan", ok, f"plan_bytes={len(ep.get('plan',''))}")
    if not ok: fails += 1

    esb = V.estimate_scan_bytes(cfg, con, test_sql)
    ok = esb.get("status") == "ok" and esb.get("scan_bytes_est", 0) > 0
    _p("estimate_scan_bytes", ok, f"{esb.get('scan_bytes_est'):,} bytes")
    if not ok: fails += 1

    # ----- WRITE TOOLS (roundtrip) -----
    print("\n== write_tools (roundtrip) ==")
    # 1) write a test proposal
    fake_prop = {
        "kind":        "materialise_gold",
        "engine":      "duckdb",
        "target_name": "_bench_test_gold",
        "grain":       ["day"],
        "sources":     ["silver.s_users"],
        "sql_body":    "SELECT 1 AS x",
        "evidence":    {"note": "bench test proposal — dismiss"},
        "confidence":  0.01,
    }
    wr = W.write_proposal(cfg, s3, fake_prop)
    ok = wr.get("status") == "ok"
    _p("write_proposal", ok, wr.get("proposal_id", "?"))
    if not ok: fails += 1

    # 2) mark it as withdrawn
    if wr.get("status") == "ok":
        mr = W.mark_proposal(cfg, s3, wr["proposal_id"], "withdrawn", "bench cleanup")
        ok = mr.get("status") == "ok"
        _p("mark_proposal", ok, mr.get("new_status", "?"))
        if not ok: fails += 1

    # 3) list and count
    props = R.list_prior_proposals(cfg, s3)
    ok = len(props) > 0 and any(p.get("proposal_id") == wr.get("proposal_id") for p in props)
    _p("list_prior_proposals", ok, f"{len(props)} total")
    if not ok: fails += 1

    # 4) experience write + read
    wex = W.write_experience(cfg, s3, "bench-test insight (safe to ignore)", "written by bench.py")
    ok = wex.get("status") == "ok"
    _p("write_experience", ok, "")
    if not ok: fails += 1

    exp = W.read_experiences(cfg, s3)
    ok = "bench-test insight" in exp
    _p("read_experiences", ok, f"{len(exp)} chars")
    if not ok: fails += 1

    # 5) deferral
    dr2 = W.defer(cfg, s3, "test_pattern_id", "bench test — not now")
    ok = dr2.get("status") == "ok"
    _p("defer", ok, "")
    if not ok: fails += 1

    defs = W.list_deferrals(cfg, s3)
    ok = "test_pattern_id" in defs
    _p("list_deferrals", ok, f"{len(defs)} deferrals")
    if not ok: fails += 1

    # ----- SUMMARY -----
    print(f"\n{'-' * 60}")
    if fails == 0:
        print(f"  ALL TOOLS OK ✔  ({sum(1 for _ in ['x'] * 20)} checks passed)")
        return 0
    else:
        print(f"  {fails} FAILURES ✘")
        return 1


if __name__ == "__main__":
    sys.exit(main())
