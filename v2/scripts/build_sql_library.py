#!/usr/bin/env python3
"""Build the v2 SQL library from query history and self-play coverage gaps."""

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
from diracdata_v2.learning.sql_library import SQLLibraryBuilder  # noqa: E402


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
    parser.add_argument("--schema-graph", required=True)
    parser.add_argument("--query-history", required=True)
    parser.add_argument("--data-root", default=str(V2_ROOT / "data"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--history-limit", type=int, default=80)
    parser.add_argument("--pattern-mode", choices=["llm", "heuristic", "off"], default="heuristic")
    parser.add_argument("--pattern-batch-size", type=int, default=20)
    parser.add_argument("--pattern-limit", type=int, default=80)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--object-prefix", default="v2/learning/artifacts")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args()

    settings = settings_from_env(args.env_file)
    run_id = args.run_id or f"{settings.catalog}_{settings.database}_{settings.schema}_sql_library"
    output_dir = Path(args.output_dir) if args.output_dir else V2_ROOT / "learning" / "artifacts" / run_id
    object_store = None if args.no_upload else object_store_from_settings(settings)
    schema_graph = json.loads(Path(args.schema_graph).read_text(encoding="utf-8"))
    pattern_generator = (
        V1ChatAdapter(chat_model_client_from_settings(settings))
        if args.pattern_mode == "llm"
        else None
    )

    result = SQLLibraryBuilder(
        pattern_generator=pattern_generator,
        pattern_batch_size=args.pattern_batch_size,
        pattern_limit=0 if args.pattern_mode == "off" else args.pattern_limit,
    ).build(
        schema_graph=schema_graph,
        query_history_path=Path(args.query_history),
        data_root=Path(args.data_root),
        catalog=settings.catalog,
        database=settings.database,
        schema=settings.schema,
        run_id=run_id,
        output_dir=output_dir,
        object_store=object_store,
        object_prefix=args.object_prefix,
        history_limit=args.history_limit,
        pattern_batch_size=args.pattern_batch_size,
        pattern_limit=0 if args.pattern_mode == "off" else args.pattern_limit,
    )
    entries = result.document["entries"]
    patterns = result.document.get("patterns", {})
    print(
        json.dumps(
            {
                "run_id": run_id,
                "local_path": str(result.local_path),
                "object_key": result.object_key,
                "entry_count": len(entries),
                "history_entries": sum(1 for item in entries.values() if item["source"] == "query_history"),
                "self_play_entries": sum(1 for item in entries.values() if item["source"] == "self_play"),
                "patterns": len(patterns),
                "missing_columns": len(result.document["coverage"]["columns_missing"]),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
