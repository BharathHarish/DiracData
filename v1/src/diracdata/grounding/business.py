"""Business grounding artifacts supplied by customers or pod owners."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from diracdata.agents.artifacts import AgentArtifactError, LearnedArtifactRepository
from diracdata.config.settings import DiracDataSettings
from diracdata.learning.paths import active_learning_artifact_key
from diracdata.query_engines.base import QueryEngine
from diracdata.storage.object_store import ObjectStore
from diracdata.tools.sql_tools import validate_read_only_sql


GROUNDING_YAML_RELATIVE_PATH = "grounding/business_grounding.yaml"
GROUNDING_JSON_RELATIVE_PATH = "grounding/business_grounding.json"
LIST_SECTIONS = (
    "glossary",
    "definitions",
    "defaults",
    "metrics",
    "sql_templates",
    "ground_truth_sql",
)

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


class BusinessGroundingError(RuntimeError):
    """Raised when business grounding cannot be loaded, validated, or published."""


@dataclass(frozen=True)
class BusinessGroundingValidation:
    """Validated business grounding ready to publish as active artifacts."""

    normalized: dict[str, Any]
    yaml_text: str
    warnings: list[str]
    yaml_key: str
    json_key: str


class BusinessGroundingRepository:
    """Artifact-backed repository for active business definitions and SQL patterns."""

    def __init__(self, *, settings: DiracDataSettings, object_store: ObjectStore) -> None:
        self.settings = settings
        self.object_store = object_store

    def exists(self) -> bool:
        return self.object_store.exists(self._active_key(GROUNDING_JSON_RELATIVE_PATH))

    def load(self) -> dict[str, Any]:
        key = self._active_key(GROUNDING_JSON_RELATIVE_PATH)
        if not self.object_store.exists(key):
            raise BusinessGroundingError(f"Business grounding artifact is missing: {key}")
        payload = self.object_store.read_json(key)
        if not isinstance(payload, dict):
            raise BusinessGroundingError("Business grounding artifact must be a JSON object")
        return payload

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        payload = self.load()
        query_tokens = _tokens(query)
        if not query_tokens:
            return []

        hits: list[dict[str, Any]] = []
        for section in LIST_SECTIONS:
            for item in _as_list(payload.get(section)):
                if not isinstance(item, dict):
                    continue
                text = _search_text(item)
                score = _score(query_tokens, text)
                if score <= 0:
                    continue
                hits.append(
                    {
                        "section": section,
                        "id": item.get("id"),
                        "name": item.get("term") or item.get("name") or item.get("question"),
                        "description": _compact_description(item),
                        "score": score,
                    }
                )
        return sorted(hits, key=lambda row: (-float(row["score"]), str(row["id"])))[:limit]

    def resolve_business_intent(self, query: str, *, limit: int) -> dict[str, Any]:
        """Resolve typed, binding grounding from a user question.

        This is intentionally stricter than ``search``. Search returns candidates;
        this method activates only exact aliases for typed sections so unrelated
        glossary or template hits cannot become SQL contracts.
        """
        payload = self.load()
        activated = {
            "metrics": _resolve_alias_section(payload, "metrics", query),
            "defaults": _resolve_alias_section(payload, "defaults", query),
            "definitions": [
                *_resolve_alias_section(payload, "glossary", query),
                *_resolve_alias_section(payload, "definitions", query),
            ],
            "sql_templates": _resolve_alias_section(payload, "sql_templates", query),
        }
        return {
            "query": query,
            "activated": activated,
            "candidates": self.search(query, limit=limit),
        }

    def get_definition(self, id_or_term: str) -> dict[str, Any] | None:
        return self._find_in_sections(id_or_term, ("glossary", "definitions", "defaults"))

    def get_metric(self, metric_id: str) -> dict[str, Any] | None:
        return self._find_in_sections(metric_id, ("metrics",))

    def get_sql_template(self, template_id: str) -> dict[str, Any] | None:
        return self._find_in_sections(template_id, ("sql_templates",))

    def get_default_policy(self, id_or_term: str) -> dict[str, Any] | None:
        return self._find_in_sections(id_or_term, ("defaults",))

    def _find_in_sections(
        self,
        id_or_term: str,
        sections: tuple[str, ...],
    ) -> dict[str, Any] | None:
        needle = _normalize_lookup(id_or_term)
        if not needle:
            return None
        payload = self.load()
        for section in sections:
            for item in _as_list(payload.get(section)):
                if not isinstance(item, dict):
                    continue
                aliases = _lookup_aliases(item)
                if needle in aliases:
                    return {"section": section, **item}
        return None

    def _active_key(self, relative_path: str) -> str:
        return active_learning_artifact_key(self.settings, relative_path=relative_path)


def publish_business_grounding(
    *,
    settings: DiracDataSettings,
    object_store: ObjectStore,
    source_path: str | Path,
    learned_repository: LearnedArtifactRepository | None = None,
    query_engine: QueryEngine | None = None,
    validate_ground_truth: bool = True,
) -> BusinessGroundingValidation:
    """Validate and publish business grounding YAML into active learning artifacts."""
    validation = validate_business_grounding(
        settings=settings,
        object_store=object_store,
        source_path=source_path,
        learned_repository=learned_repository,
        query_engine=query_engine,
        validate_ground_truth=validate_ground_truth,
    )
    object_store.write_text(
        validation.yaml_key,
        validation.yaml_text,
        content_type="application/x-yaml",
    )
    object_store.write_json(validation.json_key, validation.normalized)
    return validation


def validate_business_grounding(
    *,
    settings: DiracDataSettings,
    object_store: ObjectStore,
    source_path: str | Path,
    learned_repository: LearnedArtifactRepository | None = None,
    query_engine: QueryEngine | None = None,
    validate_ground_truth: bool = True,
) -> BusinessGroundingValidation:
    source = Path(source_path)
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise BusinessGroundingError(f"Business grounding YAML is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise BusinessGroundingError("Business grounding YAML must contain a top-level object")

    normalized = _normalize_payload(payload)
    warnings: list[str] = []
    _validate_scope(settings, normalized)

    repository = learned_repository or LearnedArtifactRepository(
        settings=settings,
        object_store=object_store,
    )
    tables, columns = _profile_schema(repository=repository, query_engine=query_engine)
    _validate_unique_ids(normalized)
    _validate_references(normalized, tables=tables, columns=columns)
    if validate_ground_truth:
        warnings.extend(
            _validate_ground_truth_sql(
                normalized,
                settings=settings,
                query_engine=query_engine,
                tables=tables,
            )
        )

    normalized["scope"] = {
        "catalog": settings.catalog,
        "database": settings.database,
        "schema": settings.schema,
    }
    normalized["artifact_type"] = "business_grounding"
    normalized["validation"] = {
        "status": "ok",
        "warnings": warnings,
    }

    yaml_text = yaml.safe_dump(
        normalized,
        sort_keys=False,
        allow_unicode=False,
    )
    return BusinessGroundingValidation(
        normalized=normalized,
        yaml_text=yaml_text,
        warnings=warnings,
        yaml_key=active_learning_artifact_key(
            settings,
            relative_path=GROUNDING_YAML_RELATIVE_PATH,
        ),
        json_key=active_learning_artifact_key(
            settings,
            relative_path=GROUNDING_JSON_RELATIVE_PATH,
        ),
    )


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for section in LIST_SECTIONS:
        value = normalized.get(section)
        if value is None:
            normalized[section] = []
        elif isinstance(value, list):
            normalized[section] = value
        else:
            raise BusinessGroundingError(f"{section} must be a list")
    if "scope" not in normalized:
        normalized["scope"] = {}
    if not isinstance(normalized["scope"], dict):
        raise BusinessGroundingError("scope must be an object")
    return normalized


def _validate_scope(settings: DiracDataSettings, payload: dict[str, Any]) -> None:
    scope = payload.get("scope", {})
    for field, expected in [
        ("catalog", settings.catalog),
        ("database", settings.database),
        ("schema", settings.schema),
    ]:
        actual = scope.get(field)
        if actual is not None and str(actual) != expected:
            raise BusinessGroundingError(
                f"scope.{field}={actual!r} does not match active setting {expected!r}"
            )


def _profile_schema(
    *,
    repository: LearnedArtifactRepository,
    query_engine: QueryEngine | None,
) -> tuple[set[str], dict[str, set[str]]]:
    tables: set[str] = set()
    columns: dict[str, set[str]] = {}
    try:
        profile = repository.load_profile()
    except AgentArtifactError:
        profile = {"tables": []}

    for table in _as_list(profile.get("tables") if isinstance(profile, dict) else None):
        if not isinstance(table, dict):
            continue
        table_name = table.get("table_name")
        if not isinstance(table_name, str) or not table_name:
            continue
        tables.add(table_name)
        columns.setdefault(table_name, set())
        for column in _as_list(table.get("columns")):
            if isinstance(column, dict) and isinstance(column.get("column_name"), str):
                columns[table_name].add(column["column_name"])

    if query_engine is not None:
        for table_name in query_engine.list_tables():
            tables.add(table_name)
            column_names = columns.setdefault(table_name, set())
            for column in query_engine.describe_table(table_name):
                column_names.add(column.name)

    if not tables:
        raise BusinessGroundingError(
            "Cannot validate business grounding without learned profile or query-engine schema"
        )
    return tables, columns


def _validate_unique_ids(payload: dict[str, Any]) -> None:
    for section in LIST_SECTIONS:
        seen: set[str] = set()
        for item in _as_list(payload.get(section)):
            if not isinstance(item, dict):
                raise BusinessGroundingError(f"{section} entries must be objects")
            item_id = str(item.get("id", "")).strip()
            if not item_id:
                raise BusinessGroundingError(f"{section} entries must include id")
            if item_id in seen:
                raise BusinessGroundingError(f"Duplicate id in {section}: {item_id}")
            seen.add(item_id)


def _validate_references(
    payload: dict[str, Any],
    *,
    tables: set[str],
    columns: dict[str, set[str]],
) -> None:
    for section in ("glossary", "definitions", "metrics", "sql_templates"):
        for item in _as_list(payload.get(section)):
            if not isinstance(item, dict):
                continue
            _validate_table_refs(item, item.get("tables"), tables=tables, section=section)
            _validate_table_refs(
                item,
                item.get("required_tables"),
                tables=tables,
                section=section,
            )
            _validate_table_refs(
                item,
                item.get("optional_tables"),
                tables=tables,
                section=section,
            )
            _validate_column_refs(
                item,
                item.get("columns"),
                columns=columns,
                section=section,
            )
            _validate_column_refs(
                item,
                item.get("filters"),
                columns=columns,
                section=section,
            )
            _validate_join_refs(item, item.get("join_path"), columns=columns, section=section)

    for item in _as_list(payload.get("defaults")):
        if isinstance(item, dict):
            _validate_column_refs(item, [item.get("field")], columns=columns, section="defaults")
            _validate_column_refs(
                item,
                item.get("alternatives"),
                columns=columns,
                section="defaults",
            )

    for item in _as_list(payload.get("ground_truth_sql")):
        if isinstance(item, dict):
            _validate_table_refs(
                item,
                item.get("tables"),
                tables=tables,
                section="ground_truth_sql",
            )


def _validate_table_refs(
    item: dict[str, Any],
    refs: object,
    *,
    tables: set[str],
    section: str,
) -> None:
    for ref in _ref_values(refs):
        if ref not in tables:
            raise BusinessGroundingError(
                f"{section}.{item.get('id')} references unknown table {ref!r}"
            )


def _validate_column_refs(
    item: dict[str, Any],
    refs: object,
    *,
    columns: dict[str, set[str]],
    section: str,
) -> None:
    for ref in _ref_values(refs):
        if "." not in ref:
            raise BusinessGroundingError(
                f"{section}.{item.get('id')} column reference must be table.column: {ref!r}"
            )
        table_name, column_name = ref.split(".", 1)
        if column_name not in columns.get(table_name, set()):
            raise BusinessGroundingError(
                f"{section}.{item.get('id')} references unknown column {ref!r}"
            )


def _validate_join_refs(
    item: dict[str, Any],
    refs: object,
    *,
    columns: dict[str, set[str]],
    section: str,
) -> None:
    for edge in _as_list(refs):
        if not isinstance(edge, (list, tuple)) or len(edge) != 2:
            raise BusinessGroundingError(
                f"{section}.{item.get('id')} join_path entries must contain two column refs"
            )
        _validate_column_refs(item, edge, columns=columns, section=section)


def _validate_ground_truth_sql(
    payload: dict[str, Any],
    *,
    settings: DiracDataSettings,
    query_engine: QueryEngine | None,
    tables: set[str],
) -> list[str]:
    warnings: list[str] = []
    for item in _as_list(payload.get("ground_truth_sql")):
        if not isinstance(item, dict):
            continue
        sql = item.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            raise BusinessGroundingError(f"ground_truth_sql.{item.get('id')} is missing sql")
        validation = validate_read_only_sql(
            sql,
            available_tables=tables,
            sql_dialect=settings.sql_dialect,
        )
        if validation["status"] != "ok":
            raise BusinessGroundingError(
                f"ground_truth_sql.{item.get('id')} failed SQL validation: "
                f"{validation.get('error')}"
            )
        if query_engine is None:
            warnings.append(f"ground_truth_sql.{item.get('id')} was not executed")
            continue
        result = query_engine.query(sql, max_rows=2)
        if not result.rows:
            raise BusinessGroundingError(f"ground_truth_sql.{item.get('id')} returned no rows")
        expected = _expected_value(item.get("expected_answer"))
        if expected is not None:
            actual = result.rows[0][0] if result.rows[0] else None
            if not _values_equal(actual, expected):
                raise BusinessGroundingError(
                    f"ground_truth_sql.{item.get('id')} expected {expected!r}, got {actual!r}"
                )
    return warnings


def _expected_value(value: object) -> object | None:
    if isinstance(value, dict):
        return value.get("value")
    return value


def _values_equal(actual: object, expected: object) -> bool:
    for left, right in [(actual, expected), (expected, actual)]:
        try:
            return float(left) == float(right)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            pass
    return str(actual) == str(expected)


def _ref_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        field = value.get("field") or value.get("column") or value.get("table")
        return [str(field)] if field else []
    if isinstance(value, list):
        refs: list[str] = []
        for item in value:
            refs.extend(_ref_values(item))
        return refs
    return []


def _as_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return []


def _search_text(item: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "id",
        "term",
        "name",
        "question",
        "definition",
        "description",
        "policy",
        "calculation",
        "meaning",
    ):
        value = item.get(key)
        if value is not None:
            values.append(str(value))
    for key in ("synonyms", "applies_to", "tables", "columns"):
        values.extend(str(value) for value in _ref_values(item.get(key)))
    return " ".join(values)


def _compact_description(item: dict[str, Any]) -> str:
    for key in ("definition", "description", "policy", "calculation", "question"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(item, sort_keys=True)[:300]


def _lookup_aliases(item: dict[str, Any]) -> set[str]:
    aliases = {
        _normalize_lookup(str(item.get(key, "")))
        for key in ("id", "term", "name", "question")
        if item.get(key)
    }
    for value in _as_list(item.get("synonyms")):
        aliases.add(_normalize_lookup(str(value)))
    for value in _as_list(item.get("applies_to")):
        aliases.add(_normalize_lookup(str(value)))
    return {alias for alias in aliases if alias}


def _resolve_alias_section(payload: dict[str, Any], section: str, query: str) -> list[dict[str, Any]]:
    normalized_query = f" {_normalize_lookup(query)} "
    matches = []
    for item in _as_list(payload.get(section)):
        if not isinstance(item, dict):
            continue
        matched_alias = _matching_alias(item, normalized_query)
        if matched_alias is None:
            continue
        matches.append(
            {
                "section": "definitions" if section == "glossary" else section,
                "source_section": section,
                "match_type": "exact_alias",
                "matched_alias": matched_alias,
                "score": 1.0,
                **item,
            }
        )
    return matches


def _matching_alias(item: dict[str, Any], normalized_query: str) -> str | None:
    aliases = sorted(_lookup_aliases(item), key=lambda value: (-len(value), value))
    for alias in aliases:
        if not alias or alias in _STOPWORDS:
            continue
        if f" {alias} " in normalized_query:
            return alias
    return None


def _normalize_lookup(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 1 and token not in _STOPWORDS
    }


def _score(query_tokens: set[str], text: str) -> float:
    text_tokens = _tokens(text)
    if not text_tokens:
        return 0.0
    overlap = len(query_tokens & text_tokens)
    if overlap == 0:
        return 0.0
    return overlap / max(len(query_tokens), 1)
