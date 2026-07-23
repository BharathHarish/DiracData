"""Join graph mining for the context fabric."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

from diracdata_v2.context_fabric.contracts import NormalizedKnowledgeCorpus
from diracdata_v2.query import DuckDBEngine


class JoinClassifier(Protocol):
    """LLM adapter used to classify join relationships from supplied evidence."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


@dataclass(frozen=True)
class JoinColumnProfile:
    column_ref: str
    table_name: str
    column_name: str
    column_type: str = ""
    description: str = ""
    table_description: str = ""
    usage_count: int = 0
    data_profile: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_ref": self.column_ref,
            "table_name": self.table_name,
            "column_name": self.column_name,
            "column_type": self.column_type,
            "description": self.description,
            "table_description": self.table_description,
            "usage_count": self.usage_count,
            "data_profile": dict(self.data_profile),
        }


@dataclass(frozen=True)
class JoinRelationshipEdge:
    edge_id: str
    left_column: str
    right_column: str
    tables: tuple[str, str]
    sql_condition: str
    evidence_type: str
    score: float = 0.0
    observed_count: int = 0
    source_case_ids: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    trust_levels: tuple[str, ...] = ()
    features: dict[str, Any] = field(default_factory=dict)
    relationship_type: str = "unknown"
    grain_effect: str = "unknown"
    default_behavior: str = ""
    competing_edge_ids: tuple[str, ...] = ()
    review_status: str = "needs_review"
    learning_mode: str = "evidence_only"
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "left_column": self.left_column,
            "right_column": self.right_column,
            "tables": list(self.tables),
            "sql_condition": self.sql_condition,
            "evidence_type": self.evidence_type,
            "score": self.score,
            "observed_count": self.observed_count,
            "source_case_ids": list(self.source_case_ids),
            "source_types": list(self.source_types),
            "trust_levels": list(self.trust_levels),
            "features": dict(self.features),
            "relationship_type": self.relationship_type,
            "grain_effect": self.grain_effect,
            "default_behavior": self.default_behavior,
            "competing_edge_ids": list(self.competing_edge_ids),
            "review_status": self.review_status,
            "learning_mode": self.learning_mode,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class JoinMinerBuildResult:
    document: dict[str, Any]
    edges: tuple[JoinRelationshipEdge, ...]
    column_profiles: tuple[JoinColumnProfile, ...]
    generation_errors: tuple[str, ...] = ()
    local_path: Path | None = None
    object_key: str | None = None


class JoinMiner:
    """Build observed and candidate join relationships from generic evidence."""

    def __init__(
        self,
        *,
        engine: DuckDBEngine | None = None,
        classifier: JoinClassifier | None = None,
        max_tables: int = 30,
        max_columns_per_table: int = 16,
        max_candidate_edges: int = 80,
        max_overlap_checks: int = 800,
        sample_limit: int = 1000,
        min_candidate_score: float = 0.35,
        min_candidate_distinct_count: int = 2,
        batch_size: int = 8,
        strict_agentic: bool = False,
    ) -> None:
        self.engine = engine
        self.classifier = classifier
        self.max_tables = max(1, max_tables)
        self.max_columns_per_table = max(1, max_columns_per_table)
        self.max_candidate_edges = max(0, max_candidate_edges)
        self.max_overlap_checks = max(0, max_overlap_checks)
        self.sample_limit = max(1, sample_limit)
        self.min_candidate_score = max(0.0, min(1.0, min_candidate_score))
        self.min_candidate_distinct_count = max(1, min_candidate_distinct_count)
        self.batch_size = max(1, batch_size)
        self.strict_agentic = strict_agentic

    def build(
        self,
        *,
        corpus: NormalizedKnowledgeCorpus,
        metadata_descriptions: dict[str, Any],
        catalog: str,
        database: str,
        schema: str,
        run_id: str,
        output_dir: Path | None = None,
        object_store: Any | None = None,
        object_prefix: str = "v2/learning/artifacts",
    ) -> JoinMinerBuildResult:
        table_columns = _table_columns_from_metadata(metadata_descriptions)
        observed = _observed_edges(corpus=corpus, table_columns=table_columns)
        selected_tables = _selected_tables(corpus=corpus, table_columns=table_columns, max_tables=self.max_tables)
        usage = _column_usage(corpus)
        profiles = self._profile_columns(
            table_columns=table_columns,
            metadata_descriptions=metadata_descriptions,
            selected_tables=selected_tables,
            usage=usage,
        )
        candidate_edges = self._candidate_edges(profiles=profiles, observed_edges=observed)
        edges = _merge_edges(observed_edges=observed, candidate_edges=candidate_edges)
        edges = _attach_competing_edges(edges)
        edges = _attach_column_evidence(edges=edges, profiles=profiles)
        classified_edges, generation_errors = self._classify_edges(edges)
        if self.strict_agentic and self.classifier is not None:
            generated_count = sum(1 for edge in classified_edges if edge.learning_mode == "generated")
            if generation_errors or generated_count != len(edges):
                details = "; ".join(generation_errors[:3]) or "not all join edges were classified"
                raise RuntimeError(f"agentic join classification failed: {details}")
        document = _document(
            edges=classified_edges,
            column_profiles=profiles,
            generation_errors=generation_errors,
            catalog=catalog,
            database=database,
            schema=schema,
            run_id=run_id,
        )
        local_path = None
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            local_path = output_dir / "join_graph.json"
            local_path.write_text(json.dumps(document, indent=2, sort_keys=True, default=str), encoding="utf-8")
        object_key = None
        if object_store is not None:
            object_key = f"{object_prefix.strip('/')}/{run_id}/join_graph.json"
            object_store.write_json(object_key, document)
        return JoinMinerBuildResult(
            document=document,
            edges=tuple(classified_edges),
            column_profiles=profiles,
            generation_errors=tuple(generation_errors),
            local_path=local_path,
            object_key=object_key,
        )

    def _profile_columns(
        self,
        *,
        table_columns: dict[str, list[str]],
        metadata_descriptions: dict[str, Any],
        selected_tables: tuple[str, ...],
        usage: dict[str, int],
    ) -> tuple[JoinColumnProfile, ...]:
        describe_by_table = self._describe_by_table(selected_tables)
        profiles = []
        for table in selected_tables:
            columns = table_columns.get(table, [])
            ranked_columns = sorted(
                columns,
                key=lambda column: (
                    -usage.get(f"{table}.{column}", 0),
                    column,
                ),
            )[: self.max_columns_per_table]
            type_lookup = describe_by_table.get(table, {})
            for column in ranked_columns:
                column_ref = f"{table}.{column}"
                profiles.append(
                    JoinColumnProfile(
                        column_ref=column_ref,
                        table_name=table,
                        column_name=column,
                        column_type=type_lookup.get(column, ""),
                        description=_column_description(metadata_descriptions, table, column),
                        table_description=_table_description(metadata_descriptions, table),
                        usage_count=usage.get(column_ref, 0),
                        data_profile=self._profile_column_data(table=table, column=column),
                    )
                )
        return tuple(profiles)

    def _describe_by_table(self, tables: tuple[str, ...]) -> dict[str, dict[str, str]]:
        if self.engine is None:
            return {}
        output = {}
        available = set(self.engine.list_tables())
        for table in tables:
            if table not in available:
                continue
            output[table] = {
                item["column_name"]: item["column_type"]
                for item in self.engine.describe_columns(table)
            }
        return output

    def _profile_column_data(self, *, table: str, column: str) -> dict[str, Any]:
        if self.engine is None:
            return {"status": "not_configured"}
        if table not in set(self.engine.list_tables()) or column not in set(self.engine.list_columns(table)):
            return {"status": "missing"}
        try:
            stats_sql = (
                f"SELECT COUNT(*) AS row_count, "
                f"COUNT({_identifier(column)}) AS non_null_count, "
                f"COUNT(DISTINCT {_identifier(column)}) AS distinct_count "
                f"FROM {_identifier(table)}"
            )
            stats_result = self.engine.query(stats_sql, max_rows=1)
            stats = dict(zip(stats_result.columns, stats_result.rows[0], strict=False)) if stats_result.rows else {}
            row_count = _int(stats.get("row_count"))
            non_null_count = _int(stats.get("non_null_count"))
            null_count = max(0, row_count - non_null_count)
            return {
                "status": "ok",
                "row_count": row_count,
                "non_null_count": non_null_count,
                "null_count": null_count,
                "null_rate": round(null_count / row_count, 6) if row_count else 0.0,
                "distinct_count": _int(stats.get("distinct_count")),
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    def _candidate_edges(
        self,
        *,
        profiles: tuple[JoinColumnProfile, ...],
        observed_edges: list[JoinRelationshipEdge],
    ) -> list[JoinRelationshipEdge]:
        if self.engine is None or self.max_candidate_edges <= 0:
            return []
        observed_ids = {edge.edge_id for edge in observed_edges}
        profiles_by_table: dict[str, list[JoinColumnProfile]] = defaultdict(list)
        for profile in profiles:
            if profile.data_profile.get("status") == "ok":
                profiles_by_table[profile.table_name].append(profile)
        scored: list[JoinRelationshipEdge] = []
        overlap_checks = 0
        tables = sorted(profiles_by_table)
        for left_index, left_table in enumerate(tables):
            for right_table in tables[left_index + 1 :]:
                for left in profiles_by_table[left_table]:
                    for right in profiles_by_table[right_table]:
                        if not _type_compatible(left.column_type, right.column_type):
                            continue
                        if (
                            min(
                                _int(left.data_profile.get("distinct_count")),
                                _int(right.data_profile.get("distinct_count")),
                            )
                            < self.min_candidate_distinct_count
                        ):
                            continue
                        if overlap_checks >= self.max_overlap_checks:
                            break
                        overlap_checks += 1
                        features = _base_features(left, right)
                        features.update(self._overlap_features(left, right))
                        score = _candidate_score(features)
                        edge_id, left_ref, right_ref = _edge_identity(left.column_ref, right.column_ref)
                        if edge_id in observed_ids or score < self.min_candidate_score:
                            continue
                        if _int(features.get("overlap_count")) <= 0:
                            continue
                        tables_pair = tuple(sorted((left_ref.split(".", 1)[0], right_ref.split(".", 1)[0])))
                        scored.append(
                            JoinRelationshipEdge(
                                edge_id=edge_id,
                                left_column=left_ref,
                                right_column=right_ref,
                                tables=(tables_pair[0], tables_pair[1]),
                                sql_condition=f"{left_ref} = {right_ref}",
                                evidence_type="candidate",
                                score=score,
                                features=features,
                                review_status="needs_review",
                            )
                        )
                    if overlap_checks >= self.max_overlap_checks:
                        break
                if overlap_checks >= self.max_overlap_checks:
                    break
            if overlap_checks >= self.max_overlap_checks:
                break
        return sorted(scored, key=lambda edge: (-edge.score, edge.sql_condition))[: self.max_candidate_edges]

    def _overlap_features(self, left: JoinColumnProfile, right: JoinColumnProfile) -> dict[str, Any]:
        try:
            sql = f"""
            WITH
              left_values AS (
                SELECT DISTINCT {_identifier(left.column_name)} AS value
                FROM {_identifier(left.table_name)}
                WHERE {_identifier(left.column_name)} IS NOT NULL
                LIMIT {int(self.sample_limit)}
              ),
              right_values AS (
                SELECT DISTINCT {_identifier(right.column_name)} AS value
                FROM {_identifier(right.table_name)}
                WHERE {_identifier(right.column_name)} IS NOT NULL
                LIMIT {int(self.sample_limit)}
              )
            SELECT
              (SELECT COUNT(*) FROM left_values) AS left_sample_count,
              (SELECT COUNT(*) FROM right_values) AS right_sample_count,
              (SELECT COUNT(*) FROM (SELECT value FROM left_values INTERSECT SELECT value FROM right_values)) AS overlap_count
            """
            result = self.engine.query(sql, max_rows=1) if self.engine is not None else None
            row = result.rows[0] if result and result.rows else (0, 0, 0)
            left_count = _int(row[0])
            right_count = _int(row[1])
            overlap = _int(row[2])
            denominator = max(1, min(left_count, right_count))
            return {
                "left_sample_count": left_count,
                "right_sample_count": right_count,
                "overlap_count": overlap,
                "sample_overlap_rate": round(overlap / denominator, 6),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "left_sample_count": 0,
                "right_sample_count": 0,
                "overlap_count": 0,
                "sample_overlap_rate": 0.0,
                "overlap_error": f"{type(exc).__name__}: {exc}",
            }

    def _classify_edges(
        self,
        edges: list[JoinRelationshipEdge],
    ) -> tuple[list[JoinRelationshipEdge], list[str]]:
        if self.classifier is None:
            return edges, []
        generated: dict[str, JoinRelationshipEdge] = {}
        errors = []
        for batch_index, batch in enumerate(_batches(edges, self.batch_size), start=1):
            prompt = _classification_prompt().replace(
                "{{join_evidence_json}}",
                json.dumps([edge.to_dict() for edge in batch], indent=2, sort_keys=True, default=str),
            )
            try:
                text = self.classifier.complete(
                    [
                        {
                            "role": "system",
                            "content": (
                                "You classify SQL join relationships from supplied evidence. "
                                "Return only valid JSON."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ]
                )
                parsed = _parse_classified_edges(text, source_edges=batch)
                if not parsed:
                    errors.append(
                        f"batch {batch_index} returned no parseable join classifications"
                    )
                generated.update({edge.edge_id: edge for edge in parsed})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"batch {batch_index} failed: {type(exc).__name__}: {exc}")
        output = []
        for edge in edges:
            output.append(generated.get(edge.edge_id, edge))
        return output, errors


def _observed_edges(
    *,
    corpus: NormalizedKnowledgeCorpus,
    table_columns: dict[str, list[str]],
) -> list[JoinRelationshipEdge]:
    state: dict[str, dict[str, Any]] = {}
    for case in corpus.cases:
        for raw_edge in case.join_edges:
            left = str(raw_edge.get("left_column") or "")
            right = str(raw_edge.get("right_column") or "")
            if not _valid_column(left, table_columns) or not _valid_column(right, table_columns):
                continue
            edge_id, left_ref, right_ref = _edge_identity(left, right)
            entry = state.setdefault(
                edge_id,
                {
                    "left": left_ref,
                    "right": right_ref,
                    "count": 0,
                    "case_ids": set(),
                    "source_types": set(),
                    "trust_levels": set(),
                },
            )
            entry["count"] += 1
            entry["case_ids"].add(case.case_id)
            entry["source_types"].add(case.source_type.value)
            entry["trust_levels"].add(case.trust_level.value)
    edges = []
    for edge_id, item in sorted(state.items()):
        left = item["left"]
        right = item["right"]
        tables = tuple(sorted((left.split(".", 1)[0], right.split(".", 1)[0])))
        edges.append(
            JoinRelationshipEdge(
                edge_id=edge_id,
                left_column=left,
                right_column=right,
                tables=(tables[0], tables[1]),
                sql_condition=f"{left} = {right}",
                evidence_type="observed",
                score=1.0,
                observed_count=int(item["count"]),
                source_case_ids=tuple(sorted(item["case_ids"])[:50]),
                source_types=tuple(sorted(item["source_types"])),
                trust_levels=tuple(sorted(item["trust_levels"])),
                features={"observed_count": int(item["count"])},
                relationship_type="observed_join",
                review_status="observed",
            )
        )
    return edges


def _merge_edges(
    *,
    observed_edges: list[JoinRelationshipEdge],
    candidate_edges: list[JoinRelationshipEdge],
) -> list[JoinRelationshipEdge]:
    by_id = {edge.edge_id: edge for edge in observed_edges}
    for candidate in candidate_edges:
        existing = by_id.get(candidate.edge_id)
        if existing is None:
            by_id[candidate.edge_id] = candidate
            continue
        by_id[candidate.edge_id] = JoinRelationshipEdge(
            edge_id=existing.edge_id,
            left_column=existing.left_column,
            right_column=existing.right_column,
            tables=existing.tables,
            sql_condition=existing.sql_condition,
            evidence_type="both",
            score=max(existing.score, candidate.score),
            observed_count=existing.observed_count,
            source_case_ids=existing.source_case_ids,
            source_types=existing.source_types,
            trust_levels=existing.trust_levels,
            features={**candidate.features, **existing.features},
            relationship_type=existing.relationship_type,
            grain_effect=existing.grain_effect,
            default_behavior=existing.default_behavior,
            review_status=existing.review_status,
        )
    return sorted(
        by_id.values(),
        key=lambda edge: (
            0 if edge.evidence_type in {"observed", "both"} else 1,
            -edge.observed_count,
            -edge.score,
            edge.sql_condition,
        ),
    )


def _attach_competing_edges(edges: list[JoinRelationshipEdge]) -> list[JoinRelationshipEdge]:
    by_table_pair: dict[tuple[str, str], list[str]] = defaultdict(list)
    for edge in edges:
        by_table_pair[edge.tables].append(edge.edge_id)
    output = []
    for edge in edges:
        competitors = tuple(item for item in by_table_pair.get(edge.tables, []) if item != edge.edge_id)[:20]
        output.append(
            JoinRelationshipEdge(
                **{
                    **edge.to_dict(),
                    "tables": edge.tables,
                    "source_case_ids": edge.source_case_ids,
                    "source_types": edge.source_types,
                    "trust_levels": edge.trust_levels,
                    "competing_edge_ids": competitors,
                }
            )
        )
    return output


def _attach_column_evidence(
    *,
    edges: list[JoinRelationshipEdge],
    profiles: tuple[JoinColumnProfile, ...],
) -> list[JoinRelationshipEdge]:
    by_column = {profile.column_ref: profile for profile in profiles}
    output = []
    for edge in edges:
        features = dict(edge.features)
        left = by_column.get(edge.left_column)
        right = by_column.get(edge.right_column)
        if left is not None:
            features["left_column_evidence"] = _compact_column_evidence(left)
        if right is not None:
            features["right_column_evidence"] = _compact_column_evidence(right)
        output.append(
            JoinRelationshipEdge(
                edge_id=edge.edge_id,
                left_column=edge.left_column,
                right_column=edge.right_column,
                tables=edge.tables,
                sql_condition=edge.sql_condition,
                evidence_type=edge.evidence_type,
                score=edge.score,
                observed_count=edge.observed_count,
                source_case_ids=edge.source_case_ids,
                source_types=edge.source_types,
                trust_levels=edge.trust_levels,
                features=features,
                relationship_type=edge.relationship_type,
                grain_effect=edge.grain_effect,
                default_behavior=edge.default_behavior,
                competing_edge_ids=edge.competing_edge_ids,
                review_status=edge.review_status,
                learning_mode=edge.learning_mode,
                rationale=edge.rationale,
            )
        )
    return output


def _compact_column_evidence(profile: JoinColumnProfile) -> dict[str, Any]:
    data_profile = profile.data_profile
    return {
        "column_type": profile.column_type,
        "description": profile.description,
        "usage_count": profile.usage_count,
        "row_count": data_profile.get("row_count"),
        "distinct_count": data_profile.get("distinct_count"),
        "null_rate": data_profile.get("null_rate"),
    }


def _parse_classified_edges(
    text: str,
    *,
    source_edges: list[JoinRelationshipEdge],
) -> list[JoinRelationshipEdge]:
    payload = _json_object(text)
    raw_edges = payload.get("edges", [])
    if not isinstance(raw_edges, list):
        return []
    source_by_id = {edge.edge_id: edge for edge in source_edges}
    output = []
    for raw in raw_edges:
        if not isinstance(raw, dict):
            continue
        edge_id = _one_line(raw.get("edge_id"))
        source = source_by_id.get(edge_id)
        if source is None:
            continue
        output.append(
            JoinRelationshipEdge(
                edge_id=source.edge_id,
                left_column=source.left_column,
                right_column=source.right_column,
                tables=source.tables,
                sql_condition=source.sql_condition,
                evidence_type=source.evidence_type,
                score=source.score,
                observed_count=source.observed_count,
                source_case_ids=source.source_case_ids,
                source_types=source.source_types,
                trust_levels=source.trust_levels,
                features=source.features,
                relationship_type=_one_line(raw.get("relationship_type")) or source.relationship_type,
                grain_effect=_one_line(raw.get("grain_effect")) or source.grain_effect,
                default_behavior=_one_line(raw.get("default_behavior")) or source.default_behavior,
                competing_edge_ids=source.competing_edge_ids,
                review_status=_generated_review_status(raw.get("review_status")),
                learning_mode="generated",
                rationale=_one_line(raw.get("rationale")),
            )
        )
    return output


def _document(
    *,
    edges: list[JoinRelationshipEdge],
    column_profiles: tuple[JoinColumnProfile, ...],
    generation_errors: list[str],
    catalog: str,
    database: str,
    schema: str,
    run_id: str,
) -> dict[str, Any]:
    return {
        "version": 1,
        "artifact_type": "join_graph_v2",
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "scope": {"catalog": catalog, "database": database, "schema": schema},
        "summary": {
            "edge_count": len(edges),
            "column_profile_count": len(column_profiles),
            "evidence_type_counts": dict(sorted(Counter(edge.evidence_type for edge in edges).items())),
            "review_counts": dict(sorted(Counter(edge.review_status for edge in edges).items())),
            "learning_mode_counts": dict(sorted(Counter(edge.learning_mode for edge in edges).items())),
            "generation_error_count": len(generation_errors),
        },
        "generation_errors": list(generation_errors),
        "column_profiles": {profile.column_ref: profile.to_dict() for profile in column_profiles},
        "join_edges": {edge.edge_id: edge.to_dict() for edge in edges},
        "indexes": _indexes(edges),
    }


def _indexes(edges: list[JoinRelationshipEdge]) -> dict[str, Any]:
    by_table: dict[str, list[str]] = defaultdict(list)
    by_column: dict[str, list[str]] = defaultdict(list)
    by_pair: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        for table in edge.tables:
            by_table[table].append(edge.edge_id)
        by_column[edge.left_column].append(edge.edge_id)
        by_column[edge.right_column].append(edge.edge_id)
        by_pair["::".join(edge.tables)].append(edge.edge_id)
    return {
        "join_edges_by_table": _sorted_index(by_table),
        "join_edges_by_column": _sorted_index(by_column),
        "join_edges_by_table_pair": _sorted_index(by_pair),
    }


def _selected_tables(
    *,
    corpus: NormalizedKnowledgeCorpus,
    table_columns: dict[str, list[str]],
    max_tables: int,
) -> tuple[str, ...]:
    counts: Counter[str] = Counter()
    for case in corpus.cases:
        counts.update(case.tables)
    ranked = sorted(table_columns, key=lambda table: (-counts.get(table, 0), table))
    return tuple(ranked[:max_tables])


def _column_usage(corpus: NormalizedKnowledgeCorpus) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for case in corpus.cases:
        counts.update(case.columns)
    return dict(counts)


def _base_features(left: JoinColumnProfile, right: JoinColumnProfile) -> dict[str, Any]:
    left_distinct = _int(left.data_profile.get("distinct_count"))
    right_distinct = _int(right.data_profile.get("distinct_count"))
    max_distinct = max(left_distinct, right_distinct, 1)
    min_distinct = min(left_distinct, right_distinct)
    null_rate = (_float(left.data_profile.get("null_rate")) + _float(right.data_profile.get("null_rate"))) / 2
    return {
        "left_type": left.column_type,
        "right_type": right.column_type,
        "type_compatible": _type_compatible(left.column_type, right.column_type),
        "name_similarity": round(_name_similarity(left.column_name, right.column_name), 6),
        "cardinality_balance": round(min_distinct / max_distinct, 6),
        "left_distinct_count": left_distinct,
        "right_distinct_count": right_distinct,
        "average_null_rate": round(null_rate, 6),
    }


def _candidate_score(features: dict[str, Any]) -> float:
    overlap = _float(features.get("sample_overlap_rate"))
    name_similarity = _float(features.get("name_similarity"))
    cardinality_balance = _float(features.get("cardinality_balance"))
    null_score = max(0.0, 1.0 - _float(features.get("average_null_rate")))
    score = (0.45 * overlap) + (0.25 * name_similarity) + (0.2 * cardinality_balance) + (0.1 * null_score)
    return round(min(1.0, max(0.0, score)), 6)


def _type_compatible(left_type: str, right_type: str) -> bool:
    left = _type_family(left_type)
    right = _type_family(right_type)
    return bool(left and right and left == right)


def _type_family(value: str) -> str:
    clean = str(value or "").lower()
    if any(token in clean for token in ("int", "decimal", "double", "float", "real", "numeric", "number")):
        return "number"
    if any(token in clean for token in ("char", "text", "string", "varchar")):
        return "text"
    if "date" in clean or "time" in clean:
        return "temporal"
    if "bool" in clean:
        return "boolean"
    return ""


def _name_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalize_name(left), _normalize_name(right)).ratio()


def _normalize_name(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _edge_identity(left: str, right: str) -> tuple[str, str, str]:
    ordered = tuple(sorted((left, right)))
    return f"join:{ordered[0]}:{ordered[1]}", ordered[0], ordered[1]


def _valid_column(ref: str, table_columns: dict[str, list[str]]) -> bool:
    if "." not in ref:
        return False
    table, column = ref.split(".", 1)
    return column in table_columns.get(table, [])


def _table_columns_from_metadata(metadata: dict[str, Any]) -> dict[str, list[str]]:
    columns = metadata.get("columns", {})
    if not isinstance(columns, dict):
        return {}
    output = {}
    for table, table_columns in columns.items():
        if isinstance(table_columns, dict):
            output[str(table)] = sorted(str(column) for column in table_columns)
    return output


def _column_description(metadata: dict[str, Any], table: str, column: str) -> str:
    table_columns = metadata.get("columns", {}).get(table, {})
    if isinstance(table_columns, dict):
        return _description(table_columns.get(column))
    return ""


def _table_description(metadata: dict[str, Any], table: str) -> str:
    return _description(metadata.get("tables", {}).get(table))


def _description(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(
            str(value.get(key) or "")
            for key in ("short_description", "long_description", "description")
            if value.get(key)
        )
    return str(value or "")


def _classification_prompt() -> str:
    return """Classify SQL join relationships from evidence.

Return only valid JSON:
{
  "edges": [
    {
      "edge_id": "edge id from evidence",
      "relationship_type": "primary_foreign_key | dimensional_lookup | bridge | temporal_alignment | ambiguous | unknown",
      "grain_effect": "preserves_left_grain | preserves_right_grain | may_multiply_rows | filters_rows | unknown",
      "default_behavior": "short SQL guidance grounded in evidence",
      "review_status": "observed | needs_review",
      "rationale": "one sentence"
    }
  ]
}

Rules:
- Use only supplied evidence.
- Do not invent tables, columns, values, or constraints.
- Observed joins from trusted or historical SQL can be marked observed.
- Candidate-only joins should remain needs_review unless evidence is strong.
- Use left_column_evidence and right_column_evidence to judge whether names,
  descriptions, and profiles support the relationship.
- Numeric overlap alone is insufficient for a safe relationship classification.
- If competing edges exist for the same table pair, mention ambiguity in rationale.
- If grain effect is not clear from evidence, use unknown or may_multiply_rows.

EVIDENCE:
{{join_evidence_json}}
"""


def _json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        import re

        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            raise
        value = json.loads(raw[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("join classification response must be a JSON object")
    return value


def _generated_review_status(value: Any) -> str:
    clean = _one_line(value)
    if clean == "approved":
        return "observed"
    if clean in {"observed", "needs_review"}:
        return clean
    return "needs_review"


def _sorted_index(values: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: sorted(set(items)) for key, items in sorted(values.items())}


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(number) else number


def _batches(values: list[JoinRelationshipEdge], size: int) -> list[list[JoinRelationshipEdge]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
