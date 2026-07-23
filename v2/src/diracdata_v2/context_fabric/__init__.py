"""Context-fabric learning utilities."""

from diracdata_v2.context_fabric.contracts import (
    KnowledgeSourceType,
    KnowledgeTrustLevel,
    NormalizedKnowledgeCase,
    NormalizedKnowledgeCorpus,
)
from diracdata_v2.context_fabric.sources import (
    build_normalized_corpus,
    normalize_experience_candidates,
    normalize_nl_sql_pairs,
    normalize_query_history,
    normalize_sql_library_entries,
    table_columns_from_metadata,
)
from diracdata_v2.context_fabric.pathways import (
    SemanticPathway,
    SemanticPathwayBuildResult,
    SemanticPathwayBuilder,
)
from diracdata_v2.context_fabric.nuances import (
    ColumnEvidence,
    ColumnEvidenceProfiler,
    ColumnNuanceBuildResult,
    ColumnNuanceBuilder,
    ColumnNuanceCard,
)
from diracdata_v2.context_fabric.join_miner import (
    JoinColumnProfile,
    JoinMiner,
    JoinMinerBuildResult,
    JoinRelationshipEdge,
)
from diracdata_v2.context_fabric.assertions import (
    RetrievedSemanticAssertion,
    SemanticAssertion,
    SemanticAssertionBuildResult,
    SemanticAssertionBuilder,
    SemanticAssertionEvidence,
    SemanticAssertionLibrary,
)
from diracdata_v2.context_fabric.semantic_coverage import (
    SemanticCoverageBuildResult,
    SemanticCoverageBuilder,
)
from diracdata_v2.context_fabric.self_play_sql import (
    SemanticSelfPlaySQLBuildResult,
    SemanticSelfPlaySQLBuilder,
    promote_validated_self_play,
)

__all__ = [
    "KnowledgeSourceType",
    "KnowledgeTrustLevel",
    "NormalizedKnowledgeCase",
    "NormalizedKnowledgeCorpus",
    "SemanticPathway",
    "SemanticPathwayBuildResult",
    "SemanticPathwayBuilder",
    "ColumnEvidence",
    "ColumnEvidenceProfiler",
    "ColumnNuanceBuildResult",
    "ColumnNuanceBuilder",
    "ColumnNuanceCard",
    "JoinColumnProfile",
    "JoinMiner",
    "JoinMinerBuildResult",
    "JoinRelationshipEdge",
    "RetrievedSemanticAssertion",
    "SemanticAssertion",
    "SemanticAssertionBuildResult",
    "SemanticAssertionBuilder",
    "SemanticAssertionEvidence",
    "SemanticAssertionLibrary",
    "SemanticCoverageBuildResult",
    "SemanticCoverageBuilder",
    "SemanticSelfPlaySQLBuildResult",
    "SemanticSelfPlaySQLBuilder",
    "promote_validated_self_play",
    "build_normalized_corpus",
    "normalize_experience_candidates",
    "normalize_nl_sql_pairs",
    "normalize_query_history",
    "normalize_sql_library_entries",
    "table_columns_from_metadata",
]
