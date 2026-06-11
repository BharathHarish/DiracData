"""Learning-phase data models."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class LearningStage(StrEnum):
    DATA_COLLECTION = "data_collection"
    PROFILING = "profiling"
    DESCRIPTION_GENERATION = "description_generation"
    JOIN_DISCOVERY = "join_discovery"
    CONTEXT_GRAPH_BUILDING = "context_graph_building"
    QUERY_LIBRARY_BUILDING = "query_library_building"
    NUANCE_BUILDING = "nuance_building"
    AGENTIC_ARTIFACT_GENERATION = "agentic_artifact_generation"
    EMBEDDING_GENERATION = "embedding_generation"
    CONTEXT_TRAINING = "context_training"


class LearningArtifactKind(StrEnum):
    SAMPLE = "samples"
    PROFILE = "profiles"
    DESCRIPTION = "descriptions"
    JOIN = "joins"
    CONTEXT_GRAPH = "context_graph"
    RETRIEVAL = "retrieval"
    EMBEDDING = "embeddings"
    CONTEXT = "contexts"


class LLMProvider(StrEnum):
    ANTHROPIC = "anthropic"


class JoinDiscoverySource(StrEnum):
    QUERY_HISTORY = "query_history"
    PROFILE_SAMPLE = "profile_sample"


class JoinConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class LearningScope:
    catalog: str
    database: str
    schema: str


@dataclass(frozen=True)
class BusinessContext:
    text: str
    table_descriptions: dict[str, str] = field(default_factory=dict)
    column_descriptions: dict[str, dict[str, str]] = field(default_factory=dict)
    glossary: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "BusinessContext":
        table_descriptions = _string_map(payload.get("table_descriptions", {}))
        column_descriptions = {
            table_name: _string_map(columns)
            for table_name, columns in _dict_map(payload.get("column_descriptions", {})).items()
        }
        glossary = _string_map(payload.get("glossary", {}))
        return cls(
            text=str(payload.get("text", "")),
            table_descriptions=table_descriptions,
            column_descriptions=column_descriptions,
            glossary=glossary,
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "BusinessContext":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("business context JSON must be an object")
        return cls.from_mapping(payload)


@dataclass(frozen=True)
class ColumnProfile:
    table_name: str
    column_name: str
    data_type: str
    null_count: int | None
    null_rate: float | None
    distinct_count: int | None
    min_value: Any = None
    max_value: Any = None
    top_values: list[dict[str, Any]] = field(default_factory=list)
    distinct_values: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class TableProfile:
    table_name: str
    row_count: int
    sample_artifact_key: str
    columns: list[ColumnProfile]


@dataclass(frozen=True)
class LearningCollection:
    run_id: str
    scope: LearningScope
    table_profiles: list[TableProfile]
    profile_artifact_key: str
    llm_context_artifact_key: str


@dataclass(frozen=True)
class MetadataDescription:
    short_description: str
    long_description: str


@dataclass(frozen=True)
class MetadataDescriptions:
    tables: dict[str, MetadataDescription]
    columns: dict[str, dict[str, MetadataDescription]]


@dataclass(frozen=True)
class LearnedContext:
    run_id: str
    scope: LearningScope
    table_names: list[str]
    profile_artifact_key: str
    llm_context_artifact_key: str
    description_artifact_key: str
    context_artifact_key: str
    joinable_pairs_artifact_key: str | None = None
    context_graph_manifest_artifact_key: str | None = None
    query_libraries_manifest_artifact_key: str | None = None
    nuance_manifest_artifact_key: str | None = None
    retrieval_index_artifact_key: str | None = None
    embedding_manifest_artifact_key: str | None = None
    schema_ast_manifest_artifact_key: str | None = None
    schema_summary_artifact_key: str | None = None
    semantic_map_artifact_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JoinablePair:
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    join_type: str
    confidence: JoinConfidence


def to_jsonable(value: object) -> object:
    """Convert dataclasses and common DB values to JSON-safe values."""
    if hasattr(value, "__dataclass_fields__"):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _dict_map(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _string_map(value: object) -> dict[str, str]:
    return {key: str(item) for key, item in _dict_map(value).items()}
