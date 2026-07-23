"""Contracts for context-fabric knowledge sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class KnowledgeSourceType(StrEnum):
    QUERY_HISTORY = "query_history"
    GOLD_NL_SQL = "gold_nl_sql"
    EXPERIENCE = "experience"
    SELF_PLAY = "self_play"


class KnowledgeTrustLevel(StrEnum):
    GOLD = "gold"
    APPROVED = "approved"
    OBSERVED = "observed"
    SYNTHETIC = "synthetic"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class NormalizedKnowledgeCase:
    """One SQL-bearing learning case from any source.

    This is deliberately semantic-light. It records what was observed or
    provided, validates SQL references against schema metadata, and preserves
    provenance for later agentic learning steps.
    """

    case_id: str
    source_type: KnowledgeSourceType
    trust_level: KnowledgeTrustLevel
    sql: str
    question: str = ""
    tables: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    join_edges: tuple[dict[str, Any], ...] = ()
    review_status: str = ""
    result_shape: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "source_type": self.source_type.value,
            "trust_level": self.trust_level.value,
            "question": self.question,
            "sql": self.sql,
            "tables": list(self.tables),
            "columns": list(self.columns),
            "join_edges": [dict(item) for item in self.join_edges],
            "review_status": self.review_status,
            "result_shape": dict(self.result_shape),
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class NormalizedKnowledgeCorpus:
    """A normalized corpus with lightweight coverage summary."""

    cases: tuple[NormalizedKnowledgeCase, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        source_counts: dict[str, int] = {}
        trust_counts: dict[str, int] = {}
        tables: set[str] = set()
        columns: set[str] = set()
        join_edges: set[str] = set()
        for case in self.cases:
            source_counts[case.source_type.value] = source_counts.get(case.source_type.value, 0) + 1
            trust_counts[case.trust_level.value] = trust_counts.get(case.trust_level.value, 0) + 1
            tables.update(case.tables)
            columns.update(case.columns)
            join_edges.update(str(edge.get("sql_condition") or "") for edge in case.join_edges)
        return {
            "case_count": len(self.cases),
            "source_counts": dict(sorted(source_counts.items())),
            "trust_counts": dict(sorted(trust_counts.items())),
            "table_count": len(tables),
            "column_count": len(columns),
            "join_edge_count": len({edge for edge in join_edges if edge}),
            "tables": sorted(tables),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "metadata": dict(self.metadata),
            "cases": [case.to_dict() for case in self.cases],
        }

