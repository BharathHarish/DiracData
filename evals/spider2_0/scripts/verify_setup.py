"""Smoke test after bootstrap — verifies the whole read path works.

Picks one local* instance at random (deterministic seed for reproducibility):
  1. Fetches manifest → sample instance
  2. Fetches its gold SQL from MinIO
  3. Fetches its SQLite DB from MinIO to local cache
  4. Executes gold SQL via DuckDB (ATTACH TYPE SQLITE)
  5. Compares result to gold CSV via the grader
  6. Reports PASS/FAIL

If this passes, we know: SpiderStore reads correctly, SQLite cache works,
DuckDB+SQLite attach works, grader agrees with the gold CSV.
"""
from __future__ import annotations
import argparse
import io
import random
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

import duckdb
import pandas as pd
from evals.spider2_0.store import SpiderStore, _load_env
from evals.spider2_0.grader import grade_one


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instance-id", default=None, help="test a specific instance; otherwise random")
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    _load_env(args.env_file)

    store = SpiderStore()

    # 1) pick instance
    instances = store.list_instances(backend="local")
    if not instances:
        sys.exit("no local* instances in MinIO — did bootstrap.py finish?")
    if args.instance_id:
        matches = [i for i in instances if i["instance_id"] == args.instance_id]
        if not matches: sys.exit(f"instance {args.instance_id} not found")
        inst = matches[0]
    else:
        # Prefer instances that HAVE gold SQL (only 24/135 do)
        with_gold = [i for i in instances if store.get_gold_sql(i["instance_id"])]
        pool = with_gold or instances
        random.seed(args.seed)
        inst = random.choice(pool)
        print(f"[verify] sampled from {len(pool)} instances (with gold SQL)")
    iid = inst["instance_id"]
    db_id = inst.get("db", "?")
    print(f"[verify] instance={iid}  db={db_id}  question={inst.get('question','')[:120]}")

    # 2) gold SQL
    gold_sql = store.get_gold_sql(iid)
    if not gold_sql:
        sys.exit(f"[verify] no gold SQL for {iid}")
    print(f"[verify] gold SQL ({len(gold_sql)} chars) fetched")

    # 3) SQLite from MinIO
    try:
        sqlite_path = store.sqlite_local_path(db_id)
        print(f"[verify] sqlite cached at {sqlite_path} ({sqlite_path.stat().st_size/1024/1024:.1f} MB)")
    except FileNotFoundError as ex:
        sys.exit(f"[verify] SQLite bundle not yet uploaded: {ex}")

    # 4) execute via DuckDB (ATTACH TYPE SQLITE — our unified engine)
    con = duckdb.connect(":memory:")
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{sqlite_path}' AS spider_db (TYPE SQLITE)")
    con.execute("USE spider_db")
    try:
        df = con.execute(gold_sql).fetchdf()
        print(f"[verify] gold SQL executed via DuckDB — {len(df)} rows × {len(df.columns)} cols")
        print(f"[verify] sample: {df.head(3).to_dict(orient='records')}")
    except Exception as ex:
        sys.exit(f"[verify] gold SQL FAILED to execute via DuckDB: {ex}")

    # 5) grade against gold CSV
    buf = io.BytesIO(); df.to_csv(buf, index=False); pred_bytes = buf.getvalue()
    gold_csvs = store.get_gold_csvs(iid)
    eval_meta = store.get_eval_index(iid)
    print(f"[verify] gold CSV variants: {list(gold_csvs.keys())}   eval_meta={eval_meta}")

    v = grade_one(iid, pred_bytes, gold_csvs, eval_meta)
    print(f"\n[verify] verdict: {'PASS ✔' if v.passed else 'FAIL ✘'}"
          f"  variant={v.matched_variant}  reason={v.reason}")
    print(f"    pred_rows={v.n_pred_rows}   gold_rows={v.n_gold_rows}")
    return 0 if v.passed else 1


if __name__ == "__main__":
    sys.exit(main())
