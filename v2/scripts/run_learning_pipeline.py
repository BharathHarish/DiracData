#!/usr/bin/env python3
"""Run the full v2 learning pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.learning import LearningPipeline, LearningPipelineConfig  # noqa: E402
from diracdata_v2.llms.model_factory import chat_completion_client_from_settings  # noqa: E402
from diracdata_v2.settings import settings_from_env  # noqa: E402
from diracdata_v2.storage import object_store_from_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--metadata-descriptions", required=True)
    parser.add_argument("--query-history", required=True)
    parser.add_argument("--data-root", default=str(V2_ROOT / "data"))
    parser.add_argument("--artifact-root", default=str(V2_ROOT / "learning" / "artifacts"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--history-limit", type=int, default=80)
    parser.add_argument("--learning-model-profile", default=None)
    parser.add_argument("--pattern-batch-size", type=int, default=20)
    parser.add_argument("--pattern-limit", type=int, default=80)
    parser.add_argument("--disable-context-fabric", action="store_true")
    parser.add_argument("--include-experiences", action="store_true")
    parser.add_argument("--experience-prefix", default="experience/candidates")
    parser.add_argument("--experience-limit", type=int, default=None)
    parser.add_argument("--auto-approve-valid-self-play", action="store_true")
    parser.add_argument("--self-play-limit", type=int, default=None)
    parser.add_argument("--context-query-history-limit", type=int, default=None)
    parser.add_argument("--context-nl-sql-pair-limit", type=int, default=None)
    parser.add_argument("--disable-semantic-coverage", action="store_true")
    parser.add_argument("--semantic-coverage-strict-agentic", action="store_true")
    parser.add_argument("--self-play-gap-limit", type=int, default=50)
    parser.add_argument("--enable-semantic-self-play-sql", action="store_true")
    parser.add_argument("--semantic-self-play-sql-task-limit", type=int, default=20)
    parser.add_argument("--semantic-self-play-sql-batch-size", type=int, default=25)
    parser.add_argument("--semantic-self-play-sql-batch-limit", type=int, default=None)
    parser.add_argument("--semantic-self-play-sql-repair-iterations", type=int, default=2)
    parser.add_argument("--semantic-self-play-sql-validation-rows", type=int, default=5)
    parser.add_argument("--semantic-self-play-sql-strict-agentic", action="store_true")
    parser.add_argument("--profile-data", action="store_true")
    parser.add_argument("--disable-semantic-pathways", action="store_true")
    parser.add_argument("--pathway-batch-size", type=int, default=20)
    parser.add_argument("--pathway-limit", type=int, default=None)
    parser.add_argument("--disable-column-nuances", action="store_true")
    parser.add_argument("--nuance-max-columns", type=int, default=40)
    parser.add_argument("--nuance-max-values", type=int, default=20)
    parser.add_argument("--nuance-batch-size", type=int, default=10)
    parser.add_argument("--nuance-strict-agentic", action="store_true")
    parser.add_argument("--disable-join-graph", action="store_true")
    parser.add_argument("--join-max-tables", type=int, default=30)
    parser.add_argument("--join-max-columns-per-table", type=int, default=16)
    parser.add_argument("--join-max-candidate-edges", type=int, default=80)
    parser.add_argument("--join-max-overlap-checks", type=int, default=800)
    parser.add_argument("--join-sample-limit", type=int, default=1000)
    parser.add_argument("--join-min-candidate-score", type=float, default=0.35)
    parser.add_argument("--join-min-candidate-distinct-count", type=int, default=2)
    parser.add_argument("--join-batch-size", type=int, default=8)
    parser.add_argument("--join-strict-agentic", action="store_true")
    parser.add_argument("--disable-semantic-assertions", action="store_true")
    parser.add_argument("--assertion-limit", type=int, default=20)
    parser.add_argument("--assertion-batch-size", type=int, default=6)
    parser.add_argument("--assertion-strict-agentic", action="store_true")
    parser.add_argument(
        "--nl-sql-pairs",
        action="append",
        default=None,
        help=(
            "Trusted NL-SQL pair CSV path. May be repeated or comma-separated. "
            "Defaults to DIRACDATA_V2_NL_SQL_PAIR_PATHS."
        ),
    )
    parser.add_argument("--nl-sql-pair-limit", type=int, default=None)
    parser.add_argument(
        "--nl-sql-pair-review-status",
        default=os.environ.get("DIRACDATA_V2_NL_SQL_PAIR_REVIEW_STATUS", "approved"),
    )
    parser.add_argument("--object-prefix", default="v2/learning/artifacts")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args()

    settings = settings_from_env(args.env_file)
    run_id = args.run_id or f"{settings.catalog}_{settings.database}_{settings.schema}_learning"
    object_store = None if args.no_upload else object_store_from_settings(settings)
    pipeline = LearningPipeline(
        generator=chat_completion_client_from_settings(
            settings,
            profile_id=args.learning_model_profile or settings.context_compiler_model_profile,
        )
    )
    nl_sql_pair_paths = _path_list(
        explicit=args.nl_sql_pairs,
        env_value=os.environ.get("DIRACDATA_V2_NL_SQL_PAIR_PATHS", ""),
    )
    nl_sql_pair_limit = (
        args.nl_sql_pair_limit
        if args.nl_sql_pair_limit is not None
        else _optional_int_env("DIRACDATA_V2_NL_SQL_PAIR_LIMIT")
    )
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
            nl_sql_pair_paths=tuple(nl_sql_pair_paths),
            nl_sql_pair_limit=nl_sql_pair_limit,
            nl_sql_pair_review_status=args.nl_sql_pair_review_status,
            enable_context_fabric=not args.disable_context_fabric,
            include_experiences=args.include_experiences,
            experience_prefix=args.experience_prefix,
            experience_limit=args.experience_limit,
            auto_approve_valid_self_play=args.auto_approve_valid_self_play,
            self_play_limit=args.self_play_limit,
            context_query_history_limit=args.context_query_history_limit,
            context_nl_sql_pair_limit=args.context_nl_sql_pair_limit,
            enable_semantic_coverage=not args.disable_semantic_coverage,
            semantic_coverage_strict_agentic=args.semantic_coverage_strict_agentic,
            self_play_gap_limit=args.self_play_gap_limit,
            enable_semantic_self_play_sql=args.enable_semantic_self_play_sql,
            semantic_self_play_sql_task_limit=args.semantic_self_play_sql_task_limit,
            semantic_self_play_sql_batch_size=args.semantic_self_play_sql_batch_size,
            semantic_self_play_sql_batch_limit=args.semantic_self_play_sql_batch_limit,
            semantic_self_play_sql_repair_iterations=args.semantic_self_play_sql_repair_iterations,
            semantic_self_play_sql_validation_rows=args.semantic_self_play_sql_validation_rows,
            semantic_self_play_sql_strict_agentic=args.semantic_self_play_sql_strict_agentic,
            profile_data=args.profile_data,
            enable_semantic_pathways=not args.disable_semantic_pathways,
            pathway_batch_size=args.pathway_batch_size,
            pathway_limit=args.pathway_limit,
            enable_column_nuances=not args.disable_column_nuances,
            nuance_max_columns=args.nuance_max_columns,
            nuance_max_values=args.nuance_max_values,
            nuance_batch_size=args.nuance_batch_size,
            nuance_strict_agentic=args.nuance_strict_agentic,
            enable_join_graph=not args.disable_join_graph,
            join_max_tables=args.join_max_tables,
            join_max_columns_per_table=args.join_max_columns_per_table,
            join_max_candidate_edges=args.join_max_candidate_edges,
            join_max_overlap_checks=args.join_max_overlap_checks,
            join_sample_limit=args.join_sample_limit,
            join_min_candidate_score=args.join_min_candidate_score,
            join_min_candidate_distinct_count=args.join_min_candidate_distinct_count,
            join_batch_size=args.join_batch_size,
            join_strict_agentic=args.join_strict_agentic,
            enable_semantic_assertions=not args.disable_semantic_assertions,
            assertion_limit=args.assertion_limit,
            assertion_batch_size=args.assertion_batch_size,
            assertion_strict_agentic=args.assertion_strict_agentic,
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


def _path_list(*, explicit: list[str] | None, env_value: str) -> list[Path]:
    values: list[str] = []
    if explicit:
        values.extend(explicit)
    elif env_value:
        values.append(env_value)
    paths: list[Path] = []
    for value in values:
        for item in value.split(","):
            clean = item.strip()
            if clean:
                paths.append(Path(clean))
    return paths


def _optional_int_env(key: str) -> int | None:
    value = os.environ.get(key)
    return int(value) if value else None


if __name__ == "__main__":
    raise SystemExit(main())
