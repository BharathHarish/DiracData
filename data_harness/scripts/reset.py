"""Reset — nuke lake/fintech/* (keeps nothing). Use before a clean rerun."""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from data_harness.common.config import load_config
from data_harness.common.minio_client import make_s3


def main():
    cfg = load_config()
    s3 = make_s3(cfg)
    prefix = f"{cfg.root_prefix}/"
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=cfg.bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            keys.append({"Key": o["Key"]})
    n = 0
    for i in range(0, len(keys), 1000):
        batch = keys[i:i+1000]
        if batch:
            s3.delete_objects(Bucket=cfg.bucket, Delete={"Objects": batch, "Quiet": True})
            n += len(batch)
    print(f"deleted {n} objects under s3://{cfg.bucket}/{prefix}")


if __name__ == "__main__":
    main()
