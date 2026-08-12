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


def _make_fireworks_llm(model_id: str = "accounts/fireworks/models/deepseek-v4-flash-0731") -> Callable[[str], str]:
    """Small Fireworks-backed LLM callable used for database.md / catalog.md authoring."""
    import os
    from openai import OpenAI
    api_key = os.environ.get("FIREWORKS_API_KEY") or os.environ.get("DIRACDATA_FIREWORKS_API_KEY")
    if not api_key:
        raise SystemExit("[learn-catalog] no FIREWORKS_API_KEY (or DIRACDATA_FIREWORKS_API_KEY) in env")
    client = OpenAI(base_url="https://api.fireworks.ai/inference/v1", api_key=api_key)
    def _call(prompt: str) -> str:
        resp = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        return resp.choices[0].message.content or ""
    return _call


def _run_learn_for_one_db(*, database: str, model_profile: str, env_file: str) -> None:
    """Delegate to the existing LearningAgent (writes to legacy fabric/<database>/)."""
    from diracdata.utils.model_factory import ChatModelFactory
    from diracdata.config import settings_from_env
    from diracdata.learning import LearningAgent, write_artifacts

    settings = settings_from_env(env_file)
    factory = ChatModelFactory()
    model = factory.build(model_profile)
    agent = LearningAgent(schema=database, model=model, settings=settings, subagents=True)

    def sink(stage, kind, text):
        if kind == "info":
            print(f"[{stage}] {text}", file=sys.stderr, flush=True)

    result = agent.learn(sink=sink)
    write_artifacts(agent, result)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog",  required=True, help="catalog name (e.g. 'local', 'spider2_local')")
    ap.add_argument("--database", default=None,
                    help="one database name or comma-separated subset; omit to learn every DB in catalog")
    ap.add_argument("--model-profile", default="fireworks_deepseek_v4_flash")
    ap.add_argument("--index-model",   default="accounts/fireworks/models/deepseek-v4-flash-0731",
                    help="Fireworks model id used for database.md / catalog.md authoring")
    ap.add_argument("--env-file", default=str(_ROOT / ".env"))
    ap.add_argument("--skip-learn",  action="store_true",
                    help="skip the LearningAgent run; only author database.md / catalog.md over what's already learned")
    ap.add_argument("--only-index",  action="store_true",
                    help="alias for --skip-learn (only refresh indexes)")
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

    print(f"[learn-catalog] catalog={args.catalog}  databases={dbs}  "
          f"skip_learn={args.skip_learn or args.only_index}", file=sys.stderr)

    # Phase 1: per-DB learning
    if not (args.skip_learn or args.only_index):
        for db in dbs:
            t0 = time.time()
            print(f"\n=== [learn-catalog] {args.catalog}/{db}: LearningAgent starting ===",
                  file=sys.stderr, flush=True)
            _run_learn_for_one_db(database=db, model_profile=args.model_profile,
                                   env_file=args.env_file)
            print(f"=== [learn-catalog] {args.catalog}/{db}: done in {time.time()-t0:.0f}s ===",
                  file=sys.stderr, flush=True)
            n = _copy_legacy_to_new(store, database=db, catalog=args.catalog)
            print(f"[learn-catalog] migrated {n} legacy artifacts → new layout",
                  file=sys.stderr, flush=True)

    # Phase 2: author database.md for each DB
    print(f"\n[learn-catalog] authoring database.md for {len(dbs)} databases...", file=sys.stderr)
    llm = _make_fireworks_llm(args.index_model)
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
