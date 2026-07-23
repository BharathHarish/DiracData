#!/usr/bin/env python3
"""Build observed and candidate join graph artifacts from context sources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.context_fabric import (  # noqa: E402
    JoinMiner,
    build_normalized_corpus,
    table_columns_from_metadata,
)
from diracdata_v2.llms.model_factory import chat_completion_client_from_settings  # noqa: E402
from diracdata_v2.query import DuckDBEngine  # noqa: E402
from diracdata_v2.settings import settings_from_env  # noqa: E402
from diracdata_v2.storage import object_store_from_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--catalog", default="")
    parser.add_argument("--database", default="")
    parser.add_argument("--schema", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--metadata-descriptions-path",
        default=str(V2_ROOT / "context" / "retail_analytics_metadata_descriptions.json"),
    )
    parser.add_argument(
        "--query-history-path",
        default=str(V2_ROOT / "data" / "query_history" / "retail_analytics_query_history.csv"),
    )
    parser.add_argument("--nl-sql-pair-path", action="append", default=[])
    parser.add_argument("--query-history-limit", type=int, default=80)
    parser.add_argument("--nl-sql-pair-limit", type=int, default=80)
    parser.add_argument("--data-root", default=str(V2_ROOT / "data"))
    parser.add_argument("--profile-data", action="store_true")
    parser.add_argument("--max-tables", type=int, default=30)
    parser.add_argument("--max-columns-per-table", type=int, default=16)
    parser.add_argument("--max-candidate-edges", type=int, default=80)
    parser.add_argument("--max-overlap-checks", type=int, default=800)
    parser.add_argument("--sample-limit", type=int, default=1000)
    parser.add_argument("--min-candidate-score", type=float, default=0.35)
    parser.add_argument("--min-candidate-distinct-count", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument(
        "--learning-model-profile",
        default="",
        help="Model profile for agentic relationship classification. If omitted, emits evidence-only edges.",
    )
    parser.add_argument(
        "--strict-agentic",
        action="store_true",
        help="Fail if a configured learning model cannot classify every emitted join edge.",
    )
    parser.add_argument("--output-dir", default=str(V2_ROOT / "data" / "uat_runs" / "join_graph_phase4"))
    parser.add_argument("--sample-output-limit", type=int, default=5)
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--object-prefix", default="v2/learning/artifacts")
    args = parser.parse_args()

    settings = settings_from_env(args.env_file)
    catalog = args.catalog or settings.catalog
    database = args.database or settings.database
    schema = args.schema or settings.schema
    run_id = args.run_id or f"{schema}_join_graph_phase4"
    metadata_path = Path(args.metadata_descriptions_path)
    if not metadata_path.exists():
        print(json.dumps({"status": "error", "error": f"missing metadata file: {metadata_path}"}), file=sys.stderr)
        return 2
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    table_columns = table_columns_from_metadata(metadata)
    corpus = build_normalized_corpus(
        table_columns=table_columns,
        query_history_path=Path(args.query_history_path) if args.query_history_path else None,
        nl_sql_pair_paths=tuple(Path(item) for item in args.nl_sql_pair_path),
        query_history_limit=args.query_history_limit,
        nl_sql_pair_limit=args.nl_sql_pair_limit,
    )
    engine = (
        DuckDBEngine(data_root=Path(args.data_root), schema_name=schema)
        if args.profile_data
        else None
    )
    classifier = (
        chat_completion_client_from_settings(settings, profile_id=args.learning_model_profile)
        if args.learning_model_profile
        else None
    )
    object_store = None if args.no_upload else object_store_from_settings(settings)
    result = JoinMiner(
        engine=engine,
        classifier=classifier,
        max_tables=args.max_tables,
        max_columns_per_table=args.max_columns_per_table,
        max_candidate_edges=args.max_candidate_edges,
        max_overlap_checks=args.max_overlap_checks,
        sample_limit=args.sample_limit,
        min_candidate_score=args.min_candidate_score,
        min_candidate_distinct_count=args.min_candidate_distinct_count,
        batch_size=args.batch_size,
        strict_agentic=args.strict_agentic,
    ).build(
        corpus=corpus,
        metadata_descriptions=metadata,
        catalog=catalog,
        database=database,
        schema=schema,
        run_id=run_id,
        output_dir=Path(args.output_dir),
        object_store=object_store,
        object_prefix=args.object_prefix,
    )
    edges = list(result.document.get("join_edges", {}).values())
    profiles = list(result.document.get("column_profiles", {}).values())
    print(
        json.dumps(
            {
                "status": "ok",
                "run_id": run_id,
                "agentic": bool(classifier),
                "learning_model_profile": args.learning_model_profile,
                "profile_data": bool(engine),
                "normalized_corpus_summary": corpus.summary(),
                "join_summary": result.document.get("summary", {}),
                "generation_errors": result.document.get("generation_errors", []),
                "local_path": str(result.local_path) if result.local_path else "",
                "object_key": result.object_key or "",
                "edge_samples": edges[: max(0, args.sample_output_limit)],
                "column_profile_samples": profiles[: max(0, args.sample_output_limit)],
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
