"""Generate nuance, invariant, and analyst-review artifacts from learned context."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml

from diracdata.config.settings import DiracDataSettings
from diracdata.learning.models import ColumnProfile, LearningCollection, to_jsonable
from diracdata.learning.paths import active_learning_artifact_key, learning_artifact_key
from diracdata.storage.object_store import ObjectStore


@dataclass(frozen=True)
class NuanceBuildResult:
    """Artifact keys and counts from nuance/invariant generation."""

    run_id: str
    manifest_artifact_key: str
    active_manifest_artifact_key: str
    null_semantics_artifact_key: str
    confounders_artifact_key: str
    invariants_artifact_key: str
    analyst_questions_artifact_key: str
    review_pack_artifact_key: str
    null_candidate_count: int
    confounder_count: int
    invariant_count: int
    analyst_question_count: int


class NuanceArtifactBuilder:
    """Build reviewable semantic nuance artifacts for a scoped pod."""

    def __init__(
        self,
        *,
        settings: DiracDataSettings,
        object_store: ObjectStore,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.object_store = object_store
        self.progress_callback = progress_callback

    def build(
        self,
        *,
        collection: LearningCollection,
        business_grounding: dict[str, Any] | None = None,
        query_libraries_manifest_artifact_key: str | None = None,
    ) -> NuanceBuildResult:
        self._emit("nuance: build null semantics, confounders, invariants, and questions")
        library_patterns = self._load_library_patterns(query_libraries_manifest_artifact_key)
        null_candidates = _null_semantics_candidates(collection)
        confounders = _confounders(collection)
        invariants = _invariants(
            collection=collection,
            business_grounding=business_grounding or {},
            library_patterns=library_patterns,
            confounders=confounders,
        )
        questions = _analyst_questions(
            null_candidates=null_candidates,
            confounders=confounders,
            invariants=invariants,
        )
        review_pack = _review_pack(
            collection=collection,
            null_candidates=null_candidates,
            confounders=confounders,
            invariants=invariants,
            questions=questions,
        )

        keys = _artifact_keys(self.settings, collection.run_id)
        self._write_yaml_pair(
            keys["null_semantics"],
            keys["active_null_semantics"],
            {
                "version": 1,
                "artifact_type": "null_semantics_candidates",
                "candidates": null_candidates,
            },
        )
        self._write_jsonl_pair(keys["confounders"], keys["active_confounders"], confounders)
        self._write_yaml_pair(
            keys["invariants"],
            keys["active_invariants"],
            {
                "version": 1,
                "artifact_type": "candidate_invariants",
                "invariants": invariants,
            },
        )
        self._write_yaml_pair(
            keys["analyst_questions"],
            keys["active_analyst_questions"],
            {
                "version": 1,
                "artifact_type": "analyst_learning_questions",
                "questions": questions,
            },
        )
        self.object_store.write_text(keys["review_pack"], review_pack, content_type="text/markdown")
        self.object_store.write_text(
            keys["active_review_pack"],
            review_pack,
            content_type="text/markdown",
        )

        manifest = {
            "artifact_type": "nuance_invariants",
            "catalog": collection.scope.catalog,
            "database": collection.scope.database,
            "schema": collection.scope.schema,
            "run_id": collection.run_id,
            "built_at": datetime.now(UTC).isoformat(),
            "null_candidate_count": len(null_candidates),
            "confounder_count": len(confounders),
            "invariant_count": len(invariants),
            "analyst_question_count": len(questions),
            "canonical_artifacts": {
                "null_semantics_artifact_key": keys["null_semantics"],
                "confounders_artifact_key": keys["confounders"],
                "invariants_artifact_key": keys["invariants"],
                "analyst_questions_artifact_key": keys["analyst_questions"],
                "review_pack_artifact_key": keys["review_pack"],
            },
            "active_artifacts": {
                "null_semantics_artifact_key": keys["active_null_semantics"],
                "confounders_artifact_key": keys["active_confounders"],
                "invariants_artifact_key": keys["active_invariants"],
                "analyst_questions_artifact_key": keys["active_analyst_questions"],
                "review_pack_artifact_key": keys["active_review_pack"],
            },
        }
        self.object_store.write_json(keys["manifest"], manifest)
        self.object_store.write_json(keys["active_manifest"], manifest)
        self._update_active_manifest(keys=keys, manifest=manifest)

        return NuanceBuildResult(
            run_id=collection.run_id,
            manifest_artifact_key=keys["manifest"],
            active_manifest_artifact_key=keys["active_manifest"],
            null_semantics_artifact_key=keys["null_semantics"],
            confounders_artifact_key=keys["confounders"],
            invariants_artifact_key=keys["invariants"],
            analyst_questions_artifact_key=keys["analyst_questions"],
            review_pack_artifact_key=keys["review_pack"],
            null_candidate_count=len(null_candidates),
            confounder_count=len(confounders),
            invariant_count=len(invariants),
            analyst_question_count=len(questions),
        )

    def _load_library_patterns(self, manifest_key: str | None) -> list[dict[str, Any]]:
        if not manifest_key or not self.object_store.exists(manifest_key):
            return []
        manifest = self.object_store.read_json(manifest_key)
        if not isinstance(manifest, dict):
            return []
        artifacts = manifest.get("canonical_artifacts")
        if not isinstance(artifacts, dict):
            return []
        sql_library_key = artifacts.get("sql_library_artifact_key")
        if isinstance(sql_library_key, str) and self.object_store.exists(sql_library_key):
            payload = yaml.safe_load(self.object_store.read_text(sql_library_key))
            if isinstance(payload, dict):
                entries = payload.get("entries")
                if isinstance(entries, list):
                    return [
                        item
                        for item in entries
                        if isinstance(item, dict) and isinstance(item.get("compact_contract"), dict)
                    ]
        pattern_key = artifacts.get("query_patterns_artifact_key")
        if not isinstance(pattern_key, str) or not self.object_store.exists(pattern_key):
            return []
        return [
            json.loads(line)
            for line in self.object_store.read_text(pattern_key).splitlines()
            if line.strip()
        ]

    def _write_jsonl_pair(
        self,
        immutable_key: str,
        active_key: str,
        rows: list[dict[str, Any]],
    ) -> None:
        payload = "".join(json.dumps(to_jsonable(row), sort_keys=True) + "\n" for row in rows)
        self.object_store.write_text(immutable_key, payload, content_type="application/jsonl")
        self.object_store.write_text(active_key, payload, content_type="application/jsonl")

    def _write_yaml_pair(self, immutable_key: str, active_key: str, payload: dict[str, Any]) -> None:
        text = yaml.safe_dump(to_jsonable(payload), sort_keys=False, allow_unicode=False)
        self.object_store.write_text(immutable_key, text, content_type="application/yaml")
        self.object_store.write_text(active_key, text, content_type="application/yaml")

    def _update_active_manifest(self, *, keys: dict[str, str], manifest: dict[str, Any]) -> None:
        active_manifest_key = active_learning_artifact_key(self.settings, relative_path="manifest.json")
        if not self.object_store.exists(active_manifest_key):
            return
        active_manifest = self.object_store.read_json(active_manifest_key)
        if not isinstance(active_manifest, dict):
            return
        active_manifest.setdefault("immutable_artifacts", {})["nuance_manifest_artifact_key"] = (
            keys["manifest"]
        )
        active_manifest.setdefault("active_artifacts", {})["nuance_manifest_artifact_key"] = (
            keys["active_manifest"]
        )
        active_manifest["nuance"] = {
            "null_candidate_count": manifest["null_candidate_count"],
            "confounder_count": manifest["confounder_count"],
            "invariant_count": manifest["invariant_count"],
            "analyst_question_count": manifest["analyst_question_count"],
        }
        self.object_store.write_json(active_manifest_key, active_manifest)

    def _emit(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)


def _null_semantics_candidates(collection: LearningCollection) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for column in _columns(collection):
        if column.null_count in {None, 0} and not column.null_rate:
            continue
        candidates.append(
            {
                "id": f"null_semantics:{column.table_name}.{column.column_name}",
                "table_name": column.table_name,
                "column_name": column.column_name,
                "data_type": column.data_type,
                "null_count": column.null_count,
                "null_rate": column.null_rate,
                "distinct_count": column.distinct_count,
                "sample_non_null_values": _sample_values(column),
                "interpretation_candidates": [
                    "unknown value",
                    "not applicable for this row",
                    "data quality or ingestion gap",
                    "not yet populated at this lifecycle stage",
                ],
                "recommended_runtime_default": (
                    "Do not silently drop NULLs. Preserve or label them unless a governed rule says otherwise."
                ),
                "confidence": "needs_review",
            }
        )
    return sorted(candidates, key=lambda item: (-float(item.get("null_rate") or 0.0), item["id"]))


def _confounders(collection: LearningCollection) -> list[dict[str, Any]]:
    by_name: dict[str, list[ColumnProfile]] = defaultdict(list)
    columns = _columns(collection)
    for column in columns:
        by_name[column.column_name.lower()].append(column)

    rows: list[dict[str, Any]] = []
    for name, group in sorted(by_name.items()):
        if len(group) < 2:
            continue
        rows.append(
            {
                "id": f"confounder:exact_column_name:{name}",
                "artifact_type": "confounder",
                "confounder_type": "exact_column_name",
                "term": name,
                "columns": [_column_ref(column) for column in group],
                "reason": "Same column name appears in multiple tables and may represent different entities or grains.",
                "resolution_policy": "Resolve using the entity in the user intent, table description, metric grain, and approved patterns.",
                "confidence": "medium",
            }
        )

    similar_pairs = []
    for index, left in enumerate(columns):
        for right in columns[index + 1 :]:
            if left.table_name == right.table_name:
                continue
            if left.column_name.lower() == right.column_name.lower():
                continue
            score = SequenceMatcher(None, left.column_name.lower(), right.column_name.lower()).ratio()
            if score < 0.82:
                continue
            similar_pairs.append((score, left, right))

    for score, left, right in sorted(similar_pairs, key=lambda item: (-item[0], _column_ref(item[1]))):
        rows.append(
            {
                "id": f"confounder:similar_column_name:{_column_ref(left)}:{_column_ref(right)}",
                "artifact_type": "confounder",
                "confounder_type": "similar_column_name",
                "columns": [_column_ref(left), _column_ref(right)],
                "similarity": round(score, 3),
                "reason": "Similar column names may be semantically close but not interchangeable.",
                "resolution_policy": "Ask or use governed patterns when the intent does not identify the entity.",
                "confidence": "low",
            }
        )
    return rows


def _invariants(
    *,
    collection: LearningCollection,
    business_grounding: dict[str, Any],
    library_patterns: list[dict[str, Any]],
    confounders: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    invariants: list[dict[str, Any]] = []
    invariants.extend(_business_grounding_invariants(business_grounding))
    invariants.extend(_library_join_invariants(library_patterns))
    invariants.extend(_confounder_invariants(confounders))
    invariants.extend(_time_column_invariants(collection, business_grounding))
    return _dedupe_by_id(invariants)


def _business_grounding_invariants(grounding: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _as_list(grounding.get("defaults")):
        if not isinstance(item, dict):
            continue
        invariant_id = str(item.get("id") or f"default:{len(rows) + 1}")
        rows.append(
            {
                "id": f"invariant:grounding:{invariant_id}",
                "invariant_type": "business_default",
                "rule": item.get("policy"),
                "applies_to": item.get("applies_to") or [],
                "columns": [item.get("field")] if item.get("field") else [],
                "source": "business_grounding",
                "confidence": "high",
                "approval_status": "candidate",
            }
        )
    for metric in _as_list(grounding.get("metrics")):
        if not isinstance(metric, dict):
            continue
        sql_contract = _nested_dict(metric, "parameterized_sql", "sql_contract")
        if not sql_contract:
            continue
        metric_id = str(metric.get("id") or metric.get("name") or "metric")
        rows.append(
            {
                "id": f"invariant:metric_contract:{metric_id}",
                "invariant_type": "metric_contract",
                "metric_id": metric_id,
                "rule": "Use the governed metric SQL contract when this metric is requested.",
                "contract": sql_contract,
                "source": "business_grounding",
                "confidence": "high",
                "approval_status": "candidate",
            }
        )
    return rows


def _library_join_invariants(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pattern in patterns:
        compact = pattern.get("compact_contract")
        if not isinstance(compact, dict):
            continue
        required = [str(value) for value in _as_list(compact.get("required_joins"))]
        avoided = [str(value) for value in _as_list(compact.get("avoid_joins"))]
        if not required and not avoided:
            continue
        rows.append(
            {
                "id": f"invariant:library_join:{pattern.get('id')}",
                "invariant_type": "join_pattern",
                "rule": "Preserve the mined join pattern unless the user intent explicitly changes the grain.",
                "required_joins": required,
                "avoid_joins": avoided,
                "fact_table": compact.get("fact_table"),
                "metrics": compact.get("metrics") or [],
                "source": "query_history_library",
                "support_count": pattern.get("query_count"),
                "confidence": "medium" if avoided else "low",
                "approval_status": "candidate",
            }
        )
    return rows


def _confounder_invariants(confounders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for confounder in confounders:
        rows.append(
            {
                "id": f"invariant:{confounder['id']}",
                "invariant_type": "confounder_resolution",
                "rule": "Do not choose among confounded columns by name alone.",
                "columns": confounder.get("columns") or [],
                "source": "schema_profile",
                "confidence": confounder.get("confidence", "low"),
                "approval_status": "candidate",
            }
        )
    return rows


def _time_column_invariants(
    collection: LearningCollection,
    grounding: dict[str, Any],
) -> list[dict[str, Any]]:
    time_columns = [
        _column_ref(column)
        for column in _columns(collection)
        if "time" in column.column_name.lower() or "date" in column.column_name.lower()
    ]
    governed_time_columns = set()
    for metric in _as_list(grounding.get("metrics")):
        if not isinstance(metric, dict):
            continue
        contract = _nested_dict(metric, "parameterized_sql", "sql_contract")
        time_column = contract.get("time_column") if isinstance(contract, dict) else None
        if isinstance(time_column, str):
            governed_time_columns.add(time_column)
    if len(time_columns) < 2:
        return []
    return [
        {
            "id": "invariant:time_column_disambiguation",
            "invariant_type": "time_semantics",
            "rule": "When multiple time columns exist, bind date filters using the metric or entity being measured.",
            "time_columns": sorted(time_columns),
            "governed_time_columns": sorted(governed_time_columns),
            "source": "schema_profile_and_business_grounding",
            "confidence": "medium" if governed_time_columns else "needs_review",
            "approval_status": "candidate",
        }
    ]


def _analyst_questions(
    *,
    null_candidates: list[dict[str, Any]],
    confounders: list[dict[str, Any]],
    invariants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for candidate in null_candidates[:25]:
        column_ref = f"{candidate['table_name']}.{candidate['column_name']}"
        questions.append(
            {
                "id": f"question:null_semantics:{column_ref}",
                "question_type": "null_semantics",
                "question": f"When {column_ref} is NULL, what should the agent assume?",
                "column_ref": column_ref,
                "options": candidate["interpretation_candidates"],
                "recommended_default": candidate["recommended_runtime_default"],
                "evidence": {
                    "null_rate": candidate.get("null_rate"),
                    "sample_non_null_values": candidate.get("sample_non_null_values"),
                },
            }
        )
    for confounder in confounders[:25]:
        questions.append(
            {
                "id": f"question:confounder:{confounder['id']}",
                "question_type": "confounder_resolution",
                "question": "How should the agent resolve this confounded column/entity set?",
                "columns": confounder.get("columns") or [],
                "reason": confounder.get("reason"),
                "recommended_default": confounder.get("resolution_policy"),
            }
        )
    for invariant in invariants[:30]:
        questions.append(
            {
                "id": f"question:invariant_review:{invariant['id']}",
                "question_type": "invariant_review",
                "question": "Should this candidate invariant be approved, edited, or rejected?",
                "invariant_id": invariant["id"],
                "rule": invariant.get("rule"),
                "source": invariant.get("source"),
                "confidence": invariant.get("confidence"),
            }
        )
    return questions


def _review_pack(
    *,
    collection: LearningCollection,
    null_candidates: list[dict[str, Any]],
    confounders: list[dict[str, Any]],
    invariants: list[dict[str, Any]],
    questions: list[dict[str, Any]],
) -> str:
    lines = [
        f"# Learning Review Pack: {collection.scope.catalog}.{collection.scope.database}.{collection.scope.schema}",
        "",
        "## Summary",
        "",
        f"- NULL semantics candidates: {len(null_candidates)}",
        f"- Confounders: {len(confounders)}",
        f"- Candidate invariants: {len(invariants)}",
        f"- Analyst questions: {len(questions)}",
        "",
        "## Highest Priority Questions",
        "",
    ]
    for question in questions[:15]:
        lines.append(f"- {question['id']}: {question['question']}")
    lines.extend(["", "## Candidate Invariants", ""])
    for invariant in invariants[:20]:
        lines.append(f"- {invariant['id']}: {invariant.get('rule')}")
    return "\n".join(lines) + "\n"


def _columns(collection: LearningCollection) -> list[ColumnProfile]:
    return [column for table in collection.table_profiles for column in table.columns]


def _column_ref(column: ColumnProfile) -> str:
    return f"{column.table_name}.{column.column_name}"


def _sample_values(column: ColumnProfile) -> list[Any]:
    values = []
    for item in column.top_values:
        if isinstance(item, dict) and "value" in item:
            values.append(item["value"])
    if not values:
        values.extend(column.distinct_values[:5])
    return values[:5]


def _nested_dict(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return {}
        value = value.get(key)
    return value if isinstance(value, dict) else {}


def _dedupe_by_id(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = {str(row["id"]): row for row in rows if row.get("id")}
    return [deduped[key] for key in sorted(deduped)]


def _artifact_keys(settings: DiracDataSettings, run_id: str) -> dict[str, str]:
    relative_paths = {
        "manifest": "nuance/manifest.json",
        "null_semantics": "nuance/null_semantics_candidates.yaml",
        "confounders": "nuance/confounders.jsonl",
        "invariants": "nuance/invariants.yaml",
        "analyst_questions": "nuance/analyst_questions.yaml",
        "review_pack": "nuance/learning_review_pack.md",
    }
    keys = {
        name: learning_artifact_key(settings, run_id=run_id, relative_path=path)
        for name, path in relative_paths.items()
    }
    keys.update(
        {
            f"active_{name}": active_learning_artifact_key(settings, relative_path=path)
            for name, path in relative_paths.items()
        }
    )
    return keys


def _as_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]
