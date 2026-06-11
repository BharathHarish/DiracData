"""Read active learned artifacts for answer-time tools."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import yaml

from diracdata.config.settings import DiracDataSettings
from diracdata.learning.paths import active_learning_artifact_key
from diracdata.storage.object_store import ObjectStore


class AgentArtifactError(RuntimeError):
    """Raised when required answer-time artifacts are unavailable or malformed."""


@dataclass(frozen=True)
class SearchHit:
    kind: str
    table_name: str
    column_name: str | None
    short_description: str
    score: float


class LearnedArtifactRepository:
    """Artifact-backed repository for the active learned context."""

    def __init__(self, *, settings: DiracDataSettings, object_store: ObjectStore) -> None:
        self.settings = settings
        self.object_store = object_store

    def preflight(self) -> dict[str, bool]:
        keys = {
            "metadata_descriptions": self._active_key("descriptions/metadata_descriptions.json"),
            "learned_context": self._active_key("contexts/learned_context.json"),
            "joinable_pairs": self._active_key("joins/joinable_pairs.jsonl"),
        }
        return {name: self.object_store.exists(key) for name, key in keys.items()}

    def load_metadata_descriptions(self) -> dict[str, Any]:
        payload = self._read_active_json("descriptions/metadata_descriptions.json")
        if not isinstance(payload, dict):
            raise AgentArtifactError("metadata descriptions artifact must be a JSON object")
        if not isinstance(payload.get("tables"), dict) or not isinstance(payload.get("columns"), dict):
            raise AgentArtifactError("metadata descriptions artifact must contain tables and columns")
        return payload

    def load_learned_context(self) -> dict[str, Any]:
        payload = self._read_active_json("contexts/learned_context.json")
        if not isinstance(payload, dict):
            raise AgentArtifactError("learned context artifact must be a JSON object")
        return payload

    def load_profile(self) -> dict[str, Any]:
        context = self.load_learned_context()
        profile_key = context.get("profile_artifact_key")
        if not isinstance(profile_key, str) or not profile_key:
            raise AgentArtifactError("learned context is missing profile_artifact_key")
        payload = self.object_store.read_json(profile_key)
        if not isinstance(payload, dict) or not isinstance(payload.get("tables"), list):
            raise AgentArtifactError("profile artifact must contain a tables list")
        return payload

    def load_joinable_pairs(self) -> list[dict[str, Any]]:
        active_key = self._active_key("joins/joinable_pairs.jsonl")
        if self.object_store.exists(active_key):
            return _parse_jsonl(self.object_store.read_text(active_key))

        context = self.load_learned_context()
        join_key = context.get("joinable_pairs_artifact_key")
        if isinstance(join_key, str) and join_key and self.object_store.exists(join_key):
            return _parse_jsonl(self.object_store.read_text(join_key))
        return []

    def load_query_library_patterns(self) -> list[dict[str, Any]]:
        sql_library = self.load_sql_library()
        if sql_library:
            return [row for row in sql_library if _as_dict(row.get("compact_contract"))]

        active_key = self._active_key("libraries/query_patterns.jsonl")
        if self.object_store.exists(active_key):
            return _parse_jsonl(self.object_store.read_text(active_key))

        context = self.load_learned_context()
        manifest_key = context.get("query_libraries_manifest_artifact_key")
        if not isinstance(manifest_key, str) or not manifest_key:
            return []
        if not self.object_store.exists(manifest_key):
            return []
        manifest = self.object_store.read_json(manifest_key)
        if not isinstance(manifest, dict):
            return []
        artifacts = manifest.get("canonical_artifacts")
        if not isinstance(artifacts, dict):
            return []
        pattern_key = artifacts.get("query_patterns_artifact_key")
        if isinstance(pattern_key, str) and self.object_store.exists(pattern_key):
            return _parse_jsonl(self.object_store.read_text(pattern_key))
        return []

    def load_sql_library(self) -> list[dict[str, Any]]:
        active_key = self._active_key("libraries/sql_library.yaml")
        if self.object_store.exists(active_key):
            payload = yaml.safe_load(self.object_store.read_text(active_key))
            return _list_from_yaml_payload(payload, "entries")

        context = self.load_learned_context()
        manifest_key = context.get("query_libraries_manifest_artifact_key")
        if not isinstance(manifest_key, str) or not manifest_key or not self.object_store.exists(manifest_key):
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
            return _list_from_yaml_payload(payload, "entries")
        return []

    def load_candidate_invariants(self) -> list[dict[str, Any]]:
        active_key = self._active_key("nuance/invariants.yaml")
        if self.object_store.exists(active_key):
            payload = yaml.safe_load(self.object_store.read_text(active_key))
            return _list_from_yaml_payload(payload, "invariants")

        manifest = self._load_optional_manifest_from_context("nuance_manifest_artifact_key")
        artifacts = manifest.get("canonical_artifacts") if isinstance(manifest, dict) else None
        key = artifacts.get("invariants_artifact_key") if isinstance(artifacts, dict) else None
        if isinstance(key, str) and self.object_store.exists(key):
            payload = yaml.safe_load(self.object_store.read_text(key))
            return _list_from_yaml_payload(payload, "invariants")
        return []

    def load_confounders(self) -> list[dict[str, Any]]:
        active_key = self._active_key("nuance/confounders.jsonl")
        if self.object_store.exists(active_key):
            return _parse_jsonl(self.object_store.read_text(active_key))

        manifest = self._load_optional_manifest_from_context("nuance_manifest_artifact_key")
        artifacts = manifest.get("canonical_artifacts") if isinstance(manifest, dict) else None
        key = artifacts.get("confounders_artifact_key") if isinstance(artifacts, dict) else None
        if isinstance(key, str) and self.object_store.exists(key):
            return _parse_jsonl(self.object_store.read_text(key))
        return []

    def _load_optional_manifest_from_context(self, field_name: str) -> dict[str, Any]:
        context = self.load_learned_context()
        manifest_key = context.get(field_name)
        if not isinstance(manifest_key, str) or not manifest_key:
            return {}
        if not self.object_store.exists(manifest_key):
            return {}
        manifest = self.object_store.read_json(manifest_key)
        return manifest if isinstance(manifest, dict) else {}

    def persist_active_joinable_pairs(
        self,
        pairs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge compact join pairs into the mutable active join artifact."""
        merged = _dedupe_join_pairs([*self.load_joinable_pairs(), *pairs])
        active_key = self._active_key("joins/joinable_pairs.jsonl")
        text = "".join(
            json.dumps(_compact_join_pair(pair), sort_keys=True) + "\n"
            for pair in merged
        )
        self.object_store.write_text(active_key, text, content_type="application/jsonl")
        return merged

    def search_descriptions(self, query: str, *, limit: int) -> list[SearchHit]:
        metadata = self.load_metadata_descriptions()
        tokens = _tokens(query)
        if not tokens:
            return []

        hits = []
        for table_name, description in metadata["tables"].items():
            text = _description_text(description)
            score = _score(tokens, table_name, text)
            if score > 0:
                hits.append(
                    SearchHit(
                        kind="table",
                        table_name=str(table_name),
                        column_name=None,
                        short_description=_short_description(description),
                        score=score,
                    )
                )

        for table_name, table_columns in metadata["columns"].items():
            if not isinstance(table_columns, dict):
                continue
            for column_name, description in table_columns.items():
                text = _description_text(description)
                score = _score(tokens, f"{table_name} {column_name}", text)
                if score > 0:
                    hits.append(
                        SearchHit(
                            kind="column",
                            table_name=str(table_name),
                            column_name=str(column_name),
                            short_description=_short_description(description),
                            score=score,
                        )
                    )

        return sorted(hits, key=lambda hit: (-hit.score, hit.table_name, hit.column_name or ""))[
            :limit
        ]

    def table_descriptions(self, table_name: str | None = None) -> dict[str, str]:
        metadata = self.load_metadata_descriptions()
        tables = metadata["tables"]
        if table_name:
            description = tables.get(table_name)
            if description is None:
                return {}
            return {table_name: _short_description(description)}
        return {
            str(name): _short_description(description)
            for name, description in sorted(tables.items())
        }

    def table_columns(self, table_name: str) -> dict[str, str]:
        metadata = self.load_metadata_descriptions()
        columns = metadata["columns"].get(table_name)
        if not isinstance(columns, dict):
            return {}
        return {
            str(column_name): _short_description(description)
            for column_name, description in sorted(columns.items())
        }

    def column_description(self, table_name: str, column_name: str) -> str | None:
        metadata = self.load_metadata_descriptions()
        columns = metadata["columns"].get(table_name)
        if not isinstance(columns, dict):
            return None
        description = columns.get(column_name)
        if description is None:
            return None
        return _short_description(description)

    def profile_column_values(
        self,
        *,
        table_name: str,
        column_name: str,
        limit: int,
    ) -> dict[str, Any] | None:
        profile = self.load_profile()
        for table in profile["tables"]:
            if not isinstance(table, dict) or table.get("table_name") != table_name:
                continue
            for column in table.get("columns", []):
                if not isinstance(column, dict) or column.get("column_name") != column_name:
                    continue
                return {
                    "table_name": table_name,
                    "column_name": column_name,
                    "data_type": column.get("data_type"),
                    "null_rate": column.get("null_rate"),
                    "distinct_count": column.get("distinct_count"),
                    "min_value": column.get("min_value"),
                    "max_value": column.get("max_value"),
                    "top_values": _limit_list(column.get("top_values"), limit),
                    "distinct_values": _limit_list(column.get("distinct_values"), limit),
                }
        return None

    def _read_active_json(self, relative_path: str) -> object:
        key = self._active_key(relative_path)
        if not self.object_store.exists(key):
            raise AgentArtifactError(f"Required active learning artifact is missing: {key}")
        return self.object_store.read_json(key)

    def _active_key(self, relative_path: str) -> str:
        return active_learning_artifact_key(self.settings, relative_path=relative_path)


def _parse_jsonl(text: str) -> list[dict[str, Any]]:
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _list_from_yaml_payload(payload: object, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dedupe_join_pairs(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[tuple[str, str], tuple[str, str]], dict[str, Any]] = {}
    for pair in pairs:
        compact = _compact_join_pair(pair)
        key = _join_pair_key(compact)
        if key not in deduped or _confidence_rank(compact) > _confidence_rank(deduped[key]):
            deduped[key] = compact
    return sorted(
        deduped.values(),
        key=lambda item: (
            str(item["left_table"]),
            str(item["left_column"]),
            str(item["right_table"]),
            str(item["right_column"]),
        ),
    )


def _compact_join_pair(pair: dict[str, Any]) -> dict[str, Any]:
    return {
        "left_table": str(pair.get("left_table", "")),
        "left_column": str(pair.get("left_column", "")),
        "right_table": str(pair.get("right_table", "")),
        "right_column": str(pair.get("right_column", "")),
        "join_type": str(pair.get("join_type", "many_to_many")),
        "confidence": str(pair.get("confidence", "low")),
    }


def _join_pair_key(pair: dict[str, Any]) -> tuple[tuple[str, str], tuple[str, str]]:
    left = (str(pair["left_table"]), str(pair["left_column"]))
    right = (str(pair["right_table"]), str(pair["right_column"]))
    first, second = sorted([left, right])
    return first, second


def _confidence_rank(pair: dict[str, Any]) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(str(pair.get("confidence")), 0)


def _description_text(description: object) -> str:
    if not isinstance(description, dict):
        return ""
    return " ".join(
        str(description.get(key, ""))
        for key in ["short_description", "long_description"]
    )


def _short_description(description: object) -> str:
    if not isinstance(description, dict):
        return ""
    value = description.get("short_description", "")
    return str(value).strip()


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 1}


def _score(query_tokens: set[str], name: str, description: str) -> float:
    name_tokens = _tokens(name.replace("_", " "))
    description_tokens = _tokens(description)
    exact_name_hits = len(query_tokens & name_tokens)
    description_hits = len(query_tokens & description_tokens)
    if exact_name_hits == 0 and description_hits == 0:
        return 0.0
    return (exact_name_hits * 3.0) + description_hits


def _limit_list(value: object, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value[:limit]
