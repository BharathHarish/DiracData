"""Learning pipeline orchestration APIs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from diracdata.config.settings import DiracDataSettings
from diracdata.llms import ChatModelClient, chat_model_client_from_settings
from diracdata.learning.agentic import AgenticLearningArtifactBuilder, AgenticLearningBuildResult
from diracdata.learning.collector import SchemaLearningCollector
from diracdata.learning.context_graph import ContextGraphBuilder, ContextGraphBuildResult
from diracdata.learning.descriptions import MetadataDescriptionGenerator
from diracdata.learning.embeddings import EmbeddingBuildResult, EmbeddingIndexBuilder
from diracdata.learning.joins import JoinablePairDiscovery
from diracdata.learning.libraries import QueryLibraryBuilder, QueryLibraryBuildResult
from diracdata.learning.models import (
    BusinessContext,
    LearnedContext,
    LearningCollection,
    LearningScope,
    LearningStage,
)
from diracdata.learning.nuance import NuanceArtifactBuilder, NuanceBuildResult
from diracdata.learning.query_history import QueryHistoryRecord
from diracdata.learning.training import SchemaContextTrainer
from diracdata.query_engines.base import QueryEngine
from diracdata.storage.object_store import ObjectStore


@dataclass(frozen=True)
class LearningRunState:
    run_id: str
    collection: LearningCollection | None = None
    description_artifact_key: str | None = None
    joinable_pairs_artifact_key: str | None = None
    context_graph_manifest_artifact_key: str | None = None
    retrieval_documents_artifact_key: str | None = None
    retrieval_index_artifact_key: str | None = None
    query_libraries_manifest_artifact_key: str | None = None
    nuance_manifest_artifact_key: str | None = None
    embedding_manifest_artifact_key: str | None = None
    schema_ast_manifest_artifact_key: str | None = None
    schema_summary_artifact_key: str | None = None
    semantic_map_artifact_key: str | None = None
    context: LearnedContext | None = None


@dataclass(frozen=True)
class LearningPipelineResult:
    collection: LearningCollection | None
    description_artifact_key: str | None
    joinable_pairs_artifact_key: str | None
    context_graph_artifact_key: str | None
    query_libraries_artifact_key: str | None
    nuance_artifact_key: str | None
    retrieval_index_artifact_key: str | None
    embedding_manifest_artifact_key: str | None
    context: LearnedContext | None
    executed_stages: list[LearningStage]
    state: LearningRunState


class LearningPipeline:
    """Coordinate data collection, description generation, and context training."""

    def __init__(
        self,
        *,
        settings: DiracDataSettings,
        query_engine: QueryEngine,
        object_store: ObjectStore,
        sample_limit: int | None = None,
        distinct_limit: int | None = None,
        top_values_limit: int | None = None,
        context_distinct_values_limit: int | None = None,
        description_column_batch_size: int | None = None,
        llm_client: ChatModelClient | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.query_engine = query_engine
        self.object_store = object_store
        self.llm_client = llm_client or chat_model_client_from_settings(settings)
        self.collector = SchemaLearningCollector(
            settings=settings,
            query_engine=query_engine,
            object_store=object_store,
            sample_limit=sample_limit,
            distinct_limit=distinct_limit,
            top_values_limit=top_values_limit,
            context_distinct_values_limit=context_distinct_values_limit,
            progress_callback=progress_callback,
        )
        self.description_generator = MetadataDescriptionGenerator(
            settings=settings,
            object_store=object_store,
            llm_client=self.llm_client,
            column_batch_size=description_column_batch_size,
            progress_callback=progress_callback,
        )
        self.join_discovery = JoinablePairDiscovery(
            settings=settings,
            object_store=object_store,
            llm_client=self.llm_client,
            progress_callback=progress_callback,
        )
        self.context_graph_builder = ContextGraphBuilder(
            settings=settings,
            object_store=object_store,
            progress_callback=progress_callback,
        )
        self.query_library_builder = QueryLibraryBuilder(
            settings=settings,
            object_store=object_store,
            progress_callback=progress_callback,
        )
        self.nuance_builder = NuanceArtifactBuilder(
            settings=settings,
            object_store=object_store,
            progress_callback=progress_callback,
        )
        self.embedding_builder = EmbeddingIndexBuilder(
            settings=settings,
            object_store=object_store,
            progress_callback=progress_callback,
        )
        self.agentic_artifact_builder = AgenticLearningArtifactBuilder(
            settings=settings,
            object_store=object_store,
            llm_client=self.llm_client,
            progress_callback=progress_callback,
        )
        self.trainer = SchemaContextTrainer(settings=settings, object_store=object_store)

    def default_stage_sequence(self) -> list[LearningStage]:
        sequence = [
            LearningStage.DATA_COLLECTION,
            LearningStage.DESCRIPTION_GENERATION,
            LearningStage.JOIN_DISCOVERY,
            LearningStage.CONTEXT_GRAPH_BUILDING,
            LearningStage.EMBEDDING_GENERATION,
        ]
        if self.settings.learning_artifact_strategy.strip().lower() == "agentic":
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

    def collect_data(
        self,
        *,
        business_context: BusinessContext,
        run_id: str | None = None,
        tables: list[str] | None = None,
    ) -> LearningCollection:
        return self.collector.collect(
            business_context=business_context,
            run_id=run_id,
            tables=tables,
        )

    def generate_descriptions(
        self,
        collection: LearningCollection,
        *,
        business_grounding: dict[str, object] | None = None,
    ) -> str:
        return self.description_generator.generate(
            collection,
            business_grounding=business_grounding,
        )

    def discover_joins(
        self,
        *,
        collection: LearningCollection,
        query_history_path: str | Path | None = None,
        query_history_records: list[QueryHistoryRecord] | None = None,
    ) -> str:
        result = self.join_discovery.discover(
            collection=collection,
            query_history_path=query_history_path,
            query_history_records=query_history_records,
        )
        return result.joinable_pairs_artifact_key

    def build_context_graph(
        self,
        *,
        collection: LearningCollection,
        description_artifact_key: str,
        joinable_pairs_artifact_key: str | None = None,
        business_grounding: dict[str, object] | None = None,
        query_history_path: str | Path | None = None,
        query_history_records: list[QueryHistoryRecord] | None = None,
    ) -> ContextGraphBuildResult:
        return self.context_graph_builder.build(
            collection=collection,
            description_artifact_key=description_artifact_key,
            joinable_pairs_artifact_key=joinable_pairs_artifact_key,
            business_grounding=business_grounding,
            query_history_path=str(query_history_path) if query_history_path is not None else None,
            query_history_records=query_history_records,
        )

    def build_query_libraries(
        self,
        *,
        collection: LearningCollection,
        query_history_path: str | Path | None = None,
        query_history_records: list[QueryHistoryRecord] | None = None,
        joinable_pairs_artifact_key: str | None = None,
        business_grounding: dict[str, object] | None = None,
    ) -> QueryLibraryBuildResult:
        return self.query_library_builder.build(
            collection=collection,
            query_history_path=query_history_path,
            query_history_records=query_history_records,
            joinable_pairs_artifact_key=joinable_pairs_artifact_key,
            business_grounding=business_grounding,
        )

    def build_nuance_artifacts(
        self,
        *,
        collection: LearningCollection,
        business_grounding: dict[str, object] | None = None,
        query_libraries_manifest_artifact_key: str | None = None,
    ) -> NuanceBuildResult:
        return self.nuance_builder.build(
            collection=collection,
            business_grounding=business_grounding,
            query_libraries_manifest_artifact_key=query_libraries_manifest_artifact_key,
        )

    def build_embeddings(
        self,
        *,
        collection: LearningCollection,
        retrieval_documents_artifact_key: str,
    ) -> EmbeddingBuildResult:
        return self.embedding_builder.build(
            collection=collection,
            retrieval_documents_artifact_key=retrieval_documents_artifact_key,
        )

    def build_agentic_artifacts(
        self,
        *,
        collection: LearningCollection,
        description_artifact_key: str,
        joinable_pairs_artifact_key: str | None = None,
        query_history_path: str | Path | None = None,
        query_history_records: list[QueryHistoryRecord] | None = None,
        business_grounding: dict[str, object] | None = None,
    ) -> AgenticLearningBuildResult:
        return self.agentic_artifact_builder.build(
            collection=collection,
            description_artifact_key=description_artifact_key,
            joinable_pairs_artifact_key=joinable_pairs_artifact_key,
            query_history_path=query_history_path,
            query_history_records=query_history_records,
            business_grounding=business_grounding,
        )

    def train_context(
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
        return self.trainer.train(
            collection=collection,
            description_artifact_key=description_artifact_key,
            joinable_pairs_artifact_key=joinable_pairs_artifact_key,
            context_graph_manifest_artifact_key=context_graph_manifest_artifact_key,
            query_libraries_manifest_artifact_key=query_libraries_manifest_artifact_key,
            nuance_manifest_artifact_key=nuance_manifest_artifact_key,
            retrieval_index_artifact_key=retrieval_index_artifact_key,
            embedding_manifest_artifact_key=embedding_manifest_artifact_key,
            schema_ast_manifest_artifact_key=schema_ast_manifest_artifact_key,
            schema_summary_artifact_key=schema_summary_artifact_key,
            semantic_map_artifact_key=semantic_map_artifact_key,
        )

    def load_run_state(self, *, run_id: str) -> LearningRunState:
        state = LearningRunState(run_id=run_id)
        profile_key = self._run_artifact_key(run_id=run_id, relative_path="profiles/table_profiles.json")
        if self.object_store.exists(profile_key):
            from diracdata.learning.joins import learning_collection_from_profile_artifact

            collection = learning_collection_from_profile_artifact(
                object_store=self.object_store,
                profile_artifact_key=profile_key,
            )
            state = self._replace_state(state, collection=collection)
        state = self._replace_state(
            state,
            description_artifact_key=self._existing_run_key(
                run_id=run_id,
                relative_path="descriptions/metadata_descriptions.json",
            ),
            joinable_pairs_artifact_key=self._existing_run_key(
                run_id=run_id,
                relative_path="joins/joinable_pairs.jsonl",
            ),
            context_graph_manifest_artifact_key=self._existing_run_key(
                run_id=run_id,
                relative_path="context_graph/manifest.json",
            ),
            retrieval_documents_artifact_key=self._existing_run_key(
                run_id=run_id,
                relative_path="retrieval/documents.jsonl",
            ),
            retrieval_index_artifact_key=self._existing_run_key(
                run_id=run_id,
                relative_path="retrieval/bm25_plus_index.json",
            ),
            query_libraries_manifest_artifact_key=self._existing_run_key(
                run_id=run_id,
                relative_path="libraries/manifest.json",
            ),
            nuance_manifest_artifact_key=self._existing_run_key(
                run_id=run_id,
                relative_path="nuance/manifest.json",
            ),
            embedding_manifest_artifact_key=self._existing_run_key(
                run_id=run_id,
                relative_path="embeddings/manifest.json",
            ),
            schema_ast_manifest_artifact_key=self._existing_run_key(
                run_id=run_id,
                relative_path="schema_ast/manifest.json",
            ),
            schema_summary_artifact_key=self._existing_run_key(
                run_id=run_id,
                relative_path="summaries/schema_summary.md",
            ),
            semantic_map_artifact_key=self._existing_run_key(
                run_id=run_id,
                relative_path="summaries/semantic_map.json",
            ),
        )
        context_key = self._existing_run_key(run_id=run_id, relative_path="contexts/learned_context.json")
        if context_key is not None:
            context_payload = self.object_store.read_json(context_key)
            if isinstance(context_payload, dict):
                context = self._learned_context_from_payload(context_payload)
                state = self._replace_state(state, context=context)
        return state

    def run_stages(
        self,
        *,
        business_context: BusinessContext | None = None,
        run_id: str | None = None,
        tables: list[str] | None = None,
        query_history_path: str | Path | None = None,
        query_history_records: list[QueryHistoryRecord] | None = None,
        business_grounding: dict[str, object] | None = None,
        start_stage: LearningStage | str | None = None,
        end_stage: LearningStage | str | None = None,
        existing_state: LearningRunState | None = None,
    ) -> LearningPipelineResult:
        sequence = self.default_stage_sequence()
        first_stage = self._coerce_stage(start_stage) or sequence[0]
        last_stage = self._coerce_stage(end_stage) or sequence[-1]
        if first_stage not in sequence:
            raise ValueError(f"Unsupported start stage for current strategy: {first_stage}")
        if last_stage not in sequence:
            raise ValueError(f"Unsupported end stage for current strategy: {last_stage}")
        if sequence.index(first_stage) > sequence.index(last_stage):
            raise ValueError("start_stage must be before or equal to end_stage")

        target_run_id = run_id or existing_state.run_id if existing_state is not None else run_id
        if target_run_id is None:
            target_run_id = self.settings.learning_run_id
        state = existing_state or self.load_run_state(run_id=target_run_id)
        if first_stage == LearningStage.DATA_COLLECTION:
            if business_context is None:
                raise ValueError("business_context is required when starting from data_collection")
            state = self._replace_state(state, run_id=target_run_id, context=None)
        else:
            if state.collection is None:
                raise ValueError(
                    f"Cannot start from {first_stage}: missing collected profile artifacts for run_id={target_run_id!r}"
                )

        executed: list[LearningStage] = []
        active_stages = sequence[sequence.index(first_stage) : sequence.index(last_stage) + 1]
        for stage in active_stages:
            state = self._execute_stage(
                stage=stage,
                state=state,
                business_context=business_context,
                tables=tables,
                query_history_path=query_history_path,
                query_history_records=query_history_records,
                business_grounding=business_grounding,
            )
            executed.append(stage)
        return self._result_from_state(state=state, executed_stages=executed)

    def run(
        self,
        *,
        business_context: BusinessContext,
        run_id: str | None = None,
        tables: list[str] | None = None,
        query_history_path: str | Path | None = None,
        query_history_records: list[QueryHistoryRecord] | None = None,
        business_grounding: dict[str, object] | None = None,
    ) -> LearningPipelineResult:
        return self.run_stages(
            business_context=business_context,
            run_id=run_id,
            tables=tables,
            query_history_path=query_history_path,
            query_history_records=query_history_records,
            business_grounding=business_grounding,
        )

    def _execute_stage(
        self,
        *,
        stage: LearningStage,
        state: LearningRunState,
        business_context: BusinessContext | None,
        tables: list[str] | None,
        query_history_path: str | Path | None,
        query_history_records: list[QueryHistoryRecord] | None,
        business_grounding: dict[str, object] | None,
    ) -> LearningRunState:
        if stage == LearningStage.DATA_COLLECTION:
            if business_context is None:
                raise ValueError("business_context is required for data_collection")
            collection = self.collect_data(
                business_context=business_context,
                run_id=state.run_id,
                tables=tables,
            )
            return LearningRunState(run_id=collection.run_id, collection=collection)

        collection = self._require_collection(state, stage)
        if stage == LearningStage.DESCRIPTION_GENERATION:
            description_key = self.generate_descriptions(
                collection,
                business_grounding=business_grounding,
            )
            return self._replace_state(state, description_artifact_key=description_key)
        if stage == LearningStage.JOIN_DISCOVERY:
            join_key = self.discover_joins(
                collection=collection,
                query_history_path=query_history_path,
                query_history_records=query_history_records,
            )
            return self._replace_state(state, joinable_pairs_artifact_key=join_key)
        if stage == LearningStage.CONTEXT_GRAPH_BUILDING:
            description_key = self._require_key(
                state.description_artifact_key,
                stage=stage,
                artifact_name="description_artifact_key",
            )
            graph_result = self.build_context_graph(
                collection=collection,
                description_artifact_key=description_key,
                joinable_pairs_artifact_key=state.joinable_pairs_artifact_key,
                business_grounding=business_grounding,
                query_history_path=query_history_path,
                query_history_records=query_history_records,
            )
            return self._replace_state(
                state,
                context_graph_manifest_artifact_key=graph_result.manifest_artifact_key,
                retrieval_documents_artifact_key=graph_result.retrieval_documents_artifact_key,
                retrieval_index_artifact_key=graph_result.bm25_index_artifact_key,
            )
        if stage == LearningStage.EMBEDDING_GENERATION:
            retrieval_documents_key = self._require_key(
                state.retrieval_documents_artifact_key,
                stage=stage,
                artifact_name="retrieval_documents_artifact_key",
            )
            embedding_result = self.build_embeddings(
                collection=collection,
                retrieval_documents_artifact_key=retrieval_documents_key,
            )
            return self._replace_state(
                state,
                embedding_manifest_artifact_key=embedding_result.manifest_artifact_key,
            )
        if stage == LearningStage.QUERY_LIBRARY_BUILDING:
            query_library_result = self.build_query_libraries(
                collection=collection,
                query_history_path=query_history_path,
                query_history_records=query_history_records,
                joinable_pairs_artifact_key=state.joinable_pairs_artifact_key,
                business_grounding=business_grounding,
            )
            return self._replace_state(
                state,
                query_libraries_manifest_artifact_key=query_library_result.manifest_artifact_key,
            )
        if stage == LearningStage.NUANCE_BUILDING:
            nuance_result = self.build_nuance_artifacts(
                collection=collection,
                business_grounding=business_grounding,
                query_libraries_manifest_artifact_key=state.query_libraries_manifest_artifact_key,
            )
            return self._replace_state(state, nuance_manifest_artifact_key=nuance_result.manifest_artifact_key)
        if stage == LearningStage.AGENTIC_ARTIFACT_GENERATION:
            description_key = self._require_key(
                state.description_artifact_key,
                stage=stage,
                artifact_name="description_artifact_key",
            )
            agentic_result = self.build_agentic_artifacts(
                collection=collection,
                description_artifact_key=description_key,
                joinable_pairs_artifact_key=state.joinable_pairs_artifact_key,
                query_history_path=query_history_path,
                query_history_records=query_history_records,
                business_grounding=business_grounding,
            )
            return self._replace_state(
                state,
                query_libraries_manifest_artifact_key=agentic_result.query_library_result.manifest_artifact_key,
                nuance_manifest_artifact_key=agentic_result.nuance_result.manifest_artifact_key,
                schema_ast_manifest_artifact_key=agentic_result.schema_ast_manifest_artifact_key,
                schema_summary_artifact_key=agentic_result.summary_artifact_key,
                semantic_map_artifact_key=agentic_result.semantic_map_artifact_key,
            )
        if stage == LearningStage.CONTEXT_TRAINING:
            description_key = self._require_key(
                state.description_artifact_key,
                stage=stage,
                artifact_name="description_artifact_key",
            )
            context = self.train_context(
                collection=collection,
                description_artifact_key=description_key,
                joinable_pairs_artifact_key=state.joinable_pairs_artifact_key,
                context_graph_manifest_artifact_key=state.context_graph_manifest_artifact_key,
                query_libraries_manifest_artifact_key=state.query_libraries_manifest_artifact_key,
                nuance_manifest_artifact_key=state.nuance_manifest_artifact_key,
                retrieval_index_artifact_key=state.retrieval_index_artifact_key,
                embedding_manifest_artifact_key=state.embedding_manifest_artifact_key,
                schema_ast_manifest_artifact_key=state.schema_ast_manifest_artifact_key,
                schema_summary_artifact_key=state.schema_summary_artifact_key,
                semantic_map_artifact_key=state.semantic_map_artifact_key,
            )
            return self._replace_state(state, context=context)
        raise ValueError(f"Unsupported learning stage: {stage}")

    def _require_collection(
        self,
        state: LearningRunState,
        stage: LearningStage,
    ) -> LearningCollection:
        if state.collection is None:
            raise ValueError(
                f"Cannot run {stage}: missing collection for run_id={state.run_id!r}. "
                "Start from data_collection or load an existing run with profile artifacts."
            )
        return state.collection

    def _require_key(
        self,
        key: str | None,
        *,
        stage: LearningStage,
        artifact_name: str,
    ) -> str:
        if key is None:
            raise ValueError(f"Cannot run {stage}: missing required artifact {artifact_name}")
        return key

    def _coerce_stage(self, stage: LearningStage | str | None) -> LearningStage | None:
        if stage is None:
            return None
        if isinstance(stage, LearningStage):
            return stage
        return LearningStage(str(stage).strip().lower())

    def _result_from_state(
        self,
        *,
        state: LearningRunState,
        executed_stages: list[LearningStage],
    ) -> LearningPipelineResult:
        return LearningPipelineResult(
            collection=state.collection,
            description_artifact_key=state.description_artifact_key,
            joinable_pairs_artifact_key=state.joinable_pairs_artifact_key,
            context_graph_artifact_key=state.context_graph_manifest_artifact_key,
            query_libraries_artifact_key=state.query_libraries_manifest_artifact_key,
            nuance_artifact_key=state.nuance_manifest_artifact_key,
            retrieval_index_artifact_key=state.retrieval_index_artifact_key,
            embedding_manifest_artifact_key=state.embedding_manifest_artifact_key,
            context=state.context,
            executed_stages=executed_stages,
            state=state,
        )

    def _existing_run_key(self, *, run_id: str, relative_path: str) -> str | None:
        key = self._run_artifact_key(run_id=run_id, relative_path=relative_path)
        return key if self.object_store.exists(key) else None

    def _run_artifact_key(self, *, run_id: str, relative_path: str) -> str:
        from diracdata.learning.paths import learning_artifact_key

        return learning_artifact_key(self.settings, run_id=run_id, relative_path=relative_path)

    def _replace_state(self, state: LearningRunState, **changes: Any) -> LearningRunState:
        payload = dict(state.__dict__)
        payload.update(changes)
        return LearningRunState(**payload)

    def _learned_context_from_payload(self, payload: dict[str, Any]) -> LearnedContext:
        scope_payload = payload["scope"]
        return LearnedContext(
            run_id=str(payload["run_id"]),
            scope=LearningScope(
                catalog=str(scope_payload["catalog"]),
                database=str(scope_payload["database"]),
                schema=str(scope_payload["schema"]),
            ),
            table_names=list(payload.get("table_names", [])),
            profile_artifact_key=str(payload["profile_artifact_key"]),
            llm_context_artifact_key=str(payload["llm_context_artifact_key"]),
            description_artifact_key=str(payload["description_artifact_key"]),
            context_artifact_key=str(payload["context_artifact_key"]),
            joinable_pairs_artifact_key=payload.get("joinable_pairs_artifact_key"),
            context_graph_manifest_artifact_key=payload.get("context_graph_manifest_artifact_key"),
            query_libraries_manifest_artifact_key=payload.get("query_libraries_manifest_artifact_key"),
            nuance_manifest_artifact_key=payload.get("nuance_manifest_artifact_key"),
            retrieval_index_artifact_key=payload.get("retrieval_index_artifact_key"),
            embedding_manifest_artifact_key=payload.get("embedding_manifest_artifact_key"),
            schema_ast_manifest_artifact_key=payload.get("schema_ast_manifest_artifact_key"),
            schema_summary_artifact_key=payload.get("schema_summary_artifact_key"),
            semantic_map_artifact_key=payload.get("semantic_map_artifact_key"),
            metadata=dict(payload.get("metadata", {})),
        )
