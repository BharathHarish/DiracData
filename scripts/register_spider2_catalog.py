"""Bootstrap the spider2_local catalog registry in the fabric store.

Discovers every SQLite blob under lake:spider2/sqlite/*.sqlite and writes the minimum
CatalogStore entries so dirac-catalog-mcp sees the catalog + 30 databases:

    fabric/catalogs/spider2_local/catalog.yaml                 — one catalog stub
    fabric/catalogs/spider2_local/databases/<db>/database.yaml — one per SQLite

Table lists + descriptions are NOT written here; that's the learning-time job
(Cursor drives it via propose_table_description + refresh_database_md).

Idempotent — running twice is a no-op if artifacts already exist.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List

import boto3
from botocore.config import Config as BotoConfig


CATALOG = "spider2_local"


def _load_env(env_file: str) -> None:
    if not env_file or not os.path.exists(env_file):
        return
    for ln in open(env_file):
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["DIRACDATA_S3_ENDPOINT_URL"],
        aws_access_key_id=os.environ["DIRACDATA_AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["DIRACDATA_AWS_SECRET_ACCESS_KEY"],
        config=BotoConfig(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
    )


def _discover_sqlite_dbs(s3, lake_bucket: str, prefix: str = "spider2/sqlite/") -> List[dict]:
    out = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=lake_bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            name = o["Key"].split("/")[-1]
            if not name.endswith(".sqlite"):
                continue
            db = name[: -len(".sqlite")]
            out.append({"db": db, "size_mb": round(o["Size"] / (1024 * 1024), 1)})
    return sorted(out, key=lambda r: r["db"])


def _put_json(s3, bucket: str, key: str, obj: dict, dry_run: bool) -> None:
    text = json.dumps(obj, indent=2, sort_keys=False)
    if dry_run:
        print(f"  [dry] would write s3://{bucket}/{key}  ({len(text)} B)")
        return
    s3.put_object(Bucket=bucket, Key=key, Body=text.encode(), ContentType="application/json")
    print(f"  wrote s3://{bucket}/{key}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Register spider2_local catalog + its 30 databases.")
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite", action="store_true",
                    help="Rewrite catalog.yaml / database.yaml even if they exist.")
    args = ap.parse_args()

    _load_env(args.env_file)
    artifact_bucket = os.environ["DIRACDATA_ARTIFACT_BUCKET"]
    lake_bucket = os.environ["DIRACDATA_LAKE_BUCKET"]
    s3 = _s3()

    dbs = _discover_sqlite_dbs(s3, lake_bucket)
    print(f"[discover] {len(dbs)} SQLite databases in s3://{lake_bucket}/spider2/sqlite/")
    if not dbs:
        print("  nothing to register — run evals/spider2_0/scripts/bootstrap.py first")
        return 1

    # 1) catalog.json — describes the catalog as SQLite-backed (JSON because CatalogStore
    #    reads via read_json; .yaml files are for text-parsed artifacts only)
    catalog_key = f"fabric/catalogs/{CATALOG}/catalog.json"
    catalog_meta = {
        "name": CATALOG,
        "engine": "duckdb+sqlite",
        "connection": {
            "sqlite_blob_prefix": "spider2/sqlite/",
            "sqlite_blob_bucket": lake_bucket,
        },
        "description": (
            "Spider 2.0-Lite local SQLite subset — 30 real-world databases used by the "
            "spider2-lite eval. Each database is one .sqlite file in the object store; "
            "DuckDB ATTACHes it read-only. Cross-DB joins are legitimate for hard questions."
        ),
        "database_count": len(dbs),
    }
    exists = _key_exists(s3, artifact_bucket, catalog_key)
    if exists and not args.overwrite:
        print(f"[skip] catalog.json already exists — pass --overwrite to force")
    else:
        _put_json(s3, artifact_bucket, catalog_key, catalog_meta, args.dry_run)

    # 2) per-database database.json stubs
    written = 0
    for row in dbs:
        db = row["db"]
        db_key = f"fabric/catalogs/{CATALOG}/databases/{db}/database.json"
        if _key_exists(s3, artifact_bucket, db_key) and not args.overwrite:
            continue
        db_meta = {
            "name": db,
            "catalog": CATALOG,
            "engine": "sqlite",
            "size_mb": row["size_mb"],
            "sqlite_key": f"spider2/sqlite/{db}.sqlite",
            "description": "",
            "table_names": [],
        }
        _put_json(s3, artifact_bucket, db_key, db_meta, args.dry_run)
        written += 1

    # 3) clean up any stale .yaml stubs from earlier runs
    for row in dbs:
        stale_key = f"fabric/catalogs/{CATALOG}/databases/{row['db']}/database.yaml"
        if _key_exists(s3, artifact_bucket, stale_key):
            if not args.dry_run:
                s3.delete_object(Bucket=artifact_bucket, Key=stale_key)
                print(f"  removed stale s3://{artifact_bucket}/{stale_key}")
    stale_cat = f"fabric/catalogs/{CATALOG}/catalog.yaml"
    if _key_exists(s3, artifact_bucket, stale_cat) and not args.dry_run:
        s3.delete_object(Bucket=artifact_bucket, Key=stale_cat)
        print(f"  removed stale s3://{artifact_bucket}/{stale_cat}")

    print(f"\n[done] registered {len(dbs)} databases ({written} database.json written)")
    print(f"       verify:  .venv/bin/dirac-catalog-mcp --catalog {CATALOG} --env-file {args.env_file}")
    return 0


def _key_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
