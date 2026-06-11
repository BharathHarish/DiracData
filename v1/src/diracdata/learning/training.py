"""Training APIs for learned context artifacts."""

from __future__ import annotations

from datetime import UTC, datetime

from diracdata.config.settings import DiracDataSettings
from diracdata.learning.models import LearnedContext, LearningCollection, LearningScope, to_jsonable
from diracdata.learning.paths import active_learning_artifact_key, learning_artifact_key
from diracdata.storage.object_store import ObjectStore


class SchemaContextTrainer:
    """Create a learned context artifact from collected profiles and descriptions."""

    def __init__(
        self,
        *,
        settings: DiracDataSettings,
        object_store: ObjectStore,
    ) -> None:
        self.settings = settings
        self.object_store = object_store

    def train(
        self,
        *,
        collection: LearningCollection,
        description_artifact_key: str,
        joinable_pairs_artifact_key: str | None = None,
        context_graph_manifest_artifact_key: str | None = None,
        query_libraries_manifest_artifact_key: str | None = None,
        nuance_manifest_artifact_key: str | None = None,
        retrieval_index_artifact_key: str | None = None,
        embedding_manifest_artifact_key: str | None = None,
        schema_ast_manifest_artifact_key: str | None = None,
        schema_summary_artifact_key: str | None = None,
        semantic_map_artifact_key: str | None = None,
    ) -> LearnedContext:
        context_key = learning_artifact_key(
            self.settings,
            run_id=collection.run_id,
            relative_path="contexts/learned_context.json",
        )
        active_description_key = active_learning_artifact_key(
            self.settings,
            relative_path="descriptions/metadata_descriptions.json",
        )
        active_context_key = active_learning_artifact_key(
            self.settings,
            relative_path="contexts/learned_context.json",
        )
        active_manifest_key = active_learning_artifact_key(
            self.settings,
            relative_path="manifest.json",
        )
        context = LearnedContext(
            run_id=collection.run_id,
            scope=LearningScope(
                catalog=collection.scope.catalog,
                database=collection.scope.database,
                schema=collection.scope.schema,
            ),
            table_names=[profile.table_name for profile in collection.table_profiles],
            profile_artifact_key=collection.profile_artifact_key,
            llm_context_artifact_key=collection.llm_context_artifact_key,
            description_artifact_key=description_artifact_key,
            context_artifact_key=context_key,
            joinable_pairs_artifact_key=joinable_pairs_artifact_key,
            context_graph_manifest_artifact_key=context_graph_manifest_artifact_key,
            query_libraries_manifest_artifact_key=query_libraries_manifest_artifact_key,
            nuance_manifest_artifact_key=nuance_manifest_artifact_key,
            retrieval_index_artifact_key=retrieval_index_artifact_key,
            embedding_manifest_artifact_key=embedding_manifest_artifact_key,
            schema_ast_manifest_artifact_key=schema_ast_manifest_artifact_key,
            schema_summary_artifact_key=schema_summary_artifact_key,
            semantic_map_artifact_key=semantic_map_artifact_key,
            metadata={
                "query_engine": self.settings.query_engine,
                "sql_dialect": self.settings.sql_dialect,
                "llm_provider": self.settings.llm_provider,
                "llm_model": self.settings.llm_model,
                "llm_temperature": self.settings.llm_temperature,
                "sample_limit": self.settings.learning_sample_limit,
                "distinct_limit": self.settings.learning_distinct_limit,
                "top_values_limit": self.settings.learning_top_values_limit,
                "context_distinct_values_limit": (
                    self.settings.learning_context_distinct_values_limit
                ),
                "active_description_artifact_key": active_description_key,
                "active_context_artifact_key": active_context_key,
                "active_manifest_artifact_key": active_manifest_key,
                "joinable_pairs_artifact_key": joinable_pairs_artifact_key,
                "context_graph_manifest_artifact_key": context_graph_manifest_artifact_key,
                "query_libraries_manifest_artifact_key": query_libraries_manifest_artifact_key,
                "nuance_manifest_artifact_key": nuance_manifest_artifact_key,
                "retrieval_index_artifact_key": retrieval_index_artifact_key,
                "embedding_manifest_artifact_key": embedding_manifest_artifact_key,
                "schema_ast_manifest_artifact_key": schema_ast_manifest_artifact_key,
                "schema_summary_artifact_key": schema_summary_artifact_key,
                "semantic_map_artifact_key": semantic_map_artifact_key,
                "learning_artifact_strategy": self.settings.learning_artifact_strategy,
                "learning_context_mode": self.settings.learning_context_mode,
            },
        )
        context_payload = to_jsonable(context)
        description_payload = self.object_store.read_json(description_artifact_key)
        manifest_payload = {
            "catalog": collection.scope.catalog,
            "database": collection.scope.database,
            "schema": collection.scope.schema,
            "active_run_id": collection.run_id,
            "published_at": datetime.now(UTC).isoformat(),
            "immutable_artifacts": {
                "profile_artifact_key": collection.profile_artifact_key,
                "llm_context_artifact_key": collection.llm_context_artifact_key,
                "description_artifact_key": description_artifact_key,
                "context_artifact_key": context_key,
            },
            "active_artifacts": {
                "description_artifact_key": active_description_key,
                "context_artifact_key": active_context_key,
                "manifest_artifact_key": active_manifest_key,
            },
        }
        if self.settings.learning_artifact_strategy.strip().lower() == "agentic":
            manifest_payload["agentic_learning"] = {
                "artifact_strategy": self.settings.learning_artifact_strategy,
                "context_mode": self.settings.learning_context_mode,
            }
        if joinable_pairs_artifact_key is not None:
            active_join_key = active_learning_artifact_key(
                self.settings,
                relative_path="joins/joinable_pairs.jsonl",
            )
            manifest_payload["immutable_artifacts"]["joinable_pairs_artifact_key"] = (
                joinable_pairs_artifact_key
            )
            manifest_payload["active_artifacts"]["joinable_pairs_artifact_key"] = active_join_key
        if context_graph_manifest_artifact_key is not None:
            active_context_graph_key = active_learning_artifact_key(
                self.settings,
                relative_path="context_graph/manifest.json",
            )
            manifest_payload["immutable_artifacts"]["context_graph_manifest_artifact_key"] = (
                context_graph_manifest_artifact_key
            )
            manifest_payload["active_artifacts"]["context_graph_manifest_artifact_key"] = (
                active_context_graph_key
            )
        if query_libraries_manifest_artifact_key is not None:
            active_query_libraries_key = active_learning_artifact_key(
                self.settings,
                relative_path="libraries/manifest.json",
            )
            manifest_payload["immutable_artifacts"]["query_libraries_manifest_artifact_key"] = (
                query_libraries_manifest_artifact_key
            )
            manifest_payload["active_artifacts"]["query_libraries_manifest_artifact_key"] = (
                active_query_libraries_key
            )
        if nuance_manifest_artifact_key is not None:
            active_nuance_key = active_learning_artifact_key(
                self.settings,
                relative_path="nuance/manifest.json",
            )
            manifest_payload["immutable_artifacts"]["nuance_manifest_artifact_key"] = (
                nuance_manifest_artifact_key
            )
            manifest_payload["active_artifacts"]["nuance_manifest_artifact_key"] = (
                active_nuance_key
            )
        if retrieval_index_artifact_key is not None:
            active_retrieval_key = active_learning_artifact_key(
                self.settings,
                relative_path="retrieval/bm25_plus_index.json",
            )
            manifest_payload["immutable_artifacts"]["retrieval_index_artifact_key"] = (
                retrieval_index_artifact_key
            )
            manifest_payload["active_artifacts"]["retrieval_index_artifact_key"] = (
                active_retrieval_key
            )
        if embedding_manifest_artifact_key is not None:
            active_embedding_key = active_learning_artifact_key(
                self.settings,
                relative_path="embeddings/manifest.json",
            )
            manifest_payload["immutable_artifacts"]["embedding_manifest_artifact_key"] = (
                embedding_manifest_artifact_key
            )
            manifest_payload["active_artifacts"]["embedding_manifest_artifact_key"] = (
                active_embedding_key
            )
        if schema_ast_manifest_artifact_key is not None:
            active_schema_ast_key = active_learning_artifact_key(
                self.settings,
                relative_path="schema_ast/manifest.json",
            )
            manifest_payload["immutable_artifacts"]["schema_ast_manifest_artifact_key"] = (
                schema_ast_manifest_artifact_key
            )
            manifest_payload["active_artifacts"]["schema_ast_manifest_artifact_key"] = (
                active_schema_ast_key
            )
        if schema_summary_artifact_key is not None:
            active_schema_summary_key = active_learning_artifact_key(
                self.settings,
                relative_path="summaries/schema_summary.md",
            )
            manifest_payload["immutable_artifacts"]["schema_summary_artifact_key"] = (
                schema_summary_artifact_key
            )
            manifest_payload["active_artifacts"]["schema_summary_artifact_key"] = (
                active_schema_summary_key
            )
        if semantic_map_artifact_key is not None:
            active_semantic_map_key = active_learning_artifact_key(
                self.settings,
                relative_path="summaries/semantic_map.json",
            )
            manifest_payload["immutable_artifacts"]["semantic_map_artifact_key"] = (
                semantic_map_artifact_key
            )
            manifest_payload["active_artifacts"]["semantic_map_artifact_key"] = (
                active_semantic_map_key
            )

        if self.object_store.exists(active_manifest_key):
            existing_manifest = self.object_store.read_json(active_manifest_key)
            if isinstance(existing_manifest, dict):
                manifest_payload = _merge_manifests(existing_manifest, manifest_payload)

        if schema_ast_manifest_artifact_key is None:
            manifest_payload.get("immutable_artifacts", {}).pop("schema_ast_manifest_artifact_key", None)
            manifest_payload.get("active_artifacts", {}).pop("schema_ast_manifest_artifact_key", None)
        if schema_summary_artifact_key is None:
            manifest_payload.get("immutable_artifacts", {}).pop("schema_summary_artifact_key", None)
            manifest_payload.get("active_artifacts", {}).pop("schema_summary_artifact_key", None)
        if semantic_map_artifact_key is None:
            manifest_payload.get("immutable_artifacts", {}).pop("semantic_map_artifact_key", None)
            manifest_payload.get("active_artifacts", {}).pop("semantic_map_artifact_key", None)

        self.object_store.write_json(context_key, context_payload)
        self.object_store.write_json(active_description_key, description_payload)
        self.object_store.write_json(active_context_key, context_payload)
        self.object_store.write_json(active_manifest_key, manifest_payload)
        return context


def _merge_manifests(existing: dict[str, object], fresh: dict[str, object]) -> dict[str, object]:
    merged = dict(existing)
    merged.update(fresh)

    merged_immutable = dict(existing.get("immutable_artifacts", {}))
    merged_immutable.update(fresh.get("immutable_artifacts", {}))
    merged["immutable_artifacts"] = merged_immutable

    merged_active = dict(existing.get("active_artifacts", {}))
    merged_active.update(fresh.get("active_artifacts", {}))
    merged["active_artifacts"] = merged_active

    if isinstance(existing.get("agentic_learning"), dict) or isinstance(fresh.get("agentic_learning"), dict):
        merged_agentic = dict(existing.get("agentic_learning", {}))
        merged_agentic.update(fresh.get("agentic_learning", {}))
        merged["agentic_learning"] = merged_agentic
    return merged
