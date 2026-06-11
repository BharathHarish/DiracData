"""Inspect active learning artifacts and optional vector-search hits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import settings_from_env
from diracdata.learning.paths import active_learning_artifact_key
from diracdata.retrieval import VectorIndexStore
from diracdata.storage import object_store_from_settings


DEFAULT_QUERIES = [
    "total payment volume successful transaction amount",
    "payment rail method UPI credit card NEFT IMPS route",
    "merchant geography state location segmentation",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = settings_from_env(args.env_file)
    store = object_store_from_settings(settings)
    active_manifest = _read_json(store, settings, "manifest.json")
    descriptions = _read_json(store, settings, "descriptions/metadata_descriptions.json")
    graph_manifest = _read_json(store, settings, "context_graph/manifest.json")
    embedding_manifest = _read_json(store, settings, "embeddings/manifest.json")
    join_pairs = _jsonl(store.read_text(_active_key(settings, "joins/joinable_pairs.jsonl")))
    sample_prefix = _active_run_prefix(settings, str(active_manifest["active_run_id"])) + "/samples/"
    sample_keys = sorted(key for key in store.list_keys(sample_prefix) if key.endswith(".csv"))

    queries = args.query or DEFAULT_QUERIES
    vector_results = _vector_results(
        settings=settings,
        store=store,
        embedding_manifest=embedding_manifest,
        queries=queries,
        top_k=args.top_k,
    )
    print(
        json.dumps(
            {
                "scope": {
                    "catalog": settings.catalog,
                    "database": settings.database,
                    "schema": settings.schema,
                },
                "active_run_id": active_manifest["active_run_id"],
                "table_count": len(descriptions["tables"]),
                "column_count": sum(len(columns) for columns in descriptions["columns"].values()),
                "sample_csv_count": len(sample_keys),
                "joinable_pair_count": len(join_pairs),
                "context_graph": {
                    "node_count": graph_manifest["node_count"],
                    "edge_count": graph_manifest["edge_count"],
                    "query_pattern_count": graph_manifest["query_pattern_count"],
                    "retrieval_document_count": graph_manifest["retrieval_document_count"],
                },
                "embeddings": {
                    "status": embedding_manifest["status"],
                    "provider": embedding_manifest["provider"],
                    "model": embedding_manifest["model"],
                    "vector_count": embedding_manifest["vector_count"],
                    "vector_dimensions": embedding_manifest["vector_dimensions"],
                    "active_vector_index": embedding_manifest.get("active_vector_index"),
                },
                "sample_artifacts": sample_keys,
                "table_descriptions": {
                    table_name: description["short_description"]
                    for table_name, description in sorted(descriptions["tables"].items())
                },
                "joinable_pairs": join_pairs,
                "vector_queries": vector_results,
            },
            indent=2,
        ),
        flush=True,
    )


def _vector_results(
    *,
    settings,
    store,
    embedding_manifest: dict[str, object],
    queries: list[str],
    top_k: int,
) -> list[dict[str, object]]:
    if embedding_manifest.get("status") != "ok":
        return []
    vector_store = VectorIndexStore(settings=settings, object_store=store)
    vectors_key = str(embedding_manifest["active_vectors_artifact_key"])
    vector_index = embedding_manifest.get("active_vector_index")
    if not isinstance(vector_index, dict):
        vector_index = None
    results = []
    for query in queries:
        search_result = vector_store.search_text(
            query=query,
            vectors_artifact_key=vectors_key,
            vector_index=vector_index,
            top_k=top_k,
        )
        results.append(
            {
                "query": query,
                "backend": search_result.backend,
                "hits": [
                    {
                        "rank": index + 1,
                        "table_name": hit.table_name,
                        "column_name": hit.column_name,
                        "score": round(hit.score, 4),
                    }
                    for index, hit in enumerate(search_result.hits)
                ],
            }
        )
    return results


def _read_json(store, settings, relative_path: str) -> dict[str, object]:
    payload = store.read_json(_active_key(settings, relative_path))
    if not isinstance(payload, dict):
        raise ValueError(f"Active artifact must be a JSON object: {relative_path}")
    return payload


def _active_key(settings, relative_path: str) -> str:
    return active_learning_artifact_key(settings, relative_path=relative_path)


def _active_run_prefix(settings, run_id: str) -> str:
    return "/".join(
        [
            "artifacts",
            "learning",
            settings.catalog,
            settings.database,
            settings.schema,
            run_id,
        ]
    )


def _jsonl(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


if __name__ == "__main__":
    main()
