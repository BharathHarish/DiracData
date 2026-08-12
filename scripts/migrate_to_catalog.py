"""One-shot migration: legacy fabric/<schema>/* → new fabric/catalogs/local/databases/<schema>/*.

Copy-not-move: legacy paths stay intact (CatalogStore's fallback still works, so any
consumer that hasn't been ported to the new layout continues to read the old paths).
This is safe to run multiple times — it's idempotent.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/migrate_to_catalog.py [--env-file .env] [--dry-run]

After running:
  - fabric/retail_complex/*                    (unchanged, legacy)
  - fabric/fintech_complex/*                   (unchanged, legacy)
  - fabric/catalogs/local/catalog.yaml         (new — top-level catalog metadata)
  - fabric/catalogs/local/databases/retail_complex/*    (new — copied)
  - fabric/catalogs/local/databases/fintech_complex/*   (new — copied)

Exit criteria: `pytest tests -q` still green; retail_complex UAT still 10/10.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))


def _load_env(env_file: str) -> None:
    if os.path.exists(env_file):
        for ln in open(env_file):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln: continue
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _copy_artifact(store, src_key: str, dst_key: str, dry_run: bool) -> str:
    """Copy one blob from src_key to dst_key. Returns 'copied' | 'skipped' | 'missing'."""
    if not store.exists(src_key):
        return "missing"
    if store.exists(dst_key):
        return "skipped (already at destination)"
    if dry_run:
        return "would-copy"
    # Read as text (works for both JSON and YAML/MD/JSONL) and write text.
    # This preserves the raw bytes without JSON round-tripping.
    text = store.read_text(src_key)
    store.write_text(dst_key, text, content_type="application/octet-stream")
    return "copied"


def _discover_legacy_schemas(store) -> List[str]:
    """Any fabric/<schema>/* directory that has a semantic_model.yaml counts as a schema."""
    out = set()
    for k in store.list_keys("fabric/"):
        if k.startswith("fabric/catalogs/"): continue
        rest = k[len("fabric/"):]
        if "/" not in rest: continue
        schema = rest.split("/", 1)[0]
        # Confirm: has semantic_model.yaml
        if store.exists(f"fabric/{schema}/semantic_model.yaml"):
            out.add(schema)
    return sorted(out)


def _list_artifacts_for(store, schema: str) -> List[str]:
    """All artifacts under fabric/<schema>/, as names (not full keys)."""
    prefix = f"fabric/{schema}/"
    return sorted(k[len(prefix):] for k in store.list_keys(prefix) if k.startswith(prefix))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--dry-run", action="store_true", help="print planned copies without writing")
    ap.add_argument("--catalog", default="local", help="target catalog name (default: local)")
    args = ap.parse_args()
    _load_env(args.env_file)

    from diracdata.config import settings_from_env
    from diracdata.stores import store_from_settings
    from diracdata.context.catalog_store import CatalogStore, _database_key, _catalog_key

    settings = settings_from_env(args.env_file)
    store = store_from_settings(settings)
    cat_store = CatalogStore(store)

    schemas = _discover_legacy_schemas(store)
    if not schemas:
        print("[migrate] no legacy fabric/<schema>/ directories found — nothing to migrate")
        return 0

    print(f"[migrate] target catalog: {args.catalog!r}")
    print(f"[migrate] discovered {len(schemas)} legacy schemas: {schemas}")
    print(f"[migrate] mode: {'DRY-RUN (no writes)' if args.dry_run else 'LIVE'}")
    print()

    total = {"copied": 0, "skipped": 0, "missing": 0, "would-copy": 0}
    for schema in schemas:
        arts = _list_artifacts_for(store, schema)
        print(f"[migrate] {schema}: {len(arts)} artifacts")
        for name in arts:
            src = f"fabric/{schema}/{name}"
            dst = _database_key(args.catalog, schema, name)
            status = _copy_artifact(store, src, dst, args.dry_run)
            key = "copied" if status == "copied" else "skipped" if "skipped" in status else \
                  "missing" if status == "missing" else "would-copy"
            total[key] = total.get(key, 0) + 1
            print(f"    {status:>20s}   {name}")

    # Author (or refresh) catalog.yaml so future CatalogStore reads know this catalog exists
    if not args.dry_run:
        cat_yaml = {
            "name": args.catalog,
            "engine": "duckdb",
            "description": f"Migrated from legacy single-schema fabric/*/ layout ({len(schemas)} databases)",
            "databases": schemas,
            "connection": {},
        }
        cat_store.put_catalog(args.catalog, "catalog.yaml", cat_yaml)
        print(f"\n[migrate] wrote {_catalog_key(args.catalog, 'catalog.yaml')}")

    print(f"\n[migrate] summary: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
