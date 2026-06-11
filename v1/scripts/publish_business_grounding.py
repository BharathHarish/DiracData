"""Validate and publish business grounding YAML into active artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.agents.artifacts import LearnedArtifactRepository
from diracdata.config import settings_from_env
from diracdata.grounding import publish_business_grounding
from diracdata.query_engines import query_engine_from_settings
from diracdata.storage import object_store_from_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--grounding-file",
        type=Path,
        default=None,
        help="Path to customer business grounding YAML. Defaults to conf/business_grounding/{catalog}.{database}.{schema}.yaml.",
    )
    parser.add_argument(
        "--skip-ground-truth-validation",
        action="store_true",
        help="Validate references but skip executing ground_truth_sql entries.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = settings_from_env(args.env_file)
    source_path = args.grounding_file or Path(
        f"conf/business_grounding/{settings.catalog}.{settings.database}.{settings.schema}.yaml"
    )
    object_store = object_store_from_settings(settings, create_bucket_if_missing=True)
    repository = LearnedArtifactRepository(settings=settings, object_store=object_store)
    query_engine = query_engine_from_settings(settings)
    try:
        validation = publish_business_grounding(
            settings=settings,
            object_store=object_store,
            source_path=source_path,
            learned_repository=repository,
            query_engine=query_engine,
            validate_ground_truth=not args.skip_ground_truth_validation,
        )
    finally:
        query_engine.close()

    print(
        json.dumps(
            {
                "status": "published",
                "source_path": str(source_path),
                "yaml_key": validation.yaml_key,
                "json_key": validation.json_key,
                "warnings": validation.warnings,
                "sections": {
                    section: len(validation.normalized.get(section, []))
                    for section in [
                        "glossary",
                        "definitions",
                        "defaults",
                        "metrics",
                        "sql_templates",
                        "ground_truth_sql",
                    ]
                },
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
