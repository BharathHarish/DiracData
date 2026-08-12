"""Build lake/fintech/lineage.json — the modeller's structural map of the world.

Contents (per PLAN §11):
  - raw.<domain>.<table>   : {cols, row_count, bytes, partition_hint}
  - silver.<table>         : {grain, sources, sql_hash, row_count, build_ms}
  - gold.<table>           : {grain, sources, row_count}
  - edges: [{from, to}]    : derived from transform headers

Explicitly NOT included: primary keys, foreign keys, join keys. §Decision #7 —
modeller must discover joins from data.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
import duckdb
from data_harness.common.config import Config
from data_harness.common.paths import (raw_scan_uri, silver_scan_uri, gold_scan_uri,
                                       reference_uri, lineage_key, utc_now)
from data_harness.common.minio_client import upload_json
from data_harness.transforms.sql_header import parse
from data_harness.transforms.runner import _list_sqls


def _try_describe(con: duckdb.DuckDBPyConnection, uri: str) -> List[Dict]:
    try:
        rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{uri}')").fetchall()
        return [{"name": r[0], "type": r[1]} for r in rows]
    except Exception:
        return []


def _try_count(con: duckdb.DuckDBPyConnection, uri: str) -> int:
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM read_parquet('{uri}')").fetchone()[0])
    except Exception:
        return 0


def _list_raw_tables(s3, cfg: Config) -> List[tuple[str, str]]:
    """List (domain, table) present under lake/fintech/raw/."""
    out = set()
    prefix = f"{cfg.root_prefix}/raw/"
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=cfg.bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            key = o["Key"]
            rest = key[len(prefix):]
            parts = rest.split("/")
            if len(parts) >= 2:
                out.add((parts[0], parts[1]))
    return sorted(out)


def _list_reference_tables(s3, cfg: Config) -> List[str]:
    out = []
    prefix = f"{cfg.root_prefix}/reference/"
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=cfg.bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            name = o["Key"][len(prefix):].removesuffix(".parquet")
            if name and "/" not in name:
                out.append(name)
    return sorted(out)


def build_lineage(cfg: Config, con: duckdb.DuckDBPyConnection, s3) -> Dict:
    lineage: Dict = {
        "generated_at":  utc_now().isoformat(),
        "reference":     {},
        "raw":           {},
        "silver":        {},
        "gold":          {},
        "edges":         [],
    }

    # -- reference --
    for name in _list_reference_tables(s3, cfg):
        uri = reference_uri(cfg, name)
        lineage["reference"][f"reference.{name}"] = {
            "cols":      _try_describe(con, uri),
            "row_count": _try_count(con, uri),
        }

    # -- raw --
    for (domain, table) in _list_raw_tables(s3, cfg):
        uri = raw_scan_uri(cfg, domain, table)
        full_name = f"raw.{domain}.{table}"
        lineage["raw"][full_name] = {
            "domain":    domain,
            "cols":      _try_describe(con, uri),
            "row_count": _try_count(con, uri),
            "scan_uri":  uri,
        }

    # -- silver + gold from SQL headers --
    for layer in ("silver", "gold"):
        for path in _list_sqls(layer):
            hdr = parse(path)
            full_name = f"{layer}.{hdr.table_name}"
            scan_uri = silver_scan_uri(cfg, hdr.table_name) if layer == "silver" else gold_scan_uri(cfg, hdr.table_name)
            lineage[layer][full_name] = {
                "grain":       hdr.grain,
                "description": hdr.description,
                "sources":     hdr.sources,
                "notes":       hdr.notes,
                "lookback":    hdr.lookback,
                "sql_hash":    "sha1:" + hashlib.sha1(hdr.body.strip().encode()).hexdigest()[:16],
                "cols":        _try_describe(con, scan_uri),
                "row_count":   _try_count(con, scan_uri),
                "scan_uri":    scan_uri,
            }
            for src in hdr.sources:
                lineage["edges"].append({"from": src, "to": full_name})

    upload_json(s3, cfg.bucket, lineage_key(cfg), lineage)
    return lineage
