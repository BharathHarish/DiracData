#!/usr/bin/env python3
"""Run the full v2 learning pipeline."""

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
from diracdata_v2.learning import LearningPipeline, LearningPipelineConfig  # noqa: E402


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
    parser.add_argument("--metadata-descriptions", required=True)
    parser.add_argument("--query-history", required=True)
    parser.add_argument("--data-root", default=str(V2_ROOT / "data"))
    parser.add_argument("--artifact-root", default=str(V2_ROOT / "learning" / "artifacts"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--history-limit", type=int, default=80)
    parser.add_argument("--pattern-batch-size", type=int, default=20)
    parser.add_argument("--pattern-limit", type=int, default=80)
    parser.add_argument("--object-prefix", default="v2/learning/artifacts")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args()

    settings = settings_from_env(args.env_file)
    run_id = args.run_id or f"{settings.catalog}_{settings.database}_{settings.schema}_learning"
    object_store = None if args.no_upload else object_store_from_settings(settings)
    pipeline = LearningPipeline(generator=V1ChatAdapter(chat_model_client_from_settings(settings)))
    result = pipeline.run(
        config=LearningPipelineConfig(
            catalog=settings.catalog,
            database=settings.database,
            schema=settings.schema,
            metadata_descriptions_path=Path(args.metadata_descriptions),
            query_history_path=Path(args.query_history),
            data_root=Path(args.data_root),
            artifact_root=Path(args.artifact_root),
            run_id=run_id,
            object_prefix=args.object_prefix,
            history_limit=args.history_limit,
            pattern_batch_size=args.pattern_batch_size,
            pattern_limit=args.pattern_limit,
        ),
        object_store=object_store,
    )
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "manifest_path": str(result.manifest_path),
                "object_key": result.object_key,
                **result.manifest["summary"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
