# data_harness/

Local, laptop-scale fintech lakehouse simulator. Produces the substrate the
**AI Data Modeller** consumes: raw domain events → silver → gold, plus a live
query workload with real DuckDB execution telemetry, plus a lineage graph.

**Fully isolated from `src/diracdata/`** — no imports either direction. The two
share only the MinIO instance (disjoint prefixes).

## Read this first

- `PLAN.md` — full design doc (domains, tables, silver/gold, lineage, phase plan)
- `config.yaml` — all runtime knobs (rates, caps, retention, seeds) — no magic
  numbers anywhere else in the harness

## Layout

```
data_harness/
  PLAN.md · config.yaml · README.md      ← start here
  common/       DuckDB conn, MinIO client, paths, logging
  generators/   per-domain data pumps (Faker + seeded state)
  writers/      buffered parquet write to lake/fintech/raw/…
  transforms/
    silver/     one .sql per silver table (13)
    gold/       one .sql per gold table (7, incl. g_lending_health_daily w/ 90d EMI lookback)
  workload/     query bank + runner + telemetry
  orchestrator/ lineage parse + topo DAG + main run.py
  schemas/      raw.yaml · silver.yaml · gold.yaml (declared shapes)
  scripts/      thin CLIs: pump_once · build_silver · build_gold · full_dag · status · reset
  outputs/      local scratch (gitignored)
```

Runtime output lands in MinIO under `lake/fintech/{raw,silver,gold,reference,query_history}/`
plus `lake/fintech/lineage.json` (single source of lineage truth, auto-regenerated).

## Once Phase 1 is built you will run

```bash
# one full DAG pass (reference → raw → silver → gold → 100 queries)
python -m data_harness.orchestrator.run --mode one_pass

# continuous mode (default 60s tick, apscheduler loop)
python -m data_harness.orchestrator.run --mode continuous --interval 60s

# fast-forward 30 days of history in ~1 hour
python -m data_harness.orchestrator.run --mode backfill --days 30

# inspect current lake state
python -m data_harness.scripts.status
```

Nothing above works yet — this is Phase 0 (plan + scaffold). See PLAN.md §13
for the phase-by-phase build plan.

## Guardrails baked in

- 8 GB disk cap on `lake/fintech/*` — generators pause if exceeded
- 30-day rolling retention on raw (older partitions dropped nightly)
- 30 s / 2 GB kill switch on any single query (killed queries are logged, not silent)
- Seeded Faker throughout — full reproducibility from `config.yaml:seeds.master`

## Not in scope for this folder

- The AI Data Modeller itself (Phase 7+, will live under `src/diracdata/modeller/`)
- The MCP surface that exposes the modeller (separate `mcps/` server)
- Any prod-ish streaming, orchestration, or federated storage
