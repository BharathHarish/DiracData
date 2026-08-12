"""Status CLI — row counts by layer, disk usage, last-tick timestamps."""
from __future__ import annotations
import sys
from pathlib import Path
from collections import defaultdict

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from data_harness.common.config import load_config
from data_harness.common.duckdb_conn import make_duckdb
from data_harness.common.minio_client import make_s3
from data_harness.common.paths import (raw_scan_uri, silver_scan_uri, gold_scan_uri,
                                       reference_uri)


def _h(n: int) -> str:
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def _try_count(con, uri: str) -> int:
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM read_parquet('{uri}')").fetchone()[0])
    except Exception:
        return 0


def main():
    cfg = load_config()
    con = make_duckdb(cfg)
    s3  = make_s3(cfg)

    # Total footprint of lake/fintech/
    per_prefix = defaultdict(lambda: {"b": 0, "c": 0})
    prefix = f"{cfg.root_prefix}/"
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=cfg.bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            parts = o["Key"][len(prefix):].split("/")
            # Group by first two path components
            key = "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
            per_prefix[key]["b"] += o["Size"]
            per_prefix[key]["c"] += 1

    print(f"=== lake/{cfg.root_prefix}/ footprint ===")
    total_b, total_c = 0, 0
    for k, v in sorted(per_prefix.items(), key=lambda x: -x[1]["b"]):
        print(f"  {k:60s} {_h(v['b']):>10s}  ({v['c']} objs)")
        total_b += v["b"]; total_c += v["c"]
    print(f"  {'TOTAL':60s} {_h(total_b):>10s}  ({total_c} objs)")

    # Row counts by layer
    print(f"\n=== row counts by layer ===")
    # Reference tables
    ref_prefix = f"{cfg.root_prefix}/reference/"
    ref_tables = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=cfg.bucket, Prefix=ref_prefix):
        for o in page.get("Contents", []):
            n = o["Key"][len(ref_prefix):].removesuffix(".parquet")
            if n and "/" not in n:
                ref_tables.append(n)
    for name in sorted(ref_tables):
        n = _try_count(con, reference_uri(cfg, name))
        print(f"  reference.{name:40s} {n:>10,} rows")

    # Raw tables — discover from prefix
    raw_prefix = f"{cfg.root_prefix}/raw/"
    raw_tables = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=cfg.bucket, Prefix=raw_prefix):
        for o in page.get("Contents", []):
            parts = o["Key"][len(raw_prefix):].split("/")
            if len(parts) >= 2:
                raw_tables.add((parts[0], parts[1]))
    for (d, t) in sorted(raw_tables):
        n = _try_count(con, raw_scan_uri(cfg, d, t))
        print(f"  raw.{d}.{t:40s} {n:>10,} rows")

    # Silver
    silver_prefix = f"{cfg.root_prefix}/silver/"
    silver_tables = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=cfg.bucket, Prefix=silver_prefix):
        for o in page.get("Contents", []):
            parts = o["Key"][len(silver_prefix):].split("/")
            if len(parts) >= 1:
                silver_tables.add(parts[0])
    for t in sorted(silver_tables):
        n = _try_count(con, silver_scan_uri(cfg, t))
        print(f"  silver.{t:44s} {n:>10,} rows")

    # Gold
    gold_prefix = f"{cfg.root_prefix}/gold/"
    gold_tables = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=cfg.bucket, Prefix=gold_prefix):
        for o in page.get("Contents", []):
            parts = o["Key"][len(gold_prefix):].split("/")
            if len(parts) >= 1:
                gold_tables.add(parts[0])
    for t in sorted(gold_tables):
        n = _try_count(con, gold_scan_uri(cfg, t))
        print(f"  gold.{t:46s} {n:>10,} rows")


if __name__ == "__main__":
    main()
