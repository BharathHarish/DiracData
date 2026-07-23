"""Stitch v2 learning steps into one reusable pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from diracdata_v2.context_fabric import (
    ColumnEvidenceProfiler,
    ColumnNuanceBuildResult,
    ColumnNuanceBuilder,
    JoinMiner,
    JoinMinerBuildResult,
    NormalizedKnowledgeCorpus,
    SemanticAssertionBuildResult,
    SemanticAssertionBuilder,
    SemanticCoverageBuildResult,
    SemanticCoverageBuilder,
    SemanticSelfPlaySQLBuildResult,
    SemanticSelfPlaySQLBuilder,
    SemanticPathwayBuildResult,
    SemanticPathwayBuilder,
    build_normalized_corpus,
    table_columns_from_metadata,
)
from diracdata_v2.context_fabric.sources import write_corpus_jsonl
from diracdata_v2.learning.schema_ast import SchemaASTBuildResult, SchemaASTBuilder
from diracdata_v2.learning.schema_graph import SchemaGraphBuildResult, SchemaGraphBuilder, TextGenerator, load_prompt
from diracdata_v2.learning.sql_library import (
    SQLLibraryBuildResult,
    SQLLibraryBuilder,
    rebuild_sql_library_patterns,
    review_sql_library_entries,
)
from diracdata_v2.query import DuckDBEngine
from diracdata_v2.semantic_catalog import SemanticCatalogBuilder, SemanticCatalogBuildResult


@dataclass(frozen=True)
class LearningPipelineConfig:
    catalog: str
    database: str
    schema: str
    metadata_descriptions_path: Path
    query_history_path: Path
    data_root: Path
    artifact_root: Path
    run_id: str
    object_prefix: str = "v2/learning/artifacts"
    history_limit: int = 80
    pattern_batch_size: int = 20
    pattern_limit: int = 80
    nl_sql_pair_paths: tuple[Path, ...] = ()
    nl_sql_pair_limit: int | None = None
    nl_sql_pair_review_status: str = "approved"
    enable_context_fabric: bool = True
    include_experiences: bool = False
    experience_prefix: str = "experience/candidates"
    experience_limit: int | None = None
    auto_approve_valid_self_play: bool = False
    self_play_limit: int | None = None
    context_query_history_limit: int | None = None
    context_nl_sql_pair_limit: int | None = None
    enable_semantic_coverage: bool = True
    semantic_coverage_strict_agentic: bool = False
    # B1: the gap planner was capped at 50 of ~301 missing columns (alphabetical window),
    # so self-play only ever dented schema coverage. These caps now clear the whole gap
    # for a schema this size; self-play is opt-in (enable_* defaults False) so this only
    # costs tokens on an explicit run. Promotion (promote_validated_self_play) then makes
    # the validated output recallable instead of stranding it as needs_review.
    self_play_gap_limit: int = 400
    enable_semantic_self_play_sql: bool = False
    semantic_self_play_sql_task_limit: int = 400
    semantic_self_play_sql_batch_size: int = 25
    semantic_self_play_sql_batch_limit: int | None = None
    semantic_self_play_sql_repair_iterations: int = 2
    semantic_self_play_sql_validation_rows: int = 5
    semantic_self_play_sql_strict_agentic: bool = False
    profile_data: bool = False
    enable_semantic_pathways: bool = True
    pathway_batch_size: int = 20
    pathway_limit: int | None = None
    enable_column_nuances: bool = True
    nuance_max_columns: int = 40
    nuance_max_values: int = 20
    nuance_batch_size: int = 10
    nuance_strict_agentic: bool = False
    enable_join_graph: bool = True
    join_max_tables: int = 30
    join_max_columns_per_table: int = 16
    join_max_candidate_edges: int = 80
    join_max_overlap_checks: int = 800
    join_sample_limit: int = 1000
    join_min_candidate_score: float = 0.35
    join_min_candidate_distinct_count: int = 2
    join_batch_size: int = 8
    join_strict_agentic: bool = False
    enable_semantic_assertions: bool = True
    assertion_limit: int | None = 20
    assertion_batch_size: int = 6
    assertion_strict_agentic: bool = False


@dataclass(frozen=True)
class LearningPipelineResult:
    run_id: str
    manifest: dict[str, Any]
    manifest_path: Path
    schema_graph: SchemaGraphBuildResult
    sql_library: SQLLibraryBuildResult
    schema_ast: SchemaASTBuildResult
    semantic_catalog: SemanticCatalogBuildResult
    normalized_corpus: NormalizedKnowledgeCorpus | None = None
    normalized_corpus_path: Path | None = None
    normalized_corpus_object_key: str | None = None
    semantic_coverage: SemanticCoverageBuildResult | None = None
    semantic_self_play_sql: SemanticSelfPlaySQLBuildResult | None = None
    semantic_pathways: SemanticPathwayBuildResult | None = None
    column_nuances: ColumnNuanceBuildResult | None = None
    join_graph: JoinMinerBuildResult | None = None
    semantic_assertions: SemanticAssertionBuildResult | None = None
    object_key: str | None = None


class LearningPipeline:
    """Run v2 learning in dependency order.

    The pipeline deliberately keeps each artifact separate:
    schema graph -> SQL library -> schema AST -> manifest.
    """

    def __init__(
        self,
        *,
        generator: TextGenerator,
        schema_graph_builder: SchemaGraphBuilder | None = None,
        sql_library_builder: SQLLibraryBuilder | None = None,
        schema_ast_builder: SchemaASTBuilder | None = None,
        semantic_catalog_builder: SemanticCatalogBuilder | None = None,
    ) -> None:
        self._schema_graph_builder = schema_graph_builder or SchemaGraphBuilder(
            generator=generator,
            prompt=load_prompt(),
        )
        self._sql_library_builder = sql_library_builder or SQLLibraryBuilder(
            pattern_generator=generator,
        )
        self._schema_ast_builder = schema_ast_builder or SchemaASTBuilder()
        self._semantic_catalog_builder = semantic_catalog_builder or SemanticCatalogBuilder(generator=generator)
        self._generator = generator

    def run(
        self,
        *,
        config: LearningPipelineConfig,
        object_store: Any | None = None,
    ) -> LearningPipelineResult:
        metadata = json.loads(config.metadata_descriptions_path.read_text(encoding="utf-8"))
        run_dir = config.artifact_root / config.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        graph_result = self._schema_graph_builder.build(
            metadata_descriptions=metadata,
            catalog=config.catalog,
            database=config.database,
            schema=config.schema,
            run_id=config.run_id,
            output_dir=run_dir,
            object_store=object_store,
            object_prefix=config.object_prefix,
        )
        sql_library_result = self._sql_library_builder.build(
            schema_graph=graph_result.document,
            query_history_path=config.query_history_path,
            data_root=config.data_root,
            catalog=config.catalog,
            database=config.database,
            schema=config.schema,
            run_id=config.run_id,
            output_dir=run_dir,
            object_store=object_store,
            object_prefix=config.object_prefix,
            history_limit=config.history_limit,
            pattern_batch_size=config.pattern_batch_size,
            pattern_limit=config.pattern_limit,
            nl_sql_pair_paths=config.nl_sql_pair_paths,
            nl_sql_pair_limit=config.nl_sql_pair_limit,
            nl_sql_pair_review_status=config.nl_sql_pair_review_status,
        )
        if config.auto_approve_valid_self_play:
            reviewed_library, _ = review_sql_library_entries(
                sql_library_result.document,
                status="approved",
                reviewer="learning_pipeline",
                note="Auto-approved validation-passed self-play entries for this pipeline run.",
                all_entries=True,
                source="self_play",
                current_status="needs_review",
                validation_status="passed",
            )
            reviewed_library = rebuild_sql_library_patterns(
                reviewed_library,
                schema_graph=graph_result.document,
                generator=self._generator,
                batch_size=config.pattern_batch_size,
                limit=None,
            )
            sql_library_result.local_path.write_text(
                json.dumps(reviewed_library, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            if object_store is not None and sql_library_result.object_key:
                object_store.write_json(sql_library_result.object_key, reviewed_library)
            sql_library_result = SQLLibraryBuildResult(
                document=reviewed_library,
                local_path=sql_library_result.local_path,
                object_key=sql_library_result.object_key,
            )

        normalized_corpus: NormalizedKnowledgeCorpus | None = None
        normalized_corpus_path: Path | None = None
        normalized_corpus_object_key: str | None = None
        semantic_coverage_result: SemanticCoverageBuildResult | None = None
        semantic_self_play_sql_result: SemanticSelfPlaySQLBuildResult | None = None
        pathway_result: SemanticPathwayBuildResult | None = None
        nuance_result: ColumnNuanceBuildResult | None = None
        join_result: JoinMinerBuildResult | None = None
        assertion_result: SemanticAssertionBuildResult | None = None

        ast_result = self._schema_ast_builder.build(
            schema_graph=graph_result.document,
            sql_library=sql_library_result.document,
            catalog=config.catalog,
            database=config.database,
            schema=config.schema,
            run_id=config.run_id,
            output_dir=run_dir,
            object_store=object_store,
            object_prefix=config.object_prefix,
        )

        engine = None  # set inside the context-fabric block; the brief stage may reuse it
        if config.enable_context_fabric:
            table_columns = table_columns_from_metadata(metadata)
            normalized_corpus = build_normalized_corpus(
                table_columns=table_columns,
                query_history_path=config.query_history_path,
                nl_sql_pair_paths=config.nl_sql_pair_paths,
                sql_library_document=sql_library_result.document,
                experience_object_store=object_store if config.include_experiences else None,
                experience_schema=config.schema if config.include_experiences else None,
                experience_prefix=config.experience_prefix,
                query_history_limit=config.context_query_history_limit
                if config.context_query_history_limit is not None
                else config.history_limit,
                nl_sql_pair_limit=config.context_nl_sql_pair_limit
                if config.context_nl_sql_pair_limit is not None
                else config.nl_sql_pair_limit,
                self_play_limit=config.self_play_limit,
                experience_limit=config.experience_limit,
            )
            normalized_corpus_path = write_corpus_jsonl(normalized_corpus, run_dir / "normalized_knowledge_corpus.jsonl")
            (run_dir / "normalized_knowledge_corpus_summary.json").write_text(
                json.dumps(normalized_corpus.summary(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            if object_store is not None:
                normalized_corpus_object_key = f"{config.object_prefix.strip('/')}/{config.run_id}/normalized_knowledge_corpus.jsonl"
                object_store.write_text(
                    normalized_corpus_object_key,
                    normalized_corpus_path.read_text(encoding="utf-8"),
                    content_type="application/jsonl",
                )

            engine_required = (
                config.profile_data
                or config.enable_semantic_self_play_sql
                or config.enable_column_nuances
                or config.enable_join_graph
            )
            engine = DuckDBEngine(data_root=config.data_root, schema_name=config.schema) if engine_required else None

            if config.enable_semantic_coverage:
                semantic_coverage_result = SemanticCoverageBuilder(
                    generator=self._generator,
                    gap_limit=config.self_play_gap_limit,
                    strict_agentic=config.semantic_coverage_strict_agentic,
                ).build(
                    corpus=normalized_corpus,
                    metadata_descriptions=metadata,
                    schema_graph=graph_result.document,
                    catalog=config.catalog,
                    database=config.database,
                    schema=config.schema,
                    run_id=config.run_id,
                    output_dir=run_dir,
                    object_store=object_store,
                    object_prefix=config.object_prefix,
                )

            if config.enable_semantic_self_play_sql and semantic_coverage_result is not None:
                semantic_self_play_sql_result = SemanticSelfPlaySQLBuilder(
                    generator=self._generator,
                    engine=engine,
                    task_limit=config.semantic_self_play_sql_task_limit,
                    batch_size=config.semantic_self_play_sql_batch_size,
                    batch_limit=config.semantic_self_play_sql_batch_limit,
                    max_repair_iterations=config.semantic_self_play_sql_repair_iterations,
                    validation_rows=config.semantic_self_play_sql_validation_rows,
                    strict_agentic=config.semantic_self_play_sql_strict_agentic,
                ).build(
                    semantic_coverage_document=semantic_coverage_result.document,
                    metadata_descriptions=metadata,
                    sql_library_document=sql_library_result.document,
                    catalog=config.catalog,
                    database=config.database,
                    schema=config.schema,
                    run_id=config.run_id,
                    output_dir=run_dir,
                    object_store=object_store,
                    object_prefix=config.object_prefix,
                )

            if config.enable_semantic_pathways:
                pathway_result = SemanticPathwayBuilder(
                    generator=self._generator,
                    batch_size=config.pathway_batch_size,
                    limit=config.pathway_limit,
                ).build(
                    corpus=normalized_corpus,
                    metadata_descriptions=metadata,
                    catalog=config.catalog,
                    database=config.database,
                    schema=config.schema,
                    run_id=config.run_id,
                    output_dir=run_dir,
                    object_store=object_store,
                    object_prefix=config.object_prefix,
                )
            if config.enable_column_nuances:
                nuance_result = ColumnNuanceBuilder(
                    generator=self._generator,
                    profiler=ColumnEvidenceProfiler(
                        engine=engine,
                        max_columns=config.nuance_max_columns,
                        max_values=config.nuance_max_values,
                    ),
                    batch_size=config.nuance_batch_size,
                    strict_agentic=config.nuance_strict_agentic,
                ).build(
                    corpus=normalized_corpus,
                    metadata_descriptions=metadata,
                    catalog=config.catalog,
                    database=config.database,
                    schema=config.schema,
                    run_id=config.run_id,
                    output_dir=run_dir,
                    object_store=object_store,
                    object_prefix=config.object_prefix,
                )
            if config.enable_join_graph:
                join_result = JoinMiner(
                    engine=engine,
                    classifier=self._generator,
                    max_tables=config.join_max_tables,
                    max_columns_per_table=config.join_max_columns_per_table,
                    max_candidate_edges=config.join_max_candidate_edges,
                    max_overlap_checks=config.join_max_overlap_checks,
                    sample_limit=config.join_sample_limit,
                    min_candidate_score=config.join_min_candidate_score,
                    min_candidate_distinct_count=config.join_min_candidate_distinct_count,
                    batch_size=config.join_batch_size,
                    strict_agentic=config.join_strict_agentic,
                ).build(
                    corpus=normalized_corpus,
                    metadata_descriptions=metadata,
                    catalog=config.catalog,
                    database=config.database,
                    schema=config.schema,
                    run_id=config.run_id,
                    output_dir=run_dir,
                    object_store=object_store,
                    object_prefix=config.object_prefix,
                )
            if config.enable_semantic_assertions:
                assertion_result = SemanticAssertionBuilder(
                    generator=self._generator,
                    limit=config.assertion_limit,
                    batch_size=config.assertion_batch_size,
                    strict_agentic=config.assertion_strict_agentic,
                ).build(
                    corpus=normalized_corpus,
                    metadata_descriptions=metadata,
                    catalog=config.catalog,
                    database=config.database,
                    schema=config.schema,
                    run_id=config.run_id,
                    pathway_document=pathway_result.document if pathway_result else None,
                    nuance_document=nuance_result.document if nuance_result else None,
                    join_document=join_result.document if join_result else None,
                    output_dir=run_dir,
                    object_store=object_store,
                    object_prefix=config.object_prefix,
                )

        catalog_result = self._semantic_catalog_builder.build(
            metadata_descriptions=metadata,
            schema_ast=ast_result.document,
            sql_library=sql_library_result.document,
            catalog=config.catalog,
            database=config.database,
            schema=config.schema,
            run_id=config.run_id,
            output_dir=run_dir,
            object_store=object_store,
            object_prefix=config.object_prefix,
            join_graph=join_result.document if join_result is not None else None,
        )

        manifest = _manifest(
            config=config,
            schema_graph=graph_result,
            sql_library=sql_library_result,
            schema_ast=ast_result,
            semantic_catalog=catalog_result,
            normalized_corpus=normalized_corpus,
            normalized_corpus_path=normalized_corpus_path,
            normalized_corpus_object_key=normalized_corpus_object_key,
            semantic_coverage=semantic_coverage_result,
            semantic_self_play_sql=semantic_self_play_sql_result,
            semantic_pathways=pathway_result,
            column_nuances=nuance_result,
            join_graph=join_result,
            semantic_assertions=assertion_result,
        )
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

        object_key = None
        if object_store is not None:
            object_key = f"{config.object_prefix.strip('/')}/{config.run_id}/manifest.json"
            object_store.write_json(object_key, manifest)

        return LearningPipelineResult(
            run_id=config.run_id,
            manifest=manifest,
            manifest_path=manifest_path,
            schema_graph=graph_result,
            sql_library=sql_library_result,
            schema_ast=ast_result,
            semantic_catalog=catalog_result,
            normalized_corpus=normalized_corpus,
            normalized_corpus_path=normalized_corpus_path,
            normalized_corpus_object_key=normalized_corpus_object_key,
            semantic_coverage=semantic_coverage_result,
            semantic_self_play_sql=semantic_self_play_sql_result,
            semantic_pathways=pathway_result,
            column_nuances=nuance_result,
            join_graph=join_result,
            semantic_assertions=assertion_result,
            object_key=object_key,
        )


def _manifest(
    *,
    config: LearningPipelineConfig,
    schema_graph: SchemaGraphBuildResult,
    sql_library: SQLLibraryBuildResult,
    schema_ast: SchemaASTBuildResult,
    semantic_catalog: SemanticCatalogBuildResult,
    normalized_corpus: NormalizedKnowledgeCorpus | None = None,
    normalized_corpus_path: Path | None = None,
    normalized_corpus_object_key: str | None = None,
    semantic_coverage: SemanticCoverageBuildResult | None = None,
    semantic_self_play_sql: SemanticSelfPlaySQLBuildResult | None = None,
    semantic_pathways: SemanticPathwayBuildResult | None = None,
    column_nuances: ColumnNuanceBuildResult | None = None,
    join_graph: JoinMinerBuildResult | None = None,
    semantic_assertions: SemanticAssertionBuildResult | None = None,
) -> dict[str, Any]:
    ast_indexes = schema_ast.document.get("indexes", {}).get("by_kind", {})
    library_entries = sql_library.document.get("entries", {})
    library_patterns = sql_library.document.get("patterns", {})
    return {
        "version": 1,
        "artifact_type": "learning_manifest",
        "run_id": config.run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "scope": {
            "catalog": config.catalog,
            "database": config.database,
            "schema": config.schema,
        },
        "inputs": {
            "metadata_descriptions_path": str(config.metadata_descriptions_path),
            "query_history_path": str(config.query_history_path),
            "nl_sql_pair_paths": [str(path) for path in config.nl_sql_pair_paths],
            "data_root": str(config.data_root),
        },
        "artifacts": {
            "schema_graph": {
                "local_path": str(schema_graph.local_path),
                "object_key": schema_graph.object_key,
            },
            "sql_library": {
                "local_path": str(sql_library.local_path),
                "object_key": sql_library.object_key,
            },
            "schema_ast": {
                "local_path": str(schema_ast.local_path),
                "object_key": schema_ast.object_key,
            },
            "semantic_catalog": {
                "local_path": str(semantic_catalog.local_path),
                "object_key": semantic_catalog.object_key,
            },
            **_optional_artifact(
                "normalized_knowledge_corpus",
                local_path=normalized_corpus_path,
                object_key=normalized_corpus_object_key,
            ),
            **_optional_result_artifact("semantic_coverage", semantic_coverage),
            **_optional_result_artifact("semantic_self_play_sql", semantic_self_play_sql),
            **_optional_result_artifact("semantic_pathways", semantic_pathways),
            **_optional_result_artifact("column_nuances", column_nuances),
            **_optional_result_artifact("join_graph", join_graph),
            **_optional_result_artifact("semantic_assertions", semantic_assertions),
        },
        "summary": {
            "domains": len(ast_indexes.get("domain", [])),
            "entities": len(ast_indexes.get("entity", [])),
            "tables": len(ast_indexes.get("table", [])),
            "columns": len(ast_indexes.get("column", [])),
            "sql_library_entries": len(library_entries),
            "sql_patterns": len(library_patterns),
            "semantic_catalog_cards": len(semantic_catalog.document.get("cards", {})),
            "semantic_catalog_join_edges": len(semantic_catalog.document.get("join_edges", {})),
            "history_entries": sum(1 for item in library_entries.values() if item.get("source") == "query_history"),
            "nl_sql_pair_entries": sum(1 for item in library_entries.values() if item.get("source") == "nl_sql_pair"),
            "self_play_entries": sum(1 for item in library_entries.values() if item.get("source") == "self_play"),
            "approved_self_play_entries": sum(
                1
                for item in library_entries.values()
                if item.get("source") == "self_play" and item.get("review_status") == "approved"
            ),
            "missing_columns": len(sql_library.document.get("coverage", {}).get("columns_missing", [])),
            "normalized_cases": len(normalized_corpus.cases) if normalized_corpus is not None else 0,
            "semantic_coverage_entities": len(
                (semantic_coverage.document if semantic_coverage else {}).get("entity_map", {}).get("entities", {})
            ),
            "semantic_coverage_gap_tasks": len(
                (semantic_coverage.document if semantic_coverage else {}).get("self_play_gap_plan", {}).get("tasks", {})
            ),
            "semantic_self_play_sql_entries": len(
                (semantic_self_play_sql.document if semantic_self_play_sql else {}).get("entries", {})
            ),
            "semantic_self_play_sql_passed": (
                (semantic_self_play_sql.document if semantic_self_play_sql else {})
                .get("summary", {})
                .get("validation_passed_count", 0)
            ),
            "semantic_pathways": len((semantic_pathways.document if semantic_pathways else {}).get("pathways", {})),
            "column_nuance_cards": len((column_nuances.document if column_nuances else {}).get("cards", {})),
            "join_graph_edges": len((join_graph.document if join_graph else {}).get("join_edges", {})),
            "semantic_assertions": len((semantic_assertions.document if semantic_assertions else {}).get("assertions", {})),
        },
    }


def _optional_result_artifact(name: str, result: Any | None) -> dict[str, dict[str, str | None]]:
    if result is None:
        return {}
    return _optional_artifact(name, local_path=result.local_path, object_key=result.object_key)


def _optional_artifact(
    name: str,
    *,
    local_path: Path | None,
    object_key: str | None,
) -> dict[str, dict[str, str | None]]:
    if local_path is None and object_key is None:
        return {}
    return {
        name: {
            "local_path": str(local_path) if local_path is not None else None,
            "object_key": object_key,
        }
    }
