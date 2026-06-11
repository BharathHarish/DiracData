"""Contracts for the agent-facing semantic catalog.

The catalog is intentionally plain JSON. Learning may use LLMs to propose
semantics, but the stored artifact must be deterministic, validated, and easy
for runtime compilers to consume.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any


class CatalogCardKind(str, Enum):
    DOMAIN = "domain"
    ENTITY = "entity"
    TABLE = "table"
    COLUMN = "column"
    DIMENSION = "dimension"
    METRIC = "metric"
    BUSINESS_TERM = "business_term"
    SQL_PATTERN = "sql_pattern"
    ASSERTION = "assertion"
    VALUE = "value"


class CatalogSource(str, Enum):
    DESCRIPTION = "description"
    QUERY_HISTORY = "query_history"
    SELF_PLAY = "self_play"
    AGENTIC_LEARNING = "agentic_learning"
    INFERRED = "inferred"


class CatalogReviewStatus(str, Enum):
    OBSERVED = "observed"
    APPROVED = "approved"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class CatalogCard:
    id: str
    kind: CatalogCardKind
    name: str
    description: str
    terms: tuple[str, ...] = ()
    sql_ref: str | None = None
    parent_ids: tuple[str, ...] = ()
    source: CatalogSource = CatalogSource.INFERRED
    review_status: CatalogReviewStatus = CatalogReviewStatus.NEEDS_REVIEW
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _to_plain(self)


@dataclass(frozen=True)
class CatalogJoinEdge:
    id: str
    left_column: str
    right_column: str
    sql_condition: str
    tables: tuple[str, str]
    source: CatalogSource = CatalogSource.QUERY_HISTORY
    review_status: CatalogReviewStatus = CatalogReviewStatus.OBSERVED
    observed_count: int = 1
    source_entry_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _to_plain(self)


@dataclass(frozen=True)
class CompiledContext:
    question: str
    needs_clarification: bool
    resolved_terms: tuple[dict[str, Any], ...] = ()
    unresolved_terms: tuple[dict[str, Any], ...] = ()
    candidate_cards: tuple[dict[str, Any], ...] = ()
    sql_patterns: tuple[dict[str, Any], ...] = ()
    join_edges: tuple[dict[str, Any], ...] = ()
    assertions: tuple[str, ...] = ()
    retrieval: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _to_plain(self)


def _to_plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _to_plain(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_plain(item) for key, item in value.items()}
    return value
