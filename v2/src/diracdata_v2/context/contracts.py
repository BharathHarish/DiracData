"""Small, explicit context-fabric contracts.

These contracts define what v2 is allowed to pass to an analytics agent. They
are intentionally plain dataclasses so the shape stays easy to inspect, serialize,
and revise while the product contract is still forming.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any


class NodeKind(str, Enum):
    DOMAIN = "domain"
    ENTITY = "entity"
    TABLE = "table"
    COLUMN = "column"
    METRIC = "metric"


class EdgeKind(str, Enum):
    CONTAINS = "contains"
    JOINS = "joins"
    ALIAS_OF = "alias_of"
    CONFOUNDS = "confounds"
    USES_COLUMN = "uses_column"
    IMPLEMENTED_BY = "implemented_by"


class TrustLevel(str, Enum):
    USER_PROVIDED = "user_provided"
    QUERY_HISTORY = "query_history"
    SELF_PLAY = "self_play"
    INFERRED = "inferred"


class ReviewStatus(str, Enum):
    APPROVED = "approved"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: NodeKind
    name: str
    path: tuple[str, ...]
    description: str = ""
    sql_ref: str | None = None
    aliases: tuple[str, ...] = ()
    grain: str | None = None
    allowed_values: tuple[str, ...] = ()
    null_meaning: str | None = None
    sql_guidance: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _to_plain_dict(self)


@dataclass(frozen=True)
class GraphEdge:
    id: str
    kind: EdgeKind
    from_node: str
    to_node: str
    description: str = ""
    sql_condition: str | None = None
    relationship: str | None = None
    grain_effect: str | None = None
    source: TrustLevel = TrustLevel.INFERRED
    confidence: str = "medium"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _to_plain_dict(self)


@dataclass(frozen=True)
class SqlLibraryEntry:
    id: str
    domain: str
    intent_terms: tuple[str, ...]
    sql: str
    parameters: tuple[str, ...] = ()
    required_nodes: tuple[str, ...] = ()
    required_edges: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    source: TrustLevel = TrustLevel.INFERRED
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _to_plain_dict(self)


@dataclass(frozen=True)
class ContextSlice:
    question: str
    selected_nodes: tuple[GraphNode, ...] = ()
    join_edges: tuple[GraphEdge, ...] = ()
    sql_library: tuple[SqlLibraryEntry, ...] = ()
    caveats: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _to_plain_dict(self)


def _to_plain_dict(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _to_plain_dict(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [_to_plain_dict(item) for item in value]
    if isinstance(value, list):
        return [_to_plain_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_plain_dict(item) for key, item in value.items()}
    return value

