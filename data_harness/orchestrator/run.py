"""Main entrypoint: python -m data_harness.orchestrator.run

Modes:
  --mode one_pass       (default) — one tick through the pipeline, exit
  --mode continuous     — apscheduler loop, tick every --interval
  --mode backfill       — fast-forward --days days of history

Stages (comma-separated, applied in order):
  reference, raw, silver, gold, queries, lineage
"""
from __future__ import annotations
import argparse
import importlib
import sys
import time
from pathlib import Path
from typing import Dict, List

# Ensure data_harness is importable regardless of cwd
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from data_harness.common.config import load_config
from data_harness.common.duckdb_conn import make_duckdb
from data_harness.common.minio_client import make_s3
from data_harness.common.logging import log
from data_harness.orchestrator.guardrails import check_disk_budget


# Ordered domains — new ones added in Phase 2
_DOMAIN_GENERATORS = [
    # Order matters — cross-domain pools populated in this sequence per tick:
    ("users",      "data_harness.generators.users",      "UsersGenerator"),
    ("merchants",  "data_harness.generators.merchants",  "MerchantsGenerator"),
    ("checkouts",  "data_harness.generators.checkouts",  "CheckoutsGenerator"),
    ("orders",     "data_harness.generators.orders",     "OrdersGenerator"),
    ("payments",   "data_harness.generators.payments",   "PaymentsGenerator"),
    ("lending",    "data_harness.generators.lending",    "LendingGenerator"),
    ("adtech",     "data_harness.generators.adtech",     "AdtechGenerator"),
    ("risk",       "data_harness.generators.risk",       "RiskGenerator"),
]


def _load_generator(module_path: str, class_name: str):
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


def stage_reference(cfg, s3):
    from data_harness.generators.reference import seed_reference
    log.info("stage.start", stage="reference")
    stats = seed_reference(cfg, s3)
    log.info("stage.done", stage="reference", tables=list(stats.keys()))


def stage_raw(cfg, s3):
    from data_harness.writers.parquet_writer import write_raw
    log.info("stage.start", stage="raw", domains=[d for d, _, _ in _DOMAIN_GENERATORS])
    for domain, mod_path, cls in _DOMAIN_GENERATORS:
        if not check_disk_budget(cfg, s3):
            log.warn("stage.paused", stage="raw", reason="disk_budget")
            return
        Gen = _load_generator(mod_path, cls)
        gen = Gen(cfg, s3)
        t0 = time.perf_counter()
        emitted = gen.emit_tick()
        for table_name, tbl in emitted.items():
            if tbl.num_rows == 0:
                continue
            parts = write_raw(s3, cfg, domain, table_name, tbl)
            total_rows = sum(p["rows"] for p in parts)
            total_bytes = sum(p["bytes"] for p in parts)
            log.info("raw.write", domain=domain, table=table_name,
                     rows=total_rows, bytes=total_bytes, partitions=len(parts))
        gen.save_state()
        log.info("domain.tick.done", domain=domain,
                 elapsed_ms=int((time.perf_counter() - t0) * 1000))
    log.info("stage.done", stage="raw")


def stage_silver(cfg, con):
    from data_harness.transforms.runner import run_layer
    log.info("stage.start", stage="silver")
    results = run_layer(cfg, con, "silver")
    ok = sum(1 for r in results if r.get("status") == "success")
    log.info("stage.done", stage="silver", ok=ok, total=len(results))


def stage_gold(cfg, con):
    from data_harness.transforms.runner import run_layer
    log.info("stage.start", stage="gold")
    results = run_layer(cfg, con, "gold")
    ok = sum(1 for r in results if r.get("status") == "success")
    log.info("stage.done", stage="gold", ok=ok, total=len(results))


def stage_queries(cfg, con, s3):
    try:
        from data_harness.workload.runner import run_workload_tick
    except ImportError:
        log.info("stage.skip", stage="queries", reason="workload_runner_not_built_yet")
        return
    log.info("stage.start", stage="queries")
    n = run_workload_tick(cfg, con, s3)
    log.info("stage.done", stage="queries", queries=n)


def stage_lineage(cfg, con, s3):
    from data_harness.orchestrator.lineage import build_lineage
    log.info("stage.start", stage="lineage")
    lineage = build_lineage(cfg, con, s3)
    log.info("stage.done", stage="lineage",
             reference=len(lineage["reference"]), raw=len(lineage["raw"]),
             silver=len(lineage["silver"]), gold=len(lineage["gold"]),
             edges=len(lineage["edges"]))


def run_once(cfg, con, s3, stages: List[str]):
    if "reference" in stages: stage_reference(cfg, s3)
    if "raw"       in stages: stage_raw(cfg, s3)
    if "silver"    in stages: stage_silver(cfg, con)
    if "gold"      in stages: stage_gold(cfg, con)
    if "queries"   in stages: stage_queries(cfg, con, s3)
    if "lineage"   in stages: stage_lineage(cfg, con, s3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["one_pass", "continuous", "backfill"], default="one_pass")
    ap.add_argument("--interval", default="60s", help="continuous mode tick interval, e.g. 60s / 5m")
    ap.add_argument("--days", type=int, default=30, help="backfill mode: simulated days")
    ap.add_argument("--stages", default="reference,raw,silver,gold,queries,lineage")
    ap.add_argument("--config", default=None, help="path to config.yaml (defaults to data_harness/config.yaml)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    con = make_duckdb(cfg)
    s3  = make_s3(cfg)
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]

    log.info("harness.start", mode=args.mode, stages=stages,
             lake=f"s3://{cfg.bucket}/{cfg.root_prefix}/")

    if args.mode == "one_pass":
        run_once(cfg, con, s3, stages)

    elif args.mode == "continuous":
        # Simple loop with sleep — apscheduler adds complexity we don't need yet.
        # Parse interval — supports Ns, Nm, Nh
        s = args.interval.strip().lower()
        if s.endswith("h"):   delay = int(s[:-1]) * 3600
        elif s.endswith("m"): delay = int(s[:-1]) * 60
        elif s.endswith("s"): delay = int(s[:-1])
        else:                 delay = int(s)
        log.info("harness.continuous", interval_seconds=delay)
        try:
            while True:
                run_once(cfg, con, s3, stages)
                time.sleep(delay)
        except KeyboardInterrupt:
            log.info("harness.stopped", reason="sigint")

    elif args.mode == "backfill":
        log.info("harness.backfill", days=args.days,
                 ticks=args.days * 24)
        # Emit one tick per simulated hour
        for i in range(args.days * 24):
            run_once(cfg, con, s3, stages)
            if (i + 1) % 24 == 0:
                log.info("harness.backfill.progress", days_done=(i + 1) // 24)

    log.info("harness.exit")


if __name__ == "__main__":
    main()
