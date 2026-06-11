"""Stitch v2 learning steps into one reusable pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from diracdata_v2.learning.schema_ast import SchemaASTBuildResult, SchemaASTBuilder
from diracdata_v2.learning.schema_graph import SchemaGraphBuildResult, SchemaGraphBuilder, TextGenerator, load_prompt
from diracdata_v2.learning.sql_library import SQLLibraryBuildResult, SQLLibraryBuilder
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


@dataclass(frozen=True)
class LearningPipelineResult:
    run_id: str
    manifest: dict[str, Any]
    manifest_path: Path
    schema_graph: SchemaGraphBuildResult
    sql_library: SQLLibraryBuildResult
    schema_ast: SchemaASTBuildResult
    semantic_catalog: SemanticCatalogBuildResult
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
        )
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
        )
        manifest = _manifest(
            config=config,
            schema_graph=graph_result,
            sql_library=sql_library_result,
            schema_ast=ast_result,
            semantic_catalog=catalog_result,
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
            object_key=object_key,
        )


def _manifest(
    *,
    config: LearningPipelineConfig,
    schema_graph: SchemaGraphBuildResult,
    sql_library: SQLLibraryBuildResult,
    schema_ast: SchemaASTBuildResult,
    semantic_catalog: SemanticCatalogBuildResult,
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
            "self_play_entries": sum(1 for item in library_entries.values() if item.get("source") == "self_play"),
            "missing_columns": len(sql_library.document.get("coverage", {}).get("columns_missing", [])),
        },
    }
