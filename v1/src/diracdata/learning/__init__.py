"""Learning-phase modules for schema context building."""

from diracdata.learning.agentic import AgenticLearningArtifactBuilder, AgenticLearningBuildResult
from diracdata.learning.collector import SchemaLearningCollector
from diracdata.learning.context_graph import ContextGraphBuilder, ContextGraphBuildResult
from diracdata.learning.descriptions import MetadataDescriptionGenerator
from diracdata.learning.embeddings import EmbeddingBuildResult, EmbeddingIndexBuilder
from diracdata.learning.learning_pipeline import (
    LearningPipeline,
    LearningPipelineResult,
    LearningRunState,
)
from diracdata.learning.libraries import QueryLibraryBuilder, QueryLibraryBuildResult
from diracdata.learning.joins import (
    JoinDiscoveryResult,
    JoinablePairDiscovery,
    learning_collection_from_profile_artifact,
)
from diracdata.learning.models import (
    BusinessContext,
    ColumnProfile,
    JoinConfidence,
    JoinDiscoverySource,
    JoinablePair,
    LearnedContext,
    LearningArtifactKind,
    LearningCollection,
    LearningScope,
    LearningStage,
    LLMProvider,
    TableProfile,
)
from diracdata.learning.nuance import NuanceArtifactBuilder, NuanceBuildResult
from diracdata.learning.query_history import (
    QueryHistoryRecord,
    load_query_history_csv,
    query_history_fieldnames,
)
from diracdata.learning.training import SchemaContextTrainer

__all__ = [
    "BusinessContext",
    "AgenticLearningArtifactBuilder",
    "AgenticLearningBuildResult",
    "ContextGraphBuilder",
    "ContextGraphBuildResult",
    "EmbeddingBuildResult",
    "EmbeddingIndexBuilder",
    "ColumnProfile",
    "JoinConfidence",
    "JoinDiscoveryResult",
    "JoinDiscoverySource",
    "JoinablePair",
    "JoinablePairDiscovery",
    "LearnedContext",
    "LLMProvider",
    "LearningArtifactKind",
    "LearningCollection",
    "LearningPipeline",
    "LearningPipelineResult",
    "LearningRunState",
    "LearningScope",
    "LearningStage",
    "MetadataDescriptionGenerator",
    "NuanceArtifactBuilder",
    "NuanceBuildResult",
    "QueryHistoryRecord",
    "QueryLibraryBuilder",
    "QueryLibraryBuildResult",
    "SchemaContextTrainer",
    "SchemaLearningCollector",
    "TableProfile",
    "learning_collection_from_profile_artifact",
    "load_query_history_csv",
    "query_history_fieldnames",
]
