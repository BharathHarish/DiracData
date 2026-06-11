"""Run the learning pipeline from any stage and print artifact/state summary."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import DiracDataSettings, settings_from_env
from diracdata.grounding import publish_business_grounding
from diracdata.learning import BusinessContext, LearningPipeline, LearningStage
from diracdata.llms import chat_model_client_from_settings
from diracdata.query_engines import query_engine_from_settings
from diracdata.storage import object_store_from_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--catalog", default=None, help="Override DIRACDATA_CATALOG for this run.")
    parser.add_argument("--database", default=None, help="Override DIRACDATA_DATABASE for this run.")
    parser.add_argument("--schema", default=None, help="Override DIRACDATA_SCHEMA for this run.")
    parser.add_argument(
        "--catalog-config",
        type=Path,
        default=None,
        help="Override DIRACDATA_CATALOG_CONFIG for this run.",
    )
    parser.add_argument(
        "--tables",
        default="all",
        help="Comma-separated tables, or 'all' for the configured catalog scope.",
    )
    parser.add_argument(
        "--business-context-file",
        type=Path,
        default=None,
        help="Required when starting from data_collection. JSON file with business context.",
    )
    parser.add_argument(
        "--business-grounding-file",
        type=Path,
        default=None,
        help=(
            "Optional business grounding YAML. Defaults to "
            "conf/business_grounding/{catalog}.{database}.{schema}.yaml when present."
        ),
    )
    parser.add_argument("--query-history-path", type=Path, default=None)
    parser.add_argument("--sample-limit", type=int, default=None)
    parser.add_argument("--distinct-limit", type=int, default=None)
    parser.add_argument("--top-values-limit", type=int, default=None)
    parser.add_argument("--context-distinct-values-limit", type=int, default=None)
    parser.add_argument("--description-column-batch-size", type=int, default=None)
    parser.add_argument(
        "--artifact-strategy",
        choices=["deterministic", "agentic"],
        default=None,
        help="Override DIRACDATA_LEARNING_ARTIFACT_STRATEGY for this run.",
    )
    parser.add_argument(
        "--context-mode",
        choices=["linear", "schema_ast"],
        default=None,
        help="Override DIRACDATA_LEARNING_CONTEXT_MODE for this run.",
    )
    parser.add_argument(
        "--start-stage",
        choices=[stage.value for stage in LearningStage],
        default=None,
    )
    parser.add_argument(
        "--end-stage",
        choices=[stage.value for stage in LearningStage],
        default=None,
    )
    parser.add_argument(
        "--publish-business-grounding",
        dest="publish_business_grounding",
        action="store_true",
        help="Publish business grounding into active artifacts before the run.",
    )
    parser.add_argument(
        "--skip-publish-business-grounding",
        dest="publish_business_grounding",
        action="store_false",
        help="Skip grounding publication even when a grounding file is present.",
    )
    parser.set_defaults(publish_business_grounding=None)
    parser.add_argument(
        "--list-stages",
        action="store_true",
        help="Print the resolved stage sequence and exit.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = _settings_with_learning_overrides(settings_from_env(args.env_file), args)
    if args.list_stages:
        print(
            json.dumps(
                {
                    "learning_artifact_strategy": settings.learning_artifact_strategy,
                    "learning_context_mode": settings.learning_context_mode,
                    "stage_sequence": [
                        stage.value for stage in _stage_sequence_for_strategy(settings.learning_artifact_strategy)
                    ],
                },
                indent=2,
            ),
            flush=True,
        )
        return

    object_store = object_store_from_settings(settings, create_bucket_if_missing=True)
    query_engine = query_engine_from_settings(settings)
    pipeline = LearningPipeline(
        settings=settings,
        query_engine=query_engine,
        object_store=object_store,
        llm_client=chat_model_client_from_settings(settings),
        sample_limit=args.sample_limit,
        distinct_limit=args.distinct_limit,
        top_values_limit=args.top_values_limit,
        context_distinct_values_limit=args.context_distinct_values_limit,
        description_column_batch_size=args.description_column_batch_size,
        progress_callback=_progress,
    )

    run_id = args.run_id or settings.learning_run_id
    business_grounding_path = _resolve_business_grounding_path(settings=settings, path=args.business_grounding_file)
    business_grounding = _load_business_grounding(business_grounding_path)
    business_context = (
        BusinessContext.from_json_file(args.business_context_file)
        if args.business_context_file is not None
        else None
    )

    try:
        if _should_publish_business_grounding(args.publish_business_grounding, business_grounding_path):
            _progress("phase: publish business grounding")
            publish_business_grounding(
                settings=settings,
                object_store=object_store,
                source_path=business_grounding_path,
                query_engine=query_engine,
            )

        result = pipeline.run_stages(
            business_context=business_context,
            run_id=run_id,
            tables=_parse_tables(args.tables),
            query_history_path=args.query_history_path,
            business_grounding=business_grounding,
            start_stage=args.start_stage,
            end_stage=args.end_stage,
        )
    finally:
        query_engine.close()

    print(
        json.dumps(
            _result_summary(
                settings=settings,
                run_id=run_id,
                result=result,
                business_context_file=args.business_context_file,
                business_grounding_file=business_grounding_path,
                query_history_path=args.query_history_path,
            ),
            indent=2,
        ),
        flush=True,
    )


def _result_summary(
    *,
    settings: DiracDataSettings,
    run_id: str,
    result,
    business_context_file: Path | None,
    business_grounding_file: Path | None,
    query_history_path: Path | None,
) -> dict[str, object]:
    state = result.state
    return {
        "status": "ok",
        "run_id": run_id,
        "scope": {
            "catalog": settings.catalog,
            "database": settings.database,
            "schema": settings.schema,
        },
        "learning_artifact_strategy": settings.learning_artifact_strategy,
        "learning_context_mode": settings.learning_context_mode,
        "executed_stages": [stage.value for stage in result.executed_stages],
        "business_context_file": str(business_context_file) if business_context_file is not None else None,
        "business_grounding_file": (
            str(business_grounding_file) if business_grounding_file is not None else None
        ),
        "query_history_path": str(query_history_path) if query_history_path is not None else None,
        "artifacts": {
            "profile_artifact_key": (
                state.collection.profile_artifact_key if state.collection is not None else None
            ),
            "llm_context_artifact_key": (
                state.collection.llm_context_artifact_key if state.collection is not None else None
            ),
            "description_artifact_key": state.description_artifact_key,
            "joinable_pairs_artifact_key": state.joinable_pairs_artifact_key,
            "context_graph_manifest_artifact_key": state.context_graph_manifest_artifact_key,
            "retrieval_documents_artifact_key": state.retrieval_documents_artifact_key,
            "retrieval_index_artifact_key": state.retrieval_index_artifact_key,
            "query_libraries_manifest_artifact_key": state.query_libraries_manifest_artifact_key,
            "nuance_manifest_artifact_key": state.nuance_manifest_artifact_key,
            "embedding_manifest_artifact_key": state.embedding_manifest_artifact_key,
            "schema_summary_artifact_key": state.schema_summary_artifact_key,
            "semantic_map_artifact_key": state.semantic_map_artifact_key,
            "schema_ast_manifest_artifact_key": state.schema_ast_manifest_artifact_key,
            "context_artifact_key": state.context.context_artifact_key if state.context is not None else None,
        },
        "table_count": len(state.collection.table_profiles) if state.collection is not None else None,
        "context_published": state.context is not None,
    }


def _settings_with_learning_overrides(
    settings: DiracDataSettings,
    args: argparse.Namespace,
) -> DiracDataSettings:
    payload = dict(settings.__dict__)
    if args.catalog is not None:
        payload["catalog"] = args.catalog
    if args.database is not None:
        payload["database"] = args.database
    if args.schema is not None:
        payload["schema"] = args.schema
    if args.catalog_config is not None:
        payload["catalog_config"] = args.catalog_config
    if args.run_id is not None:
        payload["learning_run_id"] = args.run_id
    if args.artifact_strategy is not None:
        payload["learning_artifact_strategy"] = args.artifact_strategy
    if args.context_mode is not None:
        payload["learning_context_mode"] = args.context_mode
    return replace(settings, **payload)


def _parse_tables(raw_tables: str) -> list[str] | None:
    if raw_tables.strip() == "" or raw_tables.strip().lower() == "all":
        return None
    return [table.strip() for table in raw_tables.split(",") if table.strip()]


def _default_grounding_path(settings: DiracDataSettings) -> str:
    return f"conf/business_grounding/{settings.catalog}.{settings.database}.{settings.schema}.yaml"


def _resolve_business_grounding_path(
    *,
    settings: DiracDataSettings,
    path: Path | None,
) -> Path | None:
    source = path or Path(_default_grounding_path(settings))
    return source if source.exists() else None


def _load_business_grounding(source: Path | None) -> dict[str, object] | None:
    if source is None:
        return None
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError(f"Business grounding YAML must be an object: {source}")
    return payload


def _should_publish_business_grounding(flag: bool | None, path: Path | None) -> bool:
    if path is None:
        return False
    if flag is None:
        return True
    return flag


def _progress(message: str) -> None:
    print(f"[learning] {message}", flush=True)


def _stage_sequence_for_strategy(strategy: str) -> list[LearningStage]:
    sequence = [
        LearningStage.DATA_COLLECTION,
        LearningStage.DESCRIPTION_GENERATION,
        LearningStage.JOIN_DISCOVERY,
        LearningStage.CONTEXT_GRAPH_BUILDING,
        LearningStage.EMBEDDING_GENERATION,
    ]
    if strategy.strip().lower() == "agentic":
        sequence.append(LearningStage.AGENTIC_ARTIFACT_GENERATION)
    else:
        sequence.extend(
            [
                LearningStage.QUERY_LIBRARY_BUILDING,
                LearningStage.NUANCE_BUILDING,
            ]
        )
    sequence.append(LearningStage.CONTEXT_TRAINING)
    return sequence


if __name__ == "__main__":
    main()
