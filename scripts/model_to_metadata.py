#!/usr/bin/env python3
"""Retarget: fold a compiled semantic_model.yaml into the artifacts the BASE agent already consumes
on-demand -- metadata_descriptions.json (served by describe_columns) + value_domains.json. Complex
columns' ACCESS RECIPES land inside the long_description the analyst pulls, so the governed knowledge
reaches the agent through a channel it actually uses, with no separate semantic layer.

    PYTHONPATH=src .venv/bin/python scripts/model_to_metadata.py --schema fintech_complex
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diracdata.config import settings_from_env  # noqa: E402
from diracdata.context.fabric import fabric_store_from_settings  # noqa: E402
from diracdata.learning.compiler import SemanticModel  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--merge", action="store_true",
                    help="merge into existing metadata/value_domains instead of overwriting")
    args = ap.parse_args()

    settings = settings_from_env(args.env_file)
    fab = fabric_store_from_settings(settings)
    if not fab.has(args.schema, "semantic_model.yaml"):
        print(f"no semantic_model.yaml for {args.schema}", file=sys.stderr)
        return 1
    doc = yaml.safe_load(fab.read_text(args.schema, "semantic_model.yaml")) or {}
    sm = SemanticModel.from_doc(doc)

    meta = sm.to_metadata_descriptions()
    domains = sm.to_value_domains()

    if args.merge:
        cur_meta = fab.get(args.schema, "metadata_descriptions.json") or {}
        cur_dom = fab.get(args.schema, "value_domains.json") or {}
        for t, cols in meta["columns"].items():
            cur_meta.setdefault("columns", {}).setdefault(t, {}).update(cols)
        cur_meta.setdefault("tables", {}).update(meta["tables"])
        for t, cols in domains.items():
            cur_dom.setdefault(t, {}).update(cols)
        meta, domains = cur_meta, cur_dom

    fab.put(args.schema, "metadata_descriptions.json", meta)
    fab.put(args.schema, "value_domains.json", domains)

    n_cols = sum(len(c) for c in meta["columns"].values())
    n_recipes = sum(1 for cols in meta["columns"].values()
                    for d in cols.values() if "NESTED/COMPLEX" in (d.get("long_description") or ""))
    print(json.dumps({"schema": args.schema, "tables": len(meta["tables"]), "columns": n_cols,
                      "complex_columns_with_recipe": n_recipes,
                      "value_domain_columns": sum(len(c) for c in domains.values())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
