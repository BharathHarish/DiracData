#!/usr/bin/env python3
"""Build the v2 traversal AST from schema graph and SQL library artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.learning.schema_ast import SchemaASTBuilder  # noqa: E402
from diracdata_v2.settings import settings_from_env  # noqa: E402
from diracdata_v2.storage import object_store_from_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--schema-graph", required=True)
    parser.add_argument("--sql-library", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--object-prefix", default="v2/learning/artifacts")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args()

    settings = settings_from_env(args.env_file)
    run_id = args.run_id or f"{settings.catalog}_{settings.database}_{settings.schema}_schema_ast"
    output_dir = Path(args.output_dir) if args.output_dir else V2_ROOT / "learning" / "artifacts" / run_id
    object_store = None if args.no_upload else object_store_from_settings(settings)

    schema_graph = json.loads(Path(args.schema_graph).read_text(encoding="utf-8"))
    sql_library = json.loads(Path(args.sql_library).read_text(encoding="utf-8"))
    result = SchemaASTBuilder().build(
        schema_graph=schema_graph,
        sql_library=sql_library,
        catalog=settings.catalog,
        database=settings.database,
        schema=settings.schema,
        run_id=run_id,
        output_dir=output_dir,
        object_store=object_store,
        object_prefix=args.object_prefix,
    )

    indexes = result.document["indexes"]["by_kind"]
    print(
        json.dumps(
            {
                "run_id": run_id,
                "local_path": str(result.local_path),
                "object_key": result.object_key,
                "domains": len(indexes.get("domain", [])),
                "entities": len(indexes.get("entity", [])),
                "tables": len(indexes.get("table", [])),
                "columns": len(indexes.get("column", [])),
                "linked_nodes": len(result.document["indexes"]["sql_library_ids_by_node"]),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
