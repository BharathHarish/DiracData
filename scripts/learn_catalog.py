"""learn_catalog — catalog-aware learn2 CLI.

Wraps the existing single-schema LearningAgent to run in catalog+database mode:

  # Single DB (default: catalog='local' if omitted):
  dirac learn --catalog local --database retail_complex

  # Every DB in a catalog (skipping any that already have a fabric):
  dirac learn --catalog spider2_local

  # Subset of DBs in a catalog:
  dirac learn --catalog spider2_local --database chinook,f1,IPL

After per-DB learning finishes, this script:
  1. Migrates the newly-written legacy fabric/<db>/ artifacts to
     fabric/catalogs/<catalog>/databases/<db>/ (via the same copy-not-move
     logic as scripts/migrate_to_catalog.py — indefinite backward compat)
  2. Authors database.md for each DB via the LLM
  3. Authors catalog.md once at the end (rollup of all database.md files)

Zero touches to the existing LearningAgent — we compose from outside.
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from pathlib import Path
from typing import Callable, List

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))


def _load_env(env_file: str) -> None:
    if os.path.exists(env_file):
        for ln in open(env_file):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln: continue
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _copy_legacy_to_new(store, database: str, catalog: str) -> int:
    """Copy fabric/<database>/* → fabric/catalogs/<catalog>/databases/<database>/*."""
    from diracdata.context.catalog_store import _database_key
    n = 0
    prefix = f"fabric/{database}/"
    for k in store.list_keys(prefix):
        if not k.startswith(prefix): continue
        name = k[len(prefix):]
        if not name or "/" in name: continue
        dst = _database_key(catalog, database, name)
        if store.exists(dst): continue
        store.write_text(dst, store.read_text(k), content_type="application/octet-stream")
        n += 1
    return n


def _make_index_llm(profile_id: str, settings: Any) -> Callable[[str], str]:
    """A plain str->str LLM callable (used for database.md / catalog.md authoring), built through
    the model factory so it honours whichever provider the profile names (Together, Fireworks, ...)."""
    from diracdata.models.factory import ChatModelFactory
    model = ChatModelFactory(settings=settings).create_chat_model(profile_id=profile_id)
    def _call(prompt: str) -> str:
        resp = model.invoke(prompt)
        return getattr(resp, "content", None) or str(resp)
    return _call


def _is_sqlite_catalog(cs: Any, catalog: str) -> bool:
    """A catalog whose databases are ATTACHed SQLite files (Spider 2.0 etc.). We read the catalog
    metadata's engine hint; fall back to the naming convention used by the registry."""
    cat_meta = (cs.get_catalog(catalog, "catalog.json", default={})
                or cs.get_catalog(catalog, "catalog.yaml", default={}) or {})
    if "sqlite" in str(cat_meta.get("engine", "")).lower():
        return True
    return catalog.startswith("spider2") or "sqlite" in catalog


def _download_sqlite_for_db(cs: Any, settings: Any, catalog: str, database: str) -> "Path":
    """Fetch the DB's SQLite blob from the LAKE bucket into a local cache (idempotent) and return
    the local path. The key comes from the database.json stub written by register_spider2_catalog."""
    import boto3
    from botocore.config import Config as BC

    db_meta = (cs.get(catalog, database, "database.json", default={})
               or cs.get(catalog, database, "database.yaml", default={}) or {})
    sqlite_key = db_meta.get("sqlite_key") or f"spider2/sqlite/{database}.sqlite"

    cache = Path(os.getenv("DIRACDATA_CATALOG_SQLITE_CACHE",
                           str(Path.home() / ".diracdata" / "catalog_sqlite_cache" / catalog)))
    cache.mkdir(parents=True, exist_ok=True)
    local = cache / f"{database}.sqlite"
    if local.exists() and local.stat().st_size > 0:
        return local

    s3 = boto3.client(
        "s3", endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.aws_access_key_id, aws_secret_access_key=settings.aws_secret_access_key,
        config=BC(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
    )
    s3.download_file(settings.lake_bucket, sqlite_key, str(local))
    return local


def _make_stream_sink(mode: str):
    """A sink that respects the same StreamMode contract data_analyst uses: off/messages/updates/all.
    'updates' (recommended for learning) shows phase info + every tool call/result — that is exactly
    the durable long-running trace: what tables/columns/joins the learner is currently reasoning over,
    without token-level spam."""
    from diracdata.streaming import mode_sink
    def base(stage, kind, text):
        if kind == "tool_call":
            print(f"  >> [{stage}] {text}", file=sys.stderr, flush=True)
        elif kind == "tool_result":
            snippet = text if len(text) < 200 else text[:200] + "…"
            print(f"  << [{stage}] {snippet}", file=sys.stderr, flush=True)
        elif kind == "info":
            print(f"[{stage}] {text}", file=sys.stderr, flush=True)
        elif kind == "token":
            # token streams are noisy; print without newlines when mode='all'
            print(text, end="", file=sys.stderr, flush=True)
        else:
            print(f"[{stage}] {kind}: {text}", file=sys.stderr, flush=True)
    return mode_sink(base, mode)


def _run_learn_for_one_db(*, catalog: str, database: str, model_profile: str, env_file: str,
                          sqlite_mode: bool, cs: Any, stream_mode: str) -> None:
    """Run the agentic Learner over one database (writes to legacy fabric/<database>/, which the
    caller then migrates to the catalog layout). For SQLite catalogs an ATTACHed-SQLite engine is
    injected so the same harness learns a Spider 2.0 database exactly like a parquet schema."""
    from diracdata.config import settings_from_env
    from diracdata.learning import Learner

    settings = settings_from_env(env_file)

    engine = None
    if sqlite_mode:
        from diracdata.engines import DuckDBEngine
        sqlite_path = _download_sqlite_for_db(cs, settings, catalog, database)
        engine = DuckDBEngine.from_sqlite(sqlite_path, schema_name=database)

    sink = _make_stream_sink(stream_mode)
    learner = Learner(schema=database, model=model_profile, settings=settings,
                      subagents=True, engine=engine)
    learner.learn(sink=sink)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog",  required=True, help="catalog name (e.g. 'local', 'spider2_local')")
    ap.add_argument("--database", default=None,
                    help="one database name or comma-separated subset; omit to learn every DB in catalog")
    ap.add_argument("--model-profile", default="together_deepseek_v3",
                    help="model profile the agentic Learner uses (e.g. together_deepseek_v3, "
                         "fireworks_deepseek_v4_flash)")
    ap.add_argument("--index-model",   default="together_deepseek_v3",
                    help="model profile used to author database.md / catalog.md")
    ap.add_argument("--env-file", default=str(_ROOT / ".env"))
    ap.add_argument("--skip-learn",  action="store_true",
                    help="skip the LearningAgent run; only author database.md / catalog.md over what's already learned")
    ap.add_argument("--only-index",  action="store_true",
                    help="alias for --skip-learn (only refresh indexes)")
    ap.add_argument("--stream-mode", default="updates",
                    choices=["off", "messages", "updates", "all"],
                    help="live-trace verbosity: off = silent; updates (default) = phase info + tool "
                         "calls + tool results (no token spam); messages = token stream + tool i/o; "
                         "all = everything including reasoning + usage")
    args = ap.parse_args()
    _load_env(args.env_file)

    from diracdata.config import settings_from_env
    from diracdata.stores import store_from_settings
    from diracdata.context.catalog_store import CatalogStore
    from diracdata.learning.catalog_index import (
        build_database_md, build_catalog_md, build_all_databases_md,
    )

    settings = settings_from_env(args.env_file)
    store    = store_from_settings(settings)
    cs       = CatalogStore(store)

    # Resolve database list
    if args.database:
        dbs: List[str] = [d.strip() for d in args.database.split(",") if d.strip()]
    else:
        dbs = cs.list_databases(args.catalog)
        if not dbs:
            print(f"[learn-catalog] no databases found under catalog {args.catalog!r} and no --database given.",
                  file=sys.stderr)
            return 1

    sqlite_mode = _is_sqlite_catalog(cs, args.catalog)
    print(f"[learn-catalog] catalog={args.catalog}  databases={dbs}  "
          f"model={args.model_profile}  sqlite_mode={sqlite_mode}  "
          f"skip_learn={args.skip_learn or args.only_index}", file=sys.stderr)

    # Phase 1: per-DB learning
    if not (args.skip_learn or args.only_index):
        for db in dbs:
            t0 = time.time()
            print(f"\n=== [learn-catalog] {args.catalog}/{db}: Learner starting ===",
                  file=sys.stderr, flush=True)
            try:
                _run_learn_for_one_db(catalog=args.catalog, database=db,
                                       model_profile=args.model_profile, env_file=args.env_file,
                                       sqlite_mode=sqlite_mode, cs=cs,
                                       stream_mode=args.stream_mode)
            except Exception as ex:
                print(f"=== [learn-catalog] {args.catalog}/{db}: FAILED after {time.time()-t0:.0f}s: "
                      f"{type(ex).__name__}: {ex} ===", file=sys.stderr, flush=True)
                continue
            print(f"=== [learn-catalog] {args.catalog}/{db}: done in {time.time()-t0:.0f}s ===",
                  file=sys.stderr, flush=True)
            n = _copy_legacy_to_new(store, database=db, catalog=args.catalog)
            print(f"[learn-catalog] migrated {n} legacy artifacts → new layout",
                  file=sys.stderr, flush=True)

    # Phase 2: author database.md for each DB
    print(f"\n[learn-catalog] authoring database.md for {len(dbs)} databases...", file=sys.stderr)
    llm = _make_index_llm(args.index_model, settings)
    for db in dbs:
        try:
            md = build_database_md(cs, catalog=args.catalog, database=db, llm=llm)
            print(f"  ✓ database.md for {args.catalog}/{db}: {len(md)} chars", file=sys.stderr)
        except Exception as ex:
            print(f"  ✘ database.md for {args.catalog}/{db}: {ex}", file=sys.stderr)

    # Phase 3: catalog.md rollup
    print(f"\n[learn-catalog] authoring catalog.md rollup...", file=sys.stderr)
    try:
        md = build_catalog_md(cs, catalog=args.catalog, llm=llm)
        print(f"  ✓ catalog.md for {args.catalog}: {len(md)} chars", file=sys.stderr)
    except Exception as ex:
        print(f"  ✘ catalog.md for {args.catalog}: {ex}", file=sys.stderr)

    print(f"\n[learn-catalog] complete. Read: dirac-catalog-mcp --catalog {args.catalog}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
