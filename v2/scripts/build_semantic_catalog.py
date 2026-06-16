#!/usr/bin/env python3
"""Build the v2 semantic catalog artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.semantic_catalog import SemanticCatalogBuilder  # noqa: E402
from diracdata_v2.settings import settings_from_env  # noqa: E402
from diracdata_v2.storage import object_store_from_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--metadata-descriptions", required=True)
    parser.add_argument("--schema-ast", default=None)
    parser.add_argument("--sql-library", required=True)
    parser.add_argument("--artifact-root", default=str(V2_ROOT / "learning" / "artifacts"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--object-prefix", default="v2/learning/artifacts")
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()

    settings = settings_from_env(args.env_file)
    run_id = args.run_id or f"{settings.catalog}_{settings.database}_{settings.schema}_semantic_catalog"
    object_store = None
    if args.upload:
        object_store = object_store_from_settings(settings)

    metadata = json.loads(Path(args.metadata_descriptions).read_text(encoding="utf-8"))
    schema_ast = json.loads(Path(args.schema_ast).read_text(encoding="utf-8")) if args.schema_ast else None
    sql_library = json.loads(Path(args.sql_library).read_text(encoding="utf-8"))
    result = SemanticCatalogBuilder().build(
        metadata_descriptions=metadata,
        schema_ast=schema_ast,
        sql_library=sql_library,
        catalog=settings.catalog,
        database=settings.database,
        schema=settings.schema,
        run_id=run_id,
        output_dir=Path(args.artifact_root) / run_id,
        object_store=object_store,
        object_prefix=args.object_prefix,
    )
    print(
        json.dumps(
            {
                "run_id": run_id,
                "semantic_catalog_path": str(result.local_path),
                "object_key": result.object_key,
                **result.document["validation"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
