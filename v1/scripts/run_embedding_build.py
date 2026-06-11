"""Build embedding artifacts from an existing learned retrieval document artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import settings_from_env
from diracdata.learning import EmbeddingIndexBuilder, learning_collection_from_profile_artifact
from diracdata.learning.paths import learning_artifact_key
from diracdata.storage import object_store_from_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--profile-artifact", default=None)
    parser.add_argument("--retrieval-documents-artifact", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = settings_from_env(".env")
    object_store = object_store_from_settings(settings, create_bucket_if_missing=True)
    profile_artifact = args.profile_artifact or learning_artifact_key(
        settings,
        run_id=args.run_id,
        relative_path="profiles/table_profiles.json",
    )
    retrieval_documents_artifact = args.retrieval_documents_artifact or learning_artifact_key(
        settings,
        run_id=args.run_id,
        relative_path="retrieval/documents.jsonl",
    )
    collection = learning_collection_from_profile_artifact(
        object_store=object_store,
        profile_artifact_key=profile_artifact,
    )
    result = EmbeddingIndexBuilder(
        settings=settings,
        object_store=object_store,
        progress_callback=_progress,
    ).build(
        collection=collection,
        retrieval_documents_artifact_key=retrieval_documents_artifact,
    )
    print(json.dumps(result.__dict__, indent=2), flush=True)


def _progress(message: str) -> None:
    print(f"[embeddings] {message}", flush=True)


if __name__ == "__main__":
    main()
