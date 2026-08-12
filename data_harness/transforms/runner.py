"""Silver + Gold transform runner.

Walks transforms/silver/*.sql and transforms/gold/*.sql in dependency order (from
lineage), wraps each body in COPY (...) TO 's3://…' (FORMAT PARQUET), executes.
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Dict, List
import duckdb
from data_harness.common.config import Config
from data_harness.common.logging import log
from data_harness.common.paths import silver_partition_key, gold_partition_key, utc_now
from data_harness.transforms.sql_header import SqlHeader, parse


_ROOT = Path(__file__).resolve().parent


def _list_sqls(layer: str) -> List[Path]:
    d = _ROOT / layer
    return sorted(p for p in d.glob("*.sql") if p.is_file())


def _topo_sort(headers: List[SqlHeader], layer: str) -> List[SqlHeader]:
    """Sort so a table's sources within the same layer are built first.
    Cross-layer sources (raw.*, silver.* for gold) don't create edges here — they
    are prerequisites resolved by the stage order in orchestrator/run.py.
    """
    name_to_hdr = {h.table_name: h for h in headers}
    layer_prefix = f"{layer}."
    resolved: List[SqlHeader] = []
    seen: set[str] = set()

    def visit(h: SqlHeader, chain: tuple):
        if h.table_name in seen:
            return
        if h.table_name in chain:
            raise ValueError(f"cycle in {layer} deps: {chain + (h.table_name,)}")
        for src in h.sources:
            if src.startswith(layer_prefix):
                dep_name = src[len(layer_prefix):]
                if dep_name in name_to_hdr:
                    visit(name_to_hdr[dep_name], chain + (h.table_name,))
        seen.add(h.table_name)
        resolved.append(h)

    for h in headers:
        visit(h, ())
    return resolved


def _wrap_copy(cfg: Config, layer: str, table: str, body: str) -> str:
    now = utc_now()
    if layer == "silver":
        key = silver_partition_key(cfg, table, now)
    elif layer == "gold":
        key = gold_partition_key(cfg, table, now)
    else:
        raise ValueError(f"unknown layer {layer}")
    uri = f"s3://{cfg.bucket}/{key}"
    return f"COPY ({body}) TO '{uri}' (FORMAT PARQUET, COMPRESSION ZSTD)"


def run_layer(cfg: Config, con: duckdb.DuckDBPyConnection, layer: str) -> List[Dict]:
    """Execute all SQL files under transforms/<layer>/, in topo order. Returns per-table stats."""
    paths = _list_sqls(layer)
    if not paths:
        log.info("transforms.empty", layer=layer)
        return []
    headers = [parse(p) for p in paths]
    ordered = _topo_sort(headers, layer)
    results = []
    for h in ordered:
        wrapped = _wrap_copy(cfg, layer, h.table_name, h.body)
        t0 = time.perf_counter()
        try:
            con.execute(wrapped)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            log.info("transforms.write", layer=layer, table=h.table_name,
                     elapsed_ms=elapsed_ms, sources=h.sources)
            results.append({"layer": layer, "table": h.table_name,
                            "sources": h.sources, "grain": h.grain,
                            "elapsed_ms": elapsed_ms, "sql_hash": _hash_body(h.body),
                            "status": "success"})
        except Exception as exc:
            log.error("transforms.error", layer=layer, table=h.table_name, error=str(exc))
            results.append({"layer": layer, "table": h.table_name,
                            "sources": h.sources, "grain": h.grain,
                            "status": "error", "error": str(exc)})
    return results


def _hash_body(body: str) -> str:
    import hashlib
    return "sha1:" + hashlib.sha1(body.strip().encode()).hexdigest()[:16]
