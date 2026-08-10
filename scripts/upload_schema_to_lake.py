#!/usr/bin/env python3
"""Push a schema's table parquet into the object-store lake bucket so the engine reads it
object-store-native (DuckDB over httpfs). Local `data/` is only a staging area for generation --
the lake is the source of record. Symlinked staging files are followed (real bytes uploaded).

    PYTHONPATH=src .venv/bin/python scripts/upload_schema_to_lake.py --schema retail_complex fintech_complex
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diracdata.config import settings_from_env  # noqa: E402
from diracdata.stores import S3ObjectStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", nargs="+", required=True)
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    args = ap.parse_args()

    s = settings_from_env(args.env_file)
    store = S3ObjectStore(bucket=s.lake_bucket, endpoint_url=s.s3_endpoint_url,
                          region_name=s.aws_region, aws_access_key_id=s.aws_access_key_id,
                          aws_secret_access_key=s.aws_secret_access_key)
    for schema in args.schema:
        root = Path(args.data_root) / schema / "parquet"
        files = sorted(root.rglob("*.parquet"))
        if not files:
            print(f"  {schema}: no parquet under {root}", file=sys.stderr)
            continue
        total = 0
        for path in files:
            rel = path.relative_to(root).as_posix()          # preserve layout (e.g. sf1/x.parquet)
            key = f"{schema}/{rel}"
            data = path.read_bytes()                          # follows symlinks -> real bytes
            store.write_bytes(key, data, content_type="application/octet-stream")
            total += len(data)
        print(f"  {schema}: uploaded {len(files)} tables ({total/1e6:.1f} MB) -> s3://{s.lake_bucket}/{schema}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
