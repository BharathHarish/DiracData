#!/usr/bin/env python3
"""Build long-context markdown documents for the NL AST agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.context import build_description_docs  # noqa: E402
from diracdata_v2.settings import settings_from_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument(
        "--descriptions-path",
        default=str(V2_ROOT / "context" / "retail_analytics_metadata_descriptions.json"),
    )
    parser.add_argument("--schema", default=None)
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--sample-values-limit", type=int, default=8)
    args = parser.parse_args()

    settings = settings_from_env(args.env_file)
    schema = args.schema or settings.schema
    data_root = Path(args.data_root) if args.data_root else settings.data_root
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else V2_ROOT / "context" / schema
    )
    result = build_description_docs(
        descriptions_path=Path(args.descriptions_path),
        data_root=data_root,
        schema_name=schema,
        output_dir=output_dir,
        sample_values_limit=args.sample_values_limit,
    )
    print(
        json.dumps(
            {
                "table_descriptions_path": str(result.table_descriptions_path),
                "table_column_descriptions_path": str(result.table_column_descriptions_path),
                "table_count": result.table_count,
                "column_count": result.column_count,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
