"""Workload runner — executes N queries through DuckDB with EXPLAIN ANALYZE,
captures real telemetry, appends to lake/fintech/query_history/…

Every query row includes: template_id, sql, elapsed_ms, rows_returned, tables_touched,
layer_mix (raw/silver/gold counts), archetype, user info, error status.
"""
from __future__ import annotations
import hashlib
import io
import random
import re
import time
from datetime import datetime, timezone
from typing import Dict, List
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from data_harness.common.config import Config
from data_harness.common.logging import log
from data_harness.common.minio_client import download_json
from data_harness.common.paths import (query_history_key, state_key, utc_now)
from data_harness.workload.query_bank import TEMPLATES, BankContext


_LAYER_RE = re.compile(r"s3://[^/]+/fintech/(raw|silver|gold|reference)/([^/'\"]+(?:/[^/'\"]+)?)")


def _extract_tables(sql: str) -> tuple[list[str], dict[str, int]]:
    """Scan SQL for read_parquet URIs → return (tables_touched, layer_mix)."""
    touched: set[str] = set()
    mix: dict[str, int] = {"raw": 0, "silver": 0, "gold": 0, "reference": 0}
    for m in _LAYER_RE.finditer(sql):
        layer, name = m.group(1), m.group(2)
        # For raw: name is domain/table; for others: just table
        tbl_ref = f"{layer}.{name.replace('/', '.')}"
        if tbl_ref not in touched:
            mix[layer] = mix.get(layer, 0) + 1
        touched.add(tbl_ref)
    return sorted(touched), mix


def _sql_hash(sql: str) -> str:
    return "sha1:" + hashlib.sha1(sql.strip().encode()).hexdigest()[:16]


_ROLE_WEIGHTS_KEY = "workload.user_role_weights"


def _pick_role(cfg: Config, rng: random.Random) -> str:
    weights = cfg.get(_ROLE_WEIGHTS_KEY, {"analyst": 1.0}) or {"analyst": 1.0}
    roles, ws = zip(*weights.items())
    return rng.choices(roles, weights=ws, k=1)[0]


def _pick_archetype(cfg: Config, rng: random.Random) -> str:
    weights = cfg.get("workload.archetype_weights", {"bi": 1.0})
    keys, ws = zip(*weights.items())
    return rng.choices(keys, weights=ws, k=1)[0]


def _load_pools(s3, cfg: Config) -> BankContext:
    shared = download_json(s3, cfg.bucket, state_key(cfg, "_shared_pools"), default=None) or {}
    return BankContext(
        rng          = random.Random(cfg.master_seed ^ 0xBEEF),
        merchant_ids = shared.get("merchant_ids", []),
        user_ids     = shared.get("user_ids", []),
        order_ids    = shared.get("order_ids", []),
        campaign_ids = shared.get("campaign_ids", []),
        loan_ids     = shared.get("loan_ids", []),
    )


_HISTORY_SCHEMA = pa.schema([
    ("query_id",         pa.string()),
    ("submitted_at",     pa.timestamp("us", tz="UTC")),
    ("user_id",          pa.string()),
    ("user_role",        pa.string()),
    ("archetype",        pa.string()),
    ("template_id",      pa.string()),
    ("sql_hash",         pa.string()),
    ("sql_text",         pa.string()),
    ("elapsed_ms",       pa.int64()),
    ("rows_returned",    pa.int64()),
    ("tables_touched",   pa.list_(pa.string())),
    ("layer_mix_raw",       pa.int32()),
    ("layer_mix_silver",    pa.int32()),
    ("layer_mix_gold",       pa.int32()),
    ("layer_mix_reference",  pa.int32()),
    ("status",           pa.string()),
    ("error",            pa.string()),
])


def _guardrail_kill(con: duckdb.DuckDBPyConnection, cfg: Config) -> None:
    """Best-effort per-connection budget hint. DuckDB doesn't have a hard timeout in-thread,
    but we can set a memory ceiling."""
    max_gb = float(cfg.get("guardrails.max_query_scan_gb", 2))
    con.execute(f"SET memory_limit = '{max(1, int(max_gb))}GB'")


def run_workload_tick(cfg: Config, con: duckdb.DuckDBPyConnection, s3) -> int:
    """Run one workload tick — queries_per_tick queries, all logged to query_history."""
    _guardrail_kill(con, cfg)
    ctx = _load_pools(s3, cfg)
    base_n   = int(cfg.get("workload.queries_per_tick", 20))
    mult     = float(cfg.get(f"workload.queries_per_tick_multipliers.{cfg.mode}", 1.0))
    n        = max(1, int(round(base_n * mult)))
    rng      = ctx.rng
    users_pool = [f"u_synth_{i:03d}" for i in range(int(cfg.get("workload.users_pool", 50)))]
    max_seconds = int(cfg.get("guardrails.max_query_seconds", 30))

    rows: List[dict] = []
    # ---- discovery-target injection ----
    # Guarantee every §8B discovery-target template fires ≥1x per tick so the modeller
    # always has floor-level signal per §Decision #5 + config.workload.discovery_signal_min_daily.
    fixed_templates: list = [("rca", t) for t in TEMPLATES.get("rca", [])]
    sample_n = max(1, n - len(fixed_templates))
    sample = fixed_templates + [
        (_pick_archetype(cfg, rng), None) for _ in range(sample_n)
    ]
    for arch, template_fn in sample:
        if template_fn is None:
            template_fn = rng.choice(TEMPLATES[arch])
        try:
            template_id, sql = template_fn(ctx)
        except Exception as exc:
            log.warn("workload.template_error", error=str(exc)); continue

        tables, mix = _extract_tables(sql)
        user = rng.choice(users_pool)
        role = _pick_role(cfg, rng)
        submitted = utc_now()
        t0 = time.perf_counter()
        status = "success"; err = ""; row_count = 0
        try:
            r = con.execute(sql).fetchall()
            row_count = len(r)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            if elapsed_ms > max_seconds * 1000:
                status = "killed_budget"
                err = f"elapsed {elapsed_ms}ms > {max_seconds}s"
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            status = "error"; err = str(exc)[:500]

        rows.append({
            "query_id":       f"q_{submitted.strftime('%Y%m%d%H%M%S')}_{rng.getrandbits(24):06x}",
            "submitted_at":   submitted,
            "user_id":        user,
            "user_role":      role,
            "archetype":      arch,
            "template_id":    template_id,
            "sql_hash":       _sql_hash(sql),
            "sql_text":       sql,
            "elapsed_ms":     elapsed_ms,
            "rows_returned":  row_count,
            "tables_touched": tables,
            "layer_mix_raw":       mix.get("raw", 0),
            "layer_mix_silver":    mix.get("silver", 0),
            "layer_mix_gold":      mix.get("gold", 0),
            "layer_mix_reference": mix.get("reference", 0),
            "status":         status,
            "error":          err,
        })

    if not rows:
        return 0

    # Write batch to parquet
    tbl = pa.Table.from_pylist(rows, schema=_HISTORY_SCHEMA)
    buf = io.BytesIO()
    pq.write_table(tbl, buf, compression="zstd", row_group_size=10_000)
    key = query_history_key(cfg, utc_now())
    s3.put_object(Bucket=cfg.bucket, Key=key, Body=buf.getvalue())
    log.info("workload.batch.written", n=len(rows), key=key,
             errors=sum(1 for r in rows if r["status"] != "success"),
             avg_ms=int(sum(r["elapsed_ms"] for r in rows) / max(1, len(rows))))
    return len(rows)
