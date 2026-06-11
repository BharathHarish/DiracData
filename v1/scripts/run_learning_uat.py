"""Run staged learning UAT and verify artifact coverage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from diracdata.config import settings_from_env
from diracdata.grounding import publish_business_grounding
from diracdata.learning import BusinessContext, LearningPipeline, LearningStage
from diracdata.llms import chat_model_client_from_settings
from diracdata.query_engines import query_engine_from_settings
from diracdata.storage import object_store_from_settings
from run_learning_pipeline import (  # noqa: E402
    _load_business_grounding,
    _parse_tables,
    _resolve_business_grounding_path,
    _settings_with_learning_overrides,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--run-id", default="uat_full_schema")
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
        default=Path("conf/business_contexts/commerce_pod.json"),
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
    parser.add_argument("--sample-limit", type=int, default=None)
    parser.add_argument("--distinct-limit", type=int, default=None)
    parser.add_argument("--top-values-limit", type=int, default=None)
    parser.add_argument("--context-distinct-values-limit", type=int, default=None)
    parser.add_argument("--description-column-batch-size", type=int, default=None)
    parser.add_argument("--query-history-path", type=Path, default=None)
    parser.add_argument(
        "--artifact-strategy",
        choices=["deterministic", "agentic"],
        default=None,
    )
    parser.add_argument(
        "--context-mode",
        choices=["linear", "schema_ast"],
        default=None,
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = _settings_with_learning_overrides(settings_from_env(args.env_file), args)
    object_store = object_store_from_settings(settings, create_bucket_if_missing=True)
    query_engine = query_engine_from_settings(settings)
    business_grounding_path = _resolve_business_grounding_path(
        settings=settings,
        path=args.business_grounding_file,
    )
    business_grounding = _load_business_grounding(business_grounding_path)
    tables = _parse_tables(args.tables)

    try:
        selected_tables = tables or query_engine.list_tables()
        _print_header(
            {
                "run_id": args.run_id,
                "catalog": settings.catalog,
                "database": settings.database,
                "schema": settings.schema,
                "table_count": len(selected_tables),
                "business_context_file": str(args.business_context_file),
                "business_grounding_file": (
                    str(business_grounding_path) if business_grounding_path is not None else None
                ),
                "query_history_path": str(args.query_history_path) if args.query_history_path else None,
                "learning_artifact_strategy": settings.learning_artifact_strategy,
                "learning_context_mode": settings.learning_context_mode,
                "start_stage": args.start_stage,
                "end_stage": args.end_stage,
                "description_column_batch_size": (
                    args.description_column_batch_size
                    or settings.learning_description_column_batch_size
                ),
            }
        )
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
        if business_grounding_path is not None:
            _progress("phase: publish business grounding")
            publish_business_grounding(
                settings=settings,
                object_store=object_store,
                source_path=business_grounding_path,
                query_engine=query_engine,
            )

        should_load_business_context = (
            args.start_stage is None or args.start_stage == LearningStage.DATA_COLLECTION.value
        )
        business_context = (
            BusinessContext.from_json_file(args.business_context_file)
            if should_load_business_context
            else None
        )
        result = pipeline.run_stages(
            business_context=business_context,
            run_id=args.run_id,
            tables=tables,
            query_history_path=args.query_history_path,
            business_grounding=business_grounding,
            start_stage=args.start_stage,
            end_stage=args.end_stage,
        )
    finally:
        query_engine.close()

    report = _verify_artifacts(
        settings=settings,
        object_store=object_store,
        run_id=args.run_id,
        profile_key=result.state.collection.profile_artifact_key if result.state.collection else None,
        description_key=result.state.description_artifact_key,
        joinable_pairs_key=result.state.joinable_pairs_artifact_key,
        context_graph_key=result.state.context_graph_manifest_artifact_key,
        query_libraries_key=result.state.query_libraries_manifest_artifact_key,
        nuance_key=result.state.nuance_manifest_artifact_key,
        retrieval_index_key=result.state.retrieval_index_artifact_key,
        embedding_manifest_key=result.state.embedding_manifest_artifact_key,
        context_key=result.state.context.context_artifact_key if result.state.context else None,
        schema_ast_key=result.state.schema_ast_manifest_artifact_key,
        executed_stages=[stage.value for stage in result.executed_stages],
        learning_artifact_strategy=settings.learning_artifact_strategy,
        learning_context_mode=settings.learning_context_mode,
        business_grounding_expected=business_grounding_path is not None,
    )
    print(json.dumps(report, indent=2), flush=True)


def _print_header(payload: dict[str, object]) -> None:
    print("Learning UAT", flush=True)
    print(json.dumps(payload, indent=2), flush=True)


def _progress(message: str) -> None:
    print(f"[learning] {message}", flush=True)


def _verify_artifacts(
    *,
    settings,
    object_store,
    run_id: str,
    profile_key: str | None,
    description_key: str | None,
    joinable_pairs_key: str | None,
    context_graph_key: str | None,
    query_libraries_key: str | None,
    nuance_key: str | None,
    retrieval_index_key: str | None,
    embedding_manifest_key: str | None,
    context_key: str | None,
    schema_ast_key: str | None,
    executed_stages: list[str],
    learning_artifact_strategy: str,
    learning_context_mode: str,
    business_grounding_expected: bool,
) -> dict[str, object]:
    required = {
        "profile_key": profile_key,
        "description_key": description_key,
        "joinable_pairs_key": joinable_pairs_key,
        "context_graph_key": context_graph_key,
        "retrieval_index_key": retrieval_index_key,
        "embedding_manifest_key": embedding_manifest_key,
    }
    if LearningStage.CONTEXT_TRAINING.value in executed_stages:
        required["context_key"] = context_key
    if learning_artifact_strategy == "agentic":
        required["query_libraries_key"] = query_libraries_key
        required["nuance_key"] = nuance_key
    else:
        if query_libraries_key is not None:
            required["query_libraries_key"] = query_libraries_key
        if nuance_key is not None:
            required["nuance_key"] = nuance_key
    missing_required = {name: key for name, key in required.items() if key is None}
    if missing_required:
        raise AssertionError(json.dumps({"missing_required_artifacts": missing_required}, indent=2))

    run_prefix = (
        f"artifacts/learning/{settings.catalog}/{settings.database}/{settings.schema}/{run_id}"
    )
    active_prefix = f"artifacts/learning/{settings.catalog}/{settings.database}/{settings.schema}/active"

    sample_keys = sorted(
        key
        for key in object_store.list_keys(f"{run_prefix}/samples/")
        if key.endswith(".csv")
    )
    batch_keys = sorted(object_store.list_keys(f"{run_prefix}/descriptions/batches/"))
    profile = object_store.read_json(profile_key or "")
    descriptions = object_store.read_json(description_key or "")
    context = object_store.read_json(context_key) if context_key is not None else None
    active_manifest_key = f"{active_prefix}/manifest.json"
    active_description_key = f"{active_prefix}/descriptions/metadata_descriptions.json"
    active_joinable_pairs_key = f"{active_prefix}/joins/joinable_pairs.jsonl"
    active_context_key = f"{active_prefix}/contexts/learned_context.json"
    active_context_graph_key = f"{active_prefix}/context_graph/manifest.json"
    active_query_libraries_key = f"{active_prefix}/libraries/manifest.json"
    active_nuance_key = f"{active_prefix}/nuance/manifest.json"
    active_retrieval_index_key = f"{active_prefix}/retrieval/bm25_plus_index.json"
    active_embedding_manifest_key = f"{active_prefix}/embeddings/manifest.json"
    active_grounding_json_key = f"{active_prefix}/grounding/business_grounding.json"
    active_schema_ast_key = f"{active_prefix}/schema_ast/manifest.json"
    active_manifest = object_store.read_json(active_manifest_key)
    active_descriptions = object_store.read_json(active_description_key)
    context_graph = object_store.read_json(context_graph_key or "")
    query_libraries = object_store.read_json(query_libraries_key) if query_libraries_key is not None else None
    nuance = object_store.read_json(nuance_key) if nuance_key is not None else None
    retrieval_index = object_store.read_json(retrieval_index_key or "")
    embedding_manifest = object_store.read_json(embedding_manifest_key or "")
    joinable_pairs_text = object_store.read_text(joinable_pairs_key or "")
    active_joinable_pairs_text = object_store.read_text(active_joinable_pairs_key)
    schema_ast_manifest = object_store.read_json(schema_ast_key) if schema_ast_key else None

    expected_columns = {
        table["table_name"]: {
            column["column_name"]
            for column in table["columns"]
        }
        for table in profile["tables"]
    }
    expected_tables = set(expected_columns)
    sample_tables = {
        key.rsplit("/", 1)[-1].removesuffix(".csv")
        for key in sample_keys
    }
    description_tables = set(descriptions["tables"])
    description_column_tables = set(descriptions["columns"])

    errors = []
    if sample_tables != expected_tables:
        errors.append(
            {
                "check": "sample_csv_coverage",
                "missing": sorted(expected_tables - sample_tables),
                "unexpected": sorted(sample_tables - expected_tables),
            }
        )
    if description_tables != expected_tables:
        errors.append(
            {
                "check": "description_table_coverage",
                "missing": sorted(expected_tables - description_tables),
                "unexpected": sorted(description_tables - expected_tables),
            }
        )
    if description_column_tables != expected_tables:
        errors.append(
            {
                "check": "description_column_table_coverage",
                "missing": sorted(expected_tables - description_column_tables),
                "unexpected": sorted(description_column_tables - expected_tables),
            }
        )
    for table_name, columns in expected_columns.items():
        described_columns = set(descriptions["columns"].get(table_name, {}))
        if described_columns != columns:
            errors.append(
                {
                    "check": "description_column_coverage",
                    "table": table_name,
                    "missing": sorted(columns - described_columns),
                    "unexpected": sorted(described_columns - columns),
                }
            )

    if active_manifest["active_run_id"] != run_id:
        errors.append(
            {
                "check": "active_manifest_run",
                "expected": run_id,
                "actual": active_manifest["active_run_id"],
            }
        )
    if active_descriptions != descriptions:
        errors.append({"check": "active_description_matches_immutable"})
    if active_joinable_pairs_text != joinable_pairs_text:
        errors.append({"check": "active_joinable_pairs_matches_immutable"})
    required_active_keys = [
        ("active_context_graph_exists", active_context_graph_key),
        ("active_retrieval_index_exists", active_retrieval_index_key),
        ("active_embedding_manifest_exists", active_embedding_manifest_key),
    ]
    if query_libraries_key is not None:
        required_active_keys.append(("active_query_libraries_exists", active_query_libraries_key))
    if nuance_key is not None:
        required_active_keys.append(("active_nuance_exists", active_nuance_key))
    if learning_artifact_strategy == "agentic":
        if learning_context_mode == "schema_ast" and schema_ast_key is not None:
            required_active_keys.append(("active_schema_ast_exists", active_schema_ast_key))
    if business_grounding_expected:
        required_active_keys.append(("active_business_grounding_exists", active_grounding_json_key))
    for check_name, key in required_active_keys:
        if not object_store.exists(key):
            errors.append({"check": check_name, "key": key})
    active_embedding_manifest = object_store.read_json(active_embedding_manifest_key)
    for label, manifest_payload in [
        ("immutable_embedding", embedding_manifest),
        ("active_embedding", active_embedding_manifest),
    ]:
        vector_index = manifest_payload.get("active_vector_index") or manifest_payload.get("vector_index")
        if not isinstance(vector_index, dict):
            errors.append({"check": f"{label}_vector_index_manifest"})
            continue
        if vector_index.get("status") == "ok":
            for field_name in ["index_artifact_key", "metadata_artifact_key"]:
                artifact_key = vector_index.get(field_name)
                if not isinstance(artifact_key, str) or not object_store.exists(artifact_key):
                    errors.append(
                        {
                            "check": f"{label}_vector_index_artifact_exists",
                            "field": field_name,
                            "key": artifact_key,
                        }
                    )

    if errors:
        raise AssertionError(json.dumps(errors, indent=2))

    total_columns = sum(len(columns) for columns in expected_columns.values())
    return {
        "status": "passed",
        "run_id": run_id,
        "executed_stages": executed_stages,
        "learning_artifact_strategy": learning_artifact_strategy,
        "learning_context_mode": learning_context_mode,
        "table_count": len(expected_tables),
        "total_column_count": total_columns,
        "sample_csv_count": len(sample_keys),
        "description_table_count": len(descriptions["tables"]),
        "description_column_table_count": len(descriptions["columns"]),
        "description_batch_count": len(batch_keys),
        "profile_artifact": profile_key,
        "description_artifact": description_key,
        "joinable_pairs_artifact": joinable_pairs_key,
        "context_graph_artifact": context_graph_key,
        "query_libraries_artifact": query_libraries_key,
        "nuance_artifact": nuance_key,
        "retrieval_index_artifact": retrieval_index_key,
        "embedding_manifest_artifact": embedding_manifest_key,
        "schema_ast_manifest_artifact": schema_ast_key,
        "context_artifact": context_key,
        "active_manifest_artifact": active_manifest_key,
        "active_description_artifact": active_description_key,
        "active_joinable_pairs_artifact": active_joinable_pairs_key,
        "active_context_graph_artifact": active_context_graph_key,
        "active_query_libraries_artifact": active_query_libraries_key,
        "active_nuance_artifact": active_nuance_key,
        "active_retrieval_index_artifact": active_retrieval_index_key,
        "active_embedding_manifest_artifact": active_embedding_manifest_key,
        "active_schema_ast_artifact": active_schema_ast_key if schema_ast_key else None,
        "active_business_grounding_artifact": (
            active_grounding_json_key if business_grounding_expected else None
        ),
        "active_context_artifact": active_context_key,
        "joinable_pair_count": len([line for line in joinable_pairs_text.splitlines() if line.strip()]),
        "context_graph_node_count": context_graph["node_count"],
        "context_graph_edge_count": context_graph["edge_count"],
        "query_pattern_count": context_graph["query_pattern_count"],
        "library_query_pattern_count": (
            query_libraries["query_pattern_count"] if query_libraries is not None else None
        ),
        "library_sql_count": (
            query_libraries.get("sql_library_count") if query_libraries is not None else None
        ),
        "library_sql_template_count": (
            query_libraries["sql_template_count"] if query_libraries is not None else None
        ),
        "library_entity_binding_count": (
            query_libraries["entity_binding_count"] if query_libraries is not None else None
        ),
        "library_metric_usage_count": (
            query_libraries["metric_usage_count"] if query_libraries is not None else None
        ),
        "nuance_null_candidate_count": (
            nuance["null_candidate_count"] if nuance is not None else None
        ),
        "nuance_confounder_count": nuance["confounder_count"] if nuance is not None else None,
        "nuance_invariant_count": nuance["invariant_count"] if nuance is not None else None,
        "nuance_analyst_question_count": (
            nuance["analyst_question_count"] if nuance is not None else None
        ),
        "retrieval_document_count": context_graph["retrieval_document_count"],
        "retrieval_index_document_count": retrieval_index["document_count"],
        "embedding_status": embedding_manifest["status"],
        "embedding_vector_count": embedding_manifest["vector_count"],
        "schema_ast_node_count": (
            schema_ast_manifest.get("node_count") if isinstance(schema_ast_manifest, dict) else None
        ),
        "vector_index_status": embedding_manifest.get("vector_index", {}).get("status"),
        "vector_index_artifact": embedding_manifest.get("vector_index", {}).get("index_artifact_key"),
        "active_vector_index_status": embedding_manifest.get("active_vector_index", {}).get("status"),
        "active_vector_index_artifact": embedding_manifest.get("active_vector_index", {}).get(
            "index_artifact_key"
        ),
        "sample_artifacts": sample_keys,
        "description_batch_artifacts": batch_keys,
        "context_run_id": context["run_id"] if isinstance(context, dict) else None,
    }


if __name__ == "__main__":
    main()
