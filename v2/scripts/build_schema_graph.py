#!/usr/bin/env python3
"""Build the v2 schema graph document from metadata descriptions."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))
sys.path.insert(0, str(ROOT / "v1" / "src"))

from diracdata.config.settings import settings_from_env  # noqa: E402
from diracdata.llms.chat_models import chat_model_client_from_settings  # noqa: E402
from diracdata.storage.factory import object_store_from_settings  # noqa: E402
from diracdata_v2.learning.schema_graph import SchemaGraphBuilder, load_prompt  # noqa: E402


@dataclass
class V1ChatAdapter:
    client: Any

    def complete(self, messages: list[dict[str, str]]) -> str:
        from diracdata.llms.chat_models import ChatModelMessage

        return self.client.complete(
            [ChatModelMessage(role=item["role"], content=item["content"]) for item in messages]
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument(
        "--metadata-descriptions",
        default=str(V2_ROOT / "context" / "metadata_descriptions.json"),
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Defaults to v2/learning/artifacts/<run-id>",
    )
    parser.add_argument("--object-prefix", default="v2/learning/artifacts")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args()

    settings = settings_from_env(args.env_file)
    run_id = args.run_id or f"{settings.catalog}_{settings.database}_{settings.schema}_schema_graph"
    output_dir = Path(args.output_dir) if args.output_dir else V2_ROOT / "learning" / "artifacts" / run_id
    metadata = json.loads(Path(args.metadata_descriptions).read_text(encoding="utf-8"))

    client = chat_model_client_from_settings(settings)
    builder = SchemaGraphBuilder(generator=V1ChatAdapter(client), prompt=load_prompt())
    object_store = None if args.no_upload else object_store_from_settings(settings)
    result = builder.build(
        metadata_descriptions=metadata,
        catalog=settings.catalog,
        database=settings.database,
        schema=settings.schema,
        run_id=run_id,
        output_dir=output_dir,
        object_store=object_store,
        object_prefix=args.object_prefix,
    )

    domains = len(result.document["indexes"]["by_kind"].get("domain", [])) - 1
    entities = len(result.document["indexes"]["by_kind"].get("entity", []))
    tables = len(result.document["indexes"]["by_kind"].get("table", []))
    columns = len(result.document["indexes"]["by_kind"].get("column", []))
    print(
        json.dumps(
            {
                "run_id": run_id,
                "local_path": str(result.local_path),
                "object_key": result.object_key,
                "domains": domains,
                "entities": entities,
                "tables": tables,
                "columns": columns,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

