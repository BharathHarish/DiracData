"""Bootstrap Spider 2.0-Lite into MinIO — one-shot.

What it does:
  1. Shallow-clones xlang-ai/Spider2 into a scratch dir (if not present)
  2. Uploads text artifacts to MinIO:
     - manifest (135 SQLite instances filtered from spider2-lite.json)
     - eval index (gold/spider2lite_eval.jsonl — filtered to local*)
     - external_knowledge docs (resource/documents/*.md)
     - gold SQL (evaluation_suite/gold/sql/local*.sql)
     - gold CSVs (evaluation_suite/gold/exec_result/local*_*.csv)
  3. If local_sqlite.zip is present at --bundle-path (default: scratchpad),
     unzips + uploads *.sqlite files to MinIO under spider2/sqlite/
     Otherwise prints instructions for the one manual download step.

Nothing lands in the repo. All work happens in scratchpad, all outputs go to MinIO.
"""
from __future__ import annotations
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# Make the package importable when run as a script
_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

from evals.spider2_0.store import SpiderStore, SpiderConfig, _load_env


REPO_URL = "https://github.com/xlang-ai/Spider2.git"
SQLITE_BUNDLE_URL = (
    "https://drive.google.com/uc?id=1coEVsCZq-Xvj9p2TnhBFoFTsY-UoYGmG"
    "  (or via the direct download page: "
    "https://drive.usercontent.google.com/download?id=1coEVsCZq-Xvj9p2TnhBFoFTsY-UoYGmG&export=download)"
)


def _fetch_repo(scratch: Path) -> Path:
    repo = scratch / "Spider2"
    if repo.exists() and (repo / "spider2-lite").exists():
        print(f"[bootstrap] repo already cloned at {repo}")
        return repo
    print(f"[bootstrap] cloning xlang-ai/Spider2 to {repo} (shallow)")
    subprocess.check_call(["git", "clone", "--depth", "1", REPO_URL, str(repo)])
    return repo


def _upload_manifest(store: SpiderStore, repo: Path) -> int:
    """Manifest = spider2-lite.json (all 547 rows). Filter to local* on the read side.

    We upload the WHOLE manifest so future work (add BigQuery/Snowflake creds) doesn't
    need a re-bootstrap. The store's list_instances(backend='local') filters at read time.
    """
    # The real manifest (with question/db/external_knowledge) is spider2-lite.jsonl.
    # A stale spider2-lite.json might also exist in older forks; prefer jsonl.
    for candidate in (
        repo / "spider2-lite" / "spider2-lite.jsonl",
        repo / "spider2-lite" / "spider2-lite.json",
    ):
        if candidate.exists():
            src = candidate; break
    else:
        sys.exit(f"[bootstrap] no manifest found under {repo/'spider2-lite'}")
    data = ([json.loads(l) for l in src.read_text().splitlines() if l.strip()]
            if src.suffix == ".jsonl" else json.loads(src.read_text()))
    body = "\n".join(json.dumps(r) for r in data).encode()
    store.s3.put_object(Bucket=store.cfg.bucket, Key=store.cfg.key("manifest.jsonl"),
                        Body=body, ContentType="application/x-ndjson")
    n_local = sum(1 for r in data if r.get("instance_id", "").startswith("local"))
    print(f"[bootstrap] uploaded manifest: {len(data)} total instances ({n_local} local/SQLite)")
    return n_local


def _upload_eval_index(store: SpiderStore, repo: Path) -> int:
    src = repo / "spider2-lite" / "evaluation_suite" / "gold" / "spider2lite_eval.jsonl"
    if not src.exists():
        print(f"[bootstrap] WARN: eval index not found at {src}")
        return 0
    body = src.read_bytes()
    store.s3.put_object(Bucket=store.cfg.bucket, Key=store.cfg.key("gold/eval_index.jsonl"),
                        Body=body, ContentType="application/x-ndjson")
    n = len([l for l in src.read_text().splitlines() if l.strip()])
    print(f"[bootstrap] uploaded eval index: {n} rows")
    return n


def _upload_docs(store: SpiderStore, repo: Path) -> int:
    """external_knowledge docs — upload ALL under resource/documents/."""
    src = repo / "spider2-lite" / "resource" / "documents"
    if not src.exists():
        print(f"[bootstrap] WARN: docs dir not found at {src}"); return 0
    n = 0
    for md in src.glob("*.md"):
        key = store.cfg.key(f"docs/{md.name}")
        store.s3.put_object(Bucket=store.cfg.bucket, Key=key,
                            Body=md.read_bytes(), ContentType="text/markdown")
        n += 1
    print(f"[bootstrap] uploaded {n} external_knowledge docs")
    return n


def _upload_gold_sql(store: SpiderStore, repo: Path) -> int:
    """Gold SQL for local* only (SQLite subset)."""
    src = repo / "spider2-lite" / "evaluation_suite" / "gold" / "sql"
    if not src.exists(): return 0
    n = 0
    for f in src.glob("local*.sql"):
        key = store.cfg.key(f"gold/sql/{f.name}")
        store.s3.put_object(Bucket=store.cfg.bucket, Key=key,
                            Body=f.read_bytes(), ContentType="application/sql")
        n += 1
    print(f"[bootstrap] uploaded {n} gold SQL files (local* subset)")
    return n


def _upload_gold_csv(store: SpiderStore, repo: Path) -> int:
    """Gold CSVs for local* only (variants: local###_a.csv, _b.csv, …)."""
    src = repo / "spider2-lite" / "evaluation_suite" / "gold" / "exec_result"
    if not src.exists(): return 0
    n = 0
    for f in src.glob("local*.csv"):
        key = store.cfg.key(f"gold/csv/{f.name}")
        store.s3.put_object(Bucket=store.cfg.bucket, Key=key,
                            Body=f.read_bytes(), ContentType="text/csv")
        n += 1
    print(f"[bootstrap] uploaded {n} gold CSV files (local* subset)")
    return n


def _upload_sqlite_bundle(store: SpiderStore, bundle_path: Path) -> int:
    if not bundle_path.exists():
        print(f"[bootstrap] SKIP: SQLite bundle not found at {bundle_path}")
        print(f"[bootstrap]        Download manually from:")
        print(f"                    {SQLITE_BUNDLE_URL}")
        print(f"[bootstrap]        Save to: {bundle_path}")
        print(f"[bootstrap]        Then re-run this script.")
        return 0
    if bundle_path.stat().st_size < 100 * 1024 * 1024:
        print(f"[bootstrap] WARN: {bundle_path} is only {bundle_path.stat().st_size/1024/1024:.1f} MB "
              f"(expected ~435 MB). Might be Google Drive's HTML intercept — re-download manually.")
        return 0
    print(f"[bootstrap] extracting {bundle_path} ({bundle_path.stat().st_size/1024/1024:.1f} MB)...")
    n = 0
    with zipfile.ZipFile(bundle_path) as zf:
        for name in zf.namelist():
            if not name.endswith(".sqlite") or Path(name).name.startswith("._"): continue
            db_id = Path(name).stem
            data = zf.read(name)
            key = store.cfg.key(f"sqlite/{db_id}.sqlite")
            store.s3.put_object(Bucket=store.cfg.bucket, Key=key,
                                Body=data, ContentType="application/x-sqlite3")
            n += 1
            print(f"    uploaded sqlite/{db_id}.sqlite ({len(data)/1024/1024:.1f} MB)")
    print(f"[bootstrap] uploaded {n} SQLite databases")
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scratch", default="/private/tmp/spider2_bootstrap",
                    help="local scratch dir for clone + extract (gitignored, deleted after)")
    ap.add_argument("--bundle-path", default=None,
                    help="path to local_sqlite.zip (defaults to <scratch>/local_sqlite.zip)")
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--skip-repo", action="store_true", help="don't fetch/re-clone the repo")
    ap.add_argument("--only-sqlite", action="store_true", help="only upload SQLite bundle (skip text)")
    args = ap.parse_args()

    _load_env(args.env_file)
    scratch = Path(args.scratch); scratch.mkdir(parents=True, exist_ok=True)
    bundle_path = Path(args.bundle_path) if args.bundle_path else scratch / "local_sqlite.zip"

    store = SpiderStore()
    print(f"[bootstrap] target: s3://{store.cfg.bucket}/{store.cfg.root_prefix}/")

    if not args.only_sqlite:
        repo = None if args.skip_repo else _fetch_repo(scratch)
        if repo is None:
            # look for a pre-existing clone
            repo_candidates = list(scratch.glob("Spider2")) + list(scratch.glob("*/Spider2"))
            if not repo_candidates:
                sys.exit(f"repo not found; run without --skip-repo or place a clone at {scratch}/Spider2")
            repo = repo_candidates[0]
        _upload_manifest(store, repo)
        _upload_eval_index(store, repo)
        _upload_docs(store, repo)
        _upload_gold_sql(store, repo)
        _upload_gold_csv(store, repo)

    n_dbs = _upload_sqlite_bundle(store, bundle_path)

    print("\n[bootstrap] footprint:")
    for sub, stats in sorted(store.footprint().items(), key=lambda x: -x[1]["bytes"]):
        mb = stats["bytes"] / 1024 / 1024
        print(f"    {sub:40s} {mb:>8.2f} MB   ({stats['count']} objs)")

    if n_dbs == 0:
        print(f"\n[bootstrap] TEXT ARTIFACTS UPLOADED. Next manual step:")
        print(f"    1. Open: https://drive.google.com/uc?id=1coEVsCZq-Xvj9p2TnhBFoFTsY-UoYGmG")
        print(f"    2. Click 'Download anyway' when Google Drive warns about virus scan")
        print(f"    3. Save the resulting local_sqlite.zip to: {bundle_path}")
        print(f"    4. Re-run: python -m evals.spider2_0.scripts.bootstrap --only-sqlite")


if __name__ == "__main__":
    main()
