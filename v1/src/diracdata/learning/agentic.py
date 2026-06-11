"""Agentic semantic artifact generation for learned data contexts."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from diracdata.config.settings import DiracDataSettings
from diracdata.learning.libraries import QueryLibraryBuildResult
from diracdata.learning.models import LearningCollection, to_jsonable
from diracdata.learning.nuance import NuanceBuildResult
from diracdata.learning.paths import active_learning_artifact_key, learning_artifact_key
from diracdata.learning.query_history import QueryHistoryRecord, load_query_history_csv
from diracdata.llms import ChatModelClient, ChatModelMessage
from diracdata.storage.object_store import ObjectStore


PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
SQL_LIBRARIES_PROMPT_PATH = PROMPT_DIR / "agentic_sql_libraries.md"
NUANCE_PROMPT_PATH = PROMPT_DIR / "agentic_nuance.md"
SUCCESS_STATUSES = {"FINISHED", "SUCCESS", "SUCCEEDED"}


@dataclass(frozen=True)
class AgenticLearningBuildResult:
    """Combined result from the agentic semantic-learning artifact pass."""

    run_id: str
    query_library_result: QueryLibraryBuildResult
    nuance_result: NuanceBuildResult
    summary_artifact_key: str | None = None
    active_summary_artifact_key: str | None = None
    semantic_map_artifact_key: str | None = None
    active_semantic_map_artifact_key: str | None = None
    schema_ast_manifest_artifact_key: str | None = None
    active_schema_ast_manifest_artifact_key: str | None = None


class AgenticLearningArtifactBuilder:
    """Ask an LLM to author compact semantic artifacts from learned evidence."""

    def __init__(
        self,
        *,
        settings: DiracDataSettings,
        object_store: ObjectStore,
        llm_client: ChatModelClient,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.object_store = object_store
        self.llm_client = llm_client
        self.progress_callback = progress_callback
        self.sql_libraries_prompt_template = SQL_LIBRARIES_PROMPT_PATH.read_text(encoding="utf-8")
        self.nuance_prompt_template = NUANCE_PROMPT_PATH.read_text(encoding="utf-8")

    def build(
        self,
        *,
        collection: LearningCollection,
        description_artifact_key: str,
        joinable_pairs_artifact_key: str | None = None,
        query_history_path: str | Path | None = None,
        query_history_records: list[QueryHistoryRecord] | None = None,
        business_grounding: dict[str, Any] | None = None,
    ) -> AgenticLearningBuildResult:
        self._emit("agentic learning: build semantic artifacts from focused evidence passes")
        context = self._base_context(
            collection=collection,
            description_artifact_key=description_artifact_key,
            joinable_pairs_artifact_key=joinable_pairs_artifact_key,
            query_history_path=query_history_path,
            query_history_records=query_history_records,
            business_grounding=business_grounding or {},
        )
        library_payload = self._generate_sql_libraries(context=context)
        nuance_payload = self._generate_nuance_artifacts_payload(
            context=context,
            library_payload=library_payload,
        )
        payload = _merge_agentic_payloads(library_payload=library_payload, nuance_payload=nuance_payload)
        payload = _enrich_with_business_grounding(
            payload=payload,
            business_grounding=context["business_grounding"],
        )

        library_result = self._write_library_artifacts(
            collection=collection,
            payload=payload,
            unique_success_query_count=len(_as_list(context.get("successful_query_history"))),
        )
        nuance_result = self._write_nuance_artifacts(collection=collection, payload=payload)

        schema_ast_keys: dict[str, str] | None = None
        if self.settings.learning_context_mode.strip().lower() == "schema_ast":
            schema_ast_keys = self._write_schema_ast_artifacts(
                collection=collection,
                descriptions=context["descriptions"],
                payload=payload,
            )

        self._update_active_manifest(schema_ast_keys=schema_ast_keys, payload=payload)
        return AgenticLearningBuildResult(
            run_id=collection.run_id,
            query_library_result=library_result,
            nuance_result=nuance_result,
            schema_ast_manifest_artifact_key=(
                schema_ast_keys["manifest"] if schema_ast_keys is not None else None
            ),
            active_schema_ast_manifest_artifact_key=(
                schema_ast_keys["active_manifest"] if schema_ast_keys is not None else None
            ),
        )

    def _base_context(
        self,
        *,
        collection: LearningCollection,
        description_artifact_key: str,
        joinable_pairs_artifact_key: str | None,
        query_history_path: str | Path | None,
        query_history_records: list[QueryHistoryRecord] | None,
        business_grounding: dict[str, Any],
    ) -> dict[str, Any]:
        descriptions = _as_dict(self.object_store.read_json(description_artifact_key))
        joinable_pairs = _read_jsonl_if_exists(self.object_store, joinable_pairs_artifact_key)
        records = query_history_records
        if records is None and query_history_path is not None:
            records = load_query_history_csv(query_history_path)

        return {
            "scope": to_jsonable(collection.scope),
            "artifact_strategy": self.settings.learning_artifact_strategy,
            "context_mode": self.settings.learning_context_mode,
            "descriptions": descriptions,
            "schema_profile": _profile_summary(
                collection,
                max_columns=self.settings.learning_agentic_max_columns,
            ),
            "business_grounding": business_grounding,
            "join_evidence": joinable_pairs,
            "successful_query_history": _query_history_summary(
                records or [],
                max_queries=self.settings.learning_agentic_query_history_limit,
            ),
        }

    def _generate_sql_libraries(
        self,
        *,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        self._emit("agentic learning: generate SQL libraries")
        prompt_context = {
            "scope": context["scope"],
            "descriptions": context["descriptions"],
            "schema_profile": context["schema_profile"],
            "business_grounding": context["business_grounding"],
            "join_evidence": context["join_evidence"],
            "successful_query_history": context["successful_query_history"],
        }
        return _normalize_sql_library_payload(
            self._complete_prompt(
                prompt_template=self.sql_libraries_prompt_template,
                placeholder="{{sql_library_learning_context_json}}",
                context=prompt_context,
            )
        )

    def _generate_nuance_artifacts_payload(
        self,
        *,
        context: dict[str, Any],
        library_payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._emit("agentic learning: generate confounders and invariants")
        prompt_context = {
            "scope": context["scope"],
            "descriptions": context["descriptions"],
            "schema_profile": context["schema_profile"],
            "business_grounding": context["business_grounding"],
            "join_evidence": context["join_evidence"],
            "sql_libraries": library_payload,
        }
        return _normalize_nuance_payload(
            self._complete_prompt(
                prompt_template=self.nuance_prompt_template,
                placeholder="{{nuance_learning_context_json}}",
                context=prompt_context,
            )
        )

    def _complete_prompt(
        self,
        *,
        prompt_template: str,
        placeholder: str,
        context: dict[str, Any],
    ) -> object:
        prompt = prompt_template.replace(
            placeholder,
            json.dumps(to_jsonable(context), indent=2, sort_keys=True),
        )
        response = self.llm_client.complete([ChatModelMessage(role="user", content=prompt)])
        return self._parse_or_repair_response(prompt=prompt, response=response)

    def _write_library_artifacts(
        self,
        *,
        collection: LearningCollection,
        payload: dict[str, Any],
        unique_success_query_count: int,
    ) -> QueryLibraryBuildResult:
        keys = _library_keys(self.settings, collection.run_id)
        sql_library = _ensure_ids(
            _as_dict_rows(payload.get("sql_library")),
            prefix="sql_library",
            seed_field="name",
        )
        for entry in sql_library:
            entry.setdefault("artifact_type", "sql_library_entry")
            entry.setdefault("compact_contract", _compact_contract_from_library_entry(entry))
        self._write_yaml_pair(
            keys["sql_library"],
            keys["active_sql_library"],
            {
                "version": 1,
                "artifact_type": "sql_library",
                "producer": "agentic_learning",
                "entries": sql_library,
            },
        )
        contract_rows = [entry for entry in sql_library if entry.get("compact_contract")]
        template_rows = [entry for entry in sql_library if str(entry.get("sql") or "").strip()]
        metric_rows = [entry for entry in sql_library if _as_list(entry.get("metrics"))]
        manifest = {
            "artifact_type": "sql_library",
            "producer": "agentic_learning",
            "catalog": collection.scope.catalog,
            "database": collection.scope.database,
            "schema": collection.scope.schema,
            "run_id": collection.run_id,
            "built_at": datetime.now(UTC).isoformat(),
            "unique_success_query_count": unique_success_query_count,
            "sql_library_count": len(sql_library),
            "query_pattern_count": len(contract_rows),
            "sql_template_count": len(template_rows),
            "entity_binding_count": 0,
            "metric_usage_count": len(metric_rows),
            "canonical_artifacts": {
                "sql_library_artifact_key": keys["sql_library"],
            },
            "active_artifacts": {
                "sql_library_artifact_key": keys["active_sql_library"],
            },
        }
        self.object_store.write_json(keys["manifest"], manifest)
        self.object_store.write_json(keys["active_manifest"], manifest)
        return QueryLibraryBuildResult(
            run_id=collection.run_id,
            manifest_artifact_key=keys["manifest"],
            active_manifest_artifact_key=keys["active_manifest"],
            unique_success_query_count=unique_success_query_count,
            query_pattern_count=len(contract_rows),
            sql_template_count=len(template_rows),
            entity_binding_count=0,
            metric_usage_count=len(metric_rows),
            sql_library_artifact_key=keys["sql_library"],
            sql_library_count=len(sql_library),
        )

    def _write_nuance_artifacts(
        self,
        *,
        collection: LearningCollection,
        payload: dict[str, Any],
    ) -> NuanceBuildResult:
        keys = _nuance_keys(self.settings, collection.run_id)
        confounders = _ensure_ids(
            _as_dict_rows(payload.get("confounders")),
            prefix="confounder",
            seed_field="term",
        )
        invariants = _ensure_ids(
            _as_dict_rows(payload.get("invariants")),
            prefix="invariant",
            seed_field="rule",
        )
        for invariant in invariants:
            invariant.setdefault("approval_status", "candidate")
        self._write_jsonl_pair(keys["confounders"], keys["active_confounders"], confounders)
        self._write_yaml_pair(
            keys["invariants"],
            keys["active_invariants"],
            {
                "version": 1,
                "artifact_type": "candidate_invariants",
                "producer": "agentic_learning",
                "invariants": invariants,
            },
        )
        manifest = {
            "artifact_type": "nuance_invariants",
            "producer": "agentic_learning",
            "catalog": collection.scope.catalog,
            "database": collection.scope.database,
            "schema": collection.scope.schema,
            "run_id": collection.run_id,
            "built_at": datetime.now(UTC).isoformat(),
            "confounder_count": len(confounders),
            "invariant_count": len(invariants),
            "analyst_question_count": 0,
            "null_candidate_count": 0,
            "canonical_artifacts": {
                "confounders_artifact_key": keys["confounders"],
                "invariants_artifact_key": keys["invariants"],
            },
            "active_artifacts": {
                "confounders_artifact_key": keys["active_confounders"],
                "invariants_artifact_key": keys["active_invariants"],
            },
        }
        self.object_store.write_json(keys["manifest"], manifest)
        self.object_store.write_json(keys["active_manifest"], manifest)
        return NuanceBuildResult(
            run_id=collection.run_id,
            manifest_artifact_key=keys["manifest"],
            active_manifest_artifact_key=keys["active_manifest"],
            null_semantics_artifact_key="",
            confounders_artifact_key=keys["confounders"],
            invariants_artifact_key=keys["invariants"],
            analyst_questions_artifact_key="",
            review_pack_artifact_key="",
            null_candidate_count=0,
            confounder_count=len(confounders),
            invariant_count=len(invariants),
            analyst_question_count=0,
        )

    def _write_schema_ast_artifacts(
        self,
        *,
        collection: LearningCollection,
        descriptions: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, str]:
        keys = _schema_ast_keys(self.settings, collection.run_id)
        nodes, edges = _schema_ast(collection=collection, descriptions=descriptions, payload=payload)
        self._write_jsonl_pair(keys["nodes"], keys["active_nodes"], nodes)
        self._write_jsonl_pair(keys["edges"], keys["active_edges"], edges)
        manifest = {
            "artifact_type": "schema_ast",
            "producer": "agentic_learning",
            "catalog": collection.scope.catalog,
            "database": collection.scope.database,
            "schema": collection.scope.schema,
            "run_id": collection.run_id,
            "built_at": datetime.now(UTC).isoformat(),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "root_node_id": f"schema:{collection.scope.catalog}.{collection.scope.database}.{collection.scope.schema}",
            "ast_policy": (
                "Column-level semantic AST. Tables are containers; domains and entities "
                "route the context compiler to relevant columns, templates, invariants, and confounders."
            ),
            "canonical_artifacts": {
                "nodes_artifact_key": keys["nodes"],
                "edges_artifact_key": keys["edges"],
            },
            "active_artifacts": {
                "nodes_artifact_key": keys["active_nodes"],
                "edges_artifact_key": keys["active_edges"],
            },
        }
        self.object_store.write_json(keys["manifest"], manifest)
        self.object_store.write_json(keys["active_manifest"], manifest)
        return keys

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

    def _update_active_manifest(
        self,
        *,
        schema_ast_keys: dict[str, str] | None,
        payload: dict[str, Any],
    ) -> None:
        active_manifest_key = active_learning_artifact_key(self.settings, relative_path="manifest.json")
        if not self.object_store.exists(active_manifest_key):
            return
        active_manifest = self.object_store.read_json(active_manifest_key)
        if not isinstance(active_manifest, dict):
            return
        if schema_ast_keys is not None:
            active_manifest.setdefault("immutable_artifacts", {})["schema_ast_manifest_artifact_key"] = (
                schema_ast_keys["manifest"]
            )
            active_manifest.setdefault("active_artifacts", {})["schema_ast_manifest_artifact_key"] = (
                schema_ast_keys["active_manifest"]
            )
        active_manifest["agentic_learning"] = {
            "artifact_strategy": self.settings.learning_artifact_strategy,
            "context_mode": self.settings.learning_context_mode,
            "sql_library_count": len(_as_dict_rows(payload.get("sql_library"))),
            "confounder_count": len(_as_dict_rows(payload.get("confounders"))),
            "invariant_count": len(_as_dict_rows(payload.get("invariants"))),
        }
        self.object_store.write_json(active_manifest_key, active_manifest)

    def _emit(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)

    def _parse_or_repair_response(self, *, prompt: str, response: str) -> object:
        try:
            return _parse_json_object(response)
        except json.JSONDecodeError as exc:
            attempts = max(0, int(self.settings.learning_agentic_repair_attempts))
            if attempts <= 0:
                raise
            last_exc = exc
            original_excerpt = response.strip()[:12000]
            for attempt_index in range(1, attempts + 1):
                self._emit(
                    f"agentic learning: repair malformed JSON response "
                    f"attempt {attempt_index}/{attempts}"
                )
                repair_prompt = _json_repair_prompt(
                    invalid_response=original_excerpt,
                    error=str(last_exc),
                )
                repaired = self.llm_client.complete([ChatModelMessage(role="user", content=repair_prompt)])
                try:
                    return _parse_json_object(repaired)
                except json.JSONDecodeError as repair_exc:
                    last_exc = repair_exc
            raise last_exc


def _normalize_sql_library_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("SQL library response must be a JSON object")
    rows = _as_dict_rows(payload.get("sql_library"))
    if rows:
        return {"sql_library": rows}
    patterns = _as_dict_rows(payload.get("query_patterns"))
    templates = _as_dict_rows(payload.get("sql_templates"))
    metric_usage = _as_dict_rows(payload.get("metric_usage_patterns"))
    return {"sql_library": _sql_library_entries_from_legacy_sections(patterns, templates, metric_usage)}


def _normalize_nuance_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("nuance response must be a JSON object")
    normalized = dict(payload)
    normalized["confounders"] = _as_dict_rows(normalized.get("confounders"))
    normalized["invariants"] = _as_dict_rows(normalized.get("invariants"))
    return normalized


def _merge_agentic_payloads(
    *,
    library_payload: dict[str, Any],
    nuance_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "sql_library": _as_dict_rows(library_payload.get("sql_library")),
        "confounders": _as_dict_rows(nuance_payload.get("confounders")),
        "invariants": _as_dict_rows(nuance_payload.get("invariants")),
    }


def _enrich_with_business_grounding(
    *,
    payload: dict[str, Any],
    business_grounding: dict[str, Any],
) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["sql_library"] = _merge_rows_by_id(
        _as_dict_rows(enriched.get("sql_library")),
        _grounding_sql_library_entries(business_grounding),
    )
    enriched["invariants"] = _merge_rows_by_id(
        _as_dict_rows(enriched.get("invariants")),
        _grounding_invariants(business_grounding),
    )
    return enriched


def _profile_summary(collection: LearningCollection, *, max_columns: int) -> dict[str, Any]:
    tables = []
    column_count = 0
    for table in collection.table_profiles:
        columns = []
        for column in table.columns:
            if column_count >= max_columns:
                continue
            column_count += 1
            columns.append(
                {
                    "table_name": column.table_name,
                    "column_name": column.column_name,
                    "data_type": column.data_type,
                    "null_count": column.null_count,
                    "null_rate": column.null_rate,
                    "distinct_count": column.distinct_count,
                    "top_values": column.top_values[:8],
                    "sample_distinct_values": column.distinct_values[:12],
                }
            )
        tables.append(
            {
                "table_name": table.table_name,
                "row_count": table.row_count,
                "sample_artifact_key": table.sample_artifact_key,
                "columns": columns,
            }
        )
    return {
        "tables": tables,
        "column_count_in_context": column_count,
        "truncated": sum(len(table.columns) for table in collection.table_profiles) > max_columns,
    }


def _query_history_summary(
    records: list[QueryHistoryRecord],
    *,
    max_queries: int,
) -> list[dict[str, Any]]:
    rows = []
    seen: set[str] = set()
    for record in records:
        if record.execution_status.upper() not in SUCCESS_STATUSES:
            continue
        sql = record.statement_text.strip()
        if not sql or sql in seen:
            continue
        seen.add(sql)
        rows.append(
            {
                "statement_id": record.statement_id,
                "statement_type": record.statement_type,
                "statement_text": sql,
            }
        )
        if len(rows) >= max_queries:
            break
    return rows


def _read_jsonl_if_exists(store: ObjectStore, key: str | None) -> list[dict[str, Any]]:
    if not key or not store.exists(key):
        return []
    return [json.loads(line) for line in store.read_text(key).splitlines() if line.strip()]


def _parse_json_object(text: str) -> object:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as first_exc:
        yaml_payload = _parse_yaml_object(stripped)
        if yaml_payload is not None:
            return yaml_payload
        start = stripped.find("{")
        end = stripped.rfind("}") + 1
        if start < 0 or end <= start:
            raise first_exc
        sliced = stripped[start:end]
        try:
            return json.loads(sliced)
        except json.JSONDecodeError:
            yaml_payload = _parse_yaml_object(sliced)
            if yaml_payload is not None:
                return yaml_payload
            raise


def _parse_yaml_object(text: str) -> object | None:
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if isinstance(payload, (dict, list)):
        return payload
    return None


def _json_repair_prompt(*, invalid_response: str, error: str) -> str:
    return (
        "You are repairing malformed JSON from a learning artifact generation step.\n"
        "Return only valid JSON.\n"
        "Do not add explanations.\n"
        "Preserve the intended structure and content as closely as possible.\n\n"
        "Keep the same intended object structure and keys from the malformed response.\n"
        "The model returned malformed JSON with this parse error:\n"
        f"{error}\n\n"
        "Malformed response:\n"
        "```text\n"
        f"{invalid_response}\n"
        "```\n"
    )


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict_rows(value: object) -> list[dict[str, Any]]:
    return [dict(item) for item in _as_list(value) if isinstance(item, dict)]


def _merge_rows_by_id(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in [primary, secondary]:
        for row in source:
            row_id = str(row.get("id") or "").strip()
            if not row_id or row_id in seen:
                continue
            seen.add(row_id)
            rows.append(row)
    return rows


def _ensure_ids(rows: list[dict[str, Any]], *, prefix: str, seed_field: str) -> list[dict[str, Any]]:
    result = []
    for index, row in enumerate(rows, start=1):
        if not str(row.get("id") or "").strip():
            seed = row.get(seed_field) or row.get("name") or row.get("term") or row.get("rule") or index
            row["id"] = f"{prefix}:{_slug(str(seed))}"
        result.append(row)
    return result


def _sql_library_entries_from_legacy_sections(
    patterns: list[dict[str, Any]],
    templates: list[dict[str, Any]],
    metric_usage: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    template_by_pattern = {
        str(row.get("source_pattern_id") or ""): row
        for row in templates
        if str(row.get("source_pattern_id") or "").strip()
    }
    metric_by_id = {
        str(row.get("metric_id") or ""): row
        for row in metric_usage
        if str(row.get("metric_id") or "").strip()
    }
    for pattern in patterns:
        metrics = [str(value) for value in _as_list(pattern.get("metrics")) if value]
        linked_metric = metric_by_id.get(metrics[0]) if metrics else None
        template = template_by_pattern.get(str(pattern.get("id") or ""))
        compact_contract = _compact_contract_from_pattern(pattern)
        entry = {
            "id": str(pattern.get("id") or ""),
            "kind": "pattern",
            "name": str(pattern.get("id") or pattern.get("fact_table") or "sql_library_pattern"),
            "query_count": int(pattern.get("query_count") or 0),
            "fact_table": pattern.get("fact_table"),
            "tables": _as_list(pattern.get("tables")),
            "metrics": metrics,
            "dimension_columns": _as_list(pattern.get("dimension_columns")),
            "filter_columns": _as_list(pattern.get("filter_columns")),
            "required_joins": _as_list(compact_contract.get("required_joins")),
            "avoid_joins": _as_list(compact_contract.get("avoid_joins")),
            "compact_contract": compact_contract,
            "sql": (
                str(template.get("sql") or "").strip()
                if isinstance(template, dict)
                else str(pattern.get("top_sql_template") or "").strip()
            ),
            "parameters": (
                _as_list(template.get("parameters"))
                if isinstance(template, dict)
                else [
                    param.get("name")
                    for example in _as_list(pattern.get("parameterized_examples"))[:1]
                    for param in _as_list(_as_dict(example).get("parameters"))
                    if isinstance(param, dict) and param.get("name")
                ]
            ),
            "rules": [],
            "evidence": _as_list(pattern.get("evidence")),
            "confidence": pattern.get("confidence", "medium"),
            "metric_contract": (
                _as_dict(linked_metric.get("sql_contract"))
                if isinstance(linked_metric, dict)
                else {}
            ),
        }
        entries.append(entry)
    for metric_id, usage in metric_by_id.items():
        if any(metric_id in [str(value) for value in _as_list(entry.get("metrics"))] for entry in entries):
            continue
        columns = [str(value) for value in _as_list(usage.get("columns")) if value]
        tables = sorted({column.split(".", 1)[0] for column in columns if "." in column})
        entries.append(
            {
                "id": str(usage.get("id") or f"sql_library:metric_{_slug(metric_id)}"),
                "kind": "metric_contract",
                "name": str(usage.get("metric_name") or metric_id),
                "query_count": int(usage.get("query_count") or 0),
                "fact_table": tables[0] if tables else None,
                "tables": tables,
                "metrics": [metric_id],
                "dimension_columns": [],
                "filter_columns": columns,
                "required_joins": [],
                "avoid_joins": [],
                "compact_contract": {
                    "fact_table": tables[0] if tables else None,
                    "tables": tables,
                    "metrics": [metric_id],
                    "dimension_columns": [],
                    "filter_columns": columns,
                    "required_joins": [],
                    "avoid_joins": [],
                },
                "sql": "",
                "parameters": [],
                "rules": [],
                "evidence": _as_list(usage.get("evidence")),
                "confidence": usage.get("confidence", "medium"),
                "metric_contract": _as_dict(usage.get("sql_contract")),
            }
        )
    return entries


def _grounding_sql_library_entries(grounding: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric in _as_list(grounding.get("metrics")):
        if not isinstance(metric, dict):
            continue
        parameterized = _as_dict(metric.get("parameterized_sql"))
        sql = str(parameterized.get("sql") or "").strip()
        metric_id = str(metric.get("id") or metric.get("name") or "metric")
        required_tables = [str(value) for value in _as_list(parameterized.get("required_tables")) if value]
        required_columns = [str(value) for value in _as_list(parameterized.get("required_columns")) if value]
        contract = _as_dict(parameterized.get("sql_contract"))
        time_column = contract.get("time_column")
        filter_columns = sorted(
            set(
                [
                    *required_columns,
                    *([str(time_column)] if isinstance(time_column, str) and time_column else []),
                ]
            )
        )
        rows.append(
            {
                "id": f"sql_library:metric_{_slug(metric_id)}",
                "kind": "metric_contract",
                "name": str(metric.get("name") or metric_id),
                "query_count": 0,
                "fact_table": required_tables[0] if required_tables else None,
                "tables": required_tables,
                "metrics": [metric_id],
                "dimension_columns": [],
                "filter_columns": filter_columns,
                "required_joins": [],
                "avoid_joins": [],
                "compact_contract": {
                    "fact_table": required_tables[0] if required_tables else None,
                    "tables": required_tables,
                    "metrics": [metric_id],
                    "dimension_columns": [],
                    "filter_columns": filter_columns,
                    "required_joins": [],
                    "avoid_joins": [],
                },
                "sql": sql,
                "parameters": [
                    str(item.get("name"))
                    for item in _as_dict_rows(parameterized.get("parameters"))
                    if item.get("name")
                ],
                "rules": [str(parameterized.get("description"))] if parameterized.get("description") else [],
                "evidence": ["business_grounding"],
                "confidence": "high",
                "metric_contract": contract,
            }
        )
    for template in _as_list(grounding.get("sql_templates")):
        if not isinstance(template, dict):
            continue
        sql = str(template.get("sql") or "").strip()
        if not sql:
            continue
        template_id = str(template.get("id") or template.get("name") or "template")
        required_tables = [str(value) for value in _as_list(template.get("required_tables")) if value]
        rows.append(
            {
                "id": f"sql_library:{_slug(template_id)}",
                "kind": "template",
                "name": str(template.get("name") or template_id),
                "query_count": 0,
                "fact_table": required_tables[0] if required_tables else None,
                "tables": required_tables,
                "metrics": [str(value) for value in _as_list(template.get("metrics")) if value],
                "dimension_columns": [str(value) for value in _as_list(template.get("dimensions")) if value],
                "filter_columns": [str(value) for value in _as_list(template.get("filters")) if value],
                "required_joins": [str(value) for value in _as_list(template.get("required_joins")) if value],
                "avoid_joins": [str(value) for value in _as_list(template.get("avoid_joins")) if value],
                "compact_contract": {
                    "fact_table": required_tables[0] if required_tables else None,
                    "tables": required_tables,
                    "metrics": [str(value) for value in _as_list(template.get("metrics")) if value],
                    "dimension_columns": [str(value) for value in _as_list(template.get("dimensions")) if value],
                    "filter_columns": [str(value) for value in _as_list(template.get("filters")) if value],
                    "required_joins": [str(value) for value in _as_list(template.get("required_joins")) if value],
                    "avoid_joins": [str(value) for value in _as_list(template.get("avoid_joins")) if value],
                },
                "sql": sql,
                "parameters": _template_parameters(sql),
                "rules": [str(template.get("description"))] if template.get("description") else [],
                "evidence": ["business_grounding"],
                "confidence": "high",
            }
        )
    return rows


def _grounding_query_patterns(grounding: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric in _as_list(grounding.get("metrics")):
        if not isinstance(metric, dict):
            continue
        parameterized = _as_dict(metric.get("parameterized_sql"))
        sql = str(parameterized.get("sql") or "").strip()
        if not sql:
            continue
        metric_id = str(metric.get("id") or metric.get("name") or "metric")
        required_tables = [str(value) for value in _as_list(parameterized.get("required_tables")) if value]
        required_columns = [str(value) for value in _as_list(parameterized.get("required_columns")) if value]
        time_column = _as_dict(parameterized.get("sql_contract")).get("time_column")
        filter_columns = [str(time_column)] if isinstance(time_column, str) and time_column else []
        rows.append(
            {
                "id": f"library_pattern:grounding_metric_{_slug(metric_id)}",
                "artifact_type": "metric_contract_pattern",
                "query_count": 0,
                "fact_table": required_tables[0] if required_tables else None,
                "tables": required_tables,
                "metrics": [metric_id],
                "dimension_columns": [],
                "filter_columns": sorted(set([*filter_columns, *required_columns])),
                "canonical_joins": [],
                "risky_alternatives": [],
                "top_sql_template": sql,
                "parameterized_examples": [],
                "compact_contract": {
                    "fact_table": required_tables[0] if required_tables else None,
                    "tables": required_tables,
                    "metrics": [metric_id],
                    "dimension_columns": [],
                    "filter_columns": sorted(set([*filter_columns, *required_columns])),
                    "required_joins": [],
                    "avoid_joins": [],
                },
                "evidence": ["business_grounding"],
                "confidence": "high",
            }
        )
    return rows


def _grounding_sql_templates(grounding: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric in _as_list(grounding.get("metrics")):
        if not isinstance(metric, dict):
            continue
        parameterized = _as_dict(metric.get("parameterized_sql"))
        sql = str(parameterized.get("sql") or "").strip()
        if not sql:
            continue
        metric_id = str(metric.get("id") or metric.get("name") or "metric")
        rows.append(
            {
                "id": f"sql_template:metric_{_slug(metric_id)}",
                "source_pattern_id": f"library_pattern:grounding_metric_{_slug(metric_id)}",
                "query_count": 0,
                "tables": [str(value) for value in _as_list(parameterized.get("required_tables")) if value],
                "metrics": [metric_id],
                "parameters": [str(item.get("name")) for item in _as_dict_rows(parameterized.get("parameters")) if item.get("name")],
                "sql": sql,
                "notes": [str(parameterized.get("description"))] if parameterized.get("description") else [],
                "evidence": ["business_grounding"],
                "confidence": "high",
            }
        )
    for template in _as_list(grounding.get("sql_templates")):
        if not isinstance(template, dict):
            continue
        sql = str(template.get("sql") or "").strip()
        if not sql:
            continue
        template_id = str(template.get("id") or template.get("name") or "template")
        required_tables = [str(value) for value in _as_list(template.get("required_tables")) if value]
        rows.append(
            {
                "id": f"sql_template:{_slug(template_id)}",
                "source_pattern_id": f"library_pattern:grounding_template_{_slug(template_id)}",
                "query_count": 0,
                "tables": required_tables,
                "metrics": [],
                "parameters": _template_parameters(sql),
                "sql": sql,
                "notes": [str(template.get("description"))] if template.get("description") else [],
                "evidence": ["business_grounding"],
                "confidence": "high",
            }
        )
    return rows


def _grounding_entity_binding_patterns(grounding: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section in ("glossary", "definitions"):
        for item in _as_list(grounding.get(section)):
            if not isinstance(item, dict):
                continue
            columns = [str(value) for value in _as_list(item.get("columns")) if value]
            if len(columns) != 1:
                continue
            term = str(item.get("term") or item.get("name") or "").strip()
            if not term:
                continue
            rows.append(
                {
                    "id": f"entity_binding:{_slug(term)}",
                    "term": term,
                    "column_ref": columns[0],
                    "example_values": [],
                    "evidence": ["business_grounding"],
                    "confidence": "high",
                }
            )
            for synonym in _as_list(item.get("synonyms")):
                if not synonym:
                    continue
                rows.append(
                    {
                        "id": f"entity_binding:{_slug(str(synonym))}",
                        "term": str(synonym),
                        "column_ref": columns[0],
                        "example_values": [],
                        "evidence": ["business_grounding"],
                        "confidence": "high",
                    }
                )
    return rows


def _grounding_metric_usage_patterns(grounding: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric in _as_list(grounding.get("metrics")):
        if not isinstance(metric, dict):
            continue
        metric_id = str(metric.get("id") or metric.get("name") or "").strip()
        if not metric_id:
            continue
        parameterized = _as_dict(metric.get("parameterized_sql"))
        sql_contract = _as_dict(parameterized.get("sql_contract"))
        rows.append(
            {
                "id": f"metric_usage:{_slug(metric_id)}",
                "metric_id": metric_id,
                "metric_name": str(metric.get("name") or metric_id),
                "columns": [str(value) for value in _as_list(metric.get("columns")) if value],
                "sql_contract": sql_contract,
                "evidence": ["business_grounding"],
                "confidence": "high",
            }
        )
    return rows


def _grounding_invariants(grounding: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _as_list(grounding.get("defaults")):
        if not isinstance(item, dict):
            continue
        default_id = str(item.get("id") or "default").strip()
        policy = str(item.get("policy") or "").strip()
        if not policy:
            continue
        field = str(item.get("field") or "").strip()
        rows.append(
            {
                "id": f"invariant:grounding:{_slug(default_id)}",
                "invariant_type": "business_default",
                "rule": policy,
                "columns": [field] if field else [],
                "source": "business_grounding",
                "evidence": ["business_grounding"],
                "confidence": "high",
                "approval_status": "candidate",
            }
        )
    for metric in _as_list(grounding.get("metrics")):
        if not isinstance(metric, dict):
            continue
        metric_id = str(metric.get("id") or metric.get("name") or "metric")
        contract = _as_dict(_as_dict(metric.get("parameterized_sql")).get("sql_contract"))
        if not contract:
            continue
        rows.append(
            {
                "id": f"invariant:metric_contract:{_slug(metric_id)}",
                "invariant_type": "metric_contract",
                "metric_id": metric_id,
                "rule": f"Use the governed SQL contract for metric {metric_id}.",
                "columns": [str(value) for value in _as_list(metric.get("columns")) if value],
                "source": "business_grounding",
                "evidence": ["business_grounding"],
                "confidence": "high",
                "approval_status": "candidate",
                "contract": contract,
            }
        )
        time_column = contract.get("time_column")
        if isinstance(time_column, str) and time_column:
            rows.append(
                {
                    "id": f"invariant:metric_time:{_slug(metric_id)}",
                    "invariant_type": "time_semantics",
                    "metric_id": metric_id,
                    "rule": f"Use {time_column} as the time column for metric {metric_id}.",
                    "columns": [time_column],
                    "source": "business_grounding",
                    "evidence": ["business_grounding"],
                    "confidence": "high",
                    "approval_status": "candidate",
                }
            )
    return rows


def _template_parameters(sql: str) -> list[str]:
    return sorted(set(re.findall(r"{{\s*([a-zA-Z0-9_]+)\s*}}", sql)))


def _compact_contract_from_pattern(pattern: dict[str, Any]) -> dict[str, Any]:
    return {
        "fact_table": pattern.get("fact_table"),
        "tables": _as_list(pattern.get("tables")),
        "metrics": _as_list(pattern.get("metrics")),
        "dimension_columns": _as_list(pattern.get("dimension_columns")),
        "filter_columns": _as_list(pattern.get("filter_columns")),
        "required_joins": [
            f"{join.get('left_ref')} {join.get('operator', '=')} {join.get('right_ref')}"
            for join in _as_dict_rows(pattern.get("canonical_joins"))
            if join.get("left_ref") and join.get("right_ref")
        ],
        "avoid_joins": [
            str(item.get("avoid_join"))
            for item in _as_dict_rows(pattern.get("risky_alternatives"))
            if item.get("avoid_join")
        ],
    }


def _compact_contract_from_library_entry(entry: dict[str, Any]) -> dict[str, Any]:
    compact = _as_dict(entry.get("compact_contract"))
    if compact:
        return compact
    return {
        "fact_table": entry.get("fact_table"),
        "tables": _as_list(entry.get("tables")),
        "metrics": _as_list(entry.get("metrics")),
        "dimension_columns": _as_list(entry.get("dimension_columns")),
        "filter_columns": _as_list(entry.get("filter_columns")),
        "required_joins": _as_list(entry.get("required_joins")),
        "avoid_joins": _as_list(entry.get("avoid_joins")),
    }


def _schema_ast(
    *,
    collection: LearningCollection,
    descriptions: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root_id = f"schema:{collection.scope.catalog}.{collection.scope.database}.{collection.scope.schema}"
    nodes: list[dict[str, Any]] = [
        {
            "id": root_id,
            "node_type": "schema",
            "name": collection.scope.schema,
            "scope": to_jsonable(collection.scope),
        }
    ]
    edges: list[dict[str, Any]] = []

    column_refs = {
        f"{table.table_name}.{column.column_name}"
        for table in collection.table_profiles
        for column in table.columns
    }
    table_descriptions = _as_dict(descriptions.get("tables"))
    column_descriptions = _as_dict(descriptions.get("columns"))
    for table in collection.table_profiles:
        table_id = f"table:{table.table_name}"
        table_desc = _as_dict(table_descriptions.get(table.table_name))
        nodes.append(
            {
                "id": table_id,
                "node_type": "table",
                "table_name": table.table_name,
                "description": table_desc.get("short_description"),
                "row_count": table.row_count,
            }
        )
        edges.append(_edge(root_id, table_id, "schema_has_table"))
        table_column_descriptions = _as_dict(column_descriptions.get(table.table_name))
        for column in table.columns:
            column_ref = f"{table.table_name}.{column.column_name}"
            column_id = f"column:{column_ref}"
            column_desc = _as_dict(table_column_descriptions.get(column.column_name))
            nodes.append(
                {
                    "id": column_id,
                    "node_type": "column",
                    "table_name": table.table_name,
                    "column_name": column.column_name,
                    "column_ref": column_ref,
                    "data_type": column.data_type,
                    "description": column_desc.get("short_description"),
                    "null_rate": column.null_rate,
                    "distinct_count": column.distinct_count,
                }
            )
            edges.append(_edge(table_id, column_id, "table_has_column"))

    for domain in _as_dict_rows(payload.get("domains")):
        domain_id = str(domain.get("id") or f"domain:{_slug(str(domain.get('name') or 'domain'))}")
        nodes.append({**domain, "id": domain_id, "node_type": "domain"})
        edges.append(_edge(root_id, domain_id, "schema_has_domain"))
        for column_ref in _valid_refs(domain.get("columns"), column_refs):
            edges.append(_edge(domain_id, f"column:{column_ref}", "domain_uses_column"))

    for entity in _as_dict_rows(payload.get("key_entities")):
        entity_id = str(entity.get("id") or f"entity:{_slug(str(entity.get('name') or 'entity'))}")
        nodes.append({**entity, "id": entity_id, "node_type": "entity"})
        edges.append(_edge(root_id, entity_id, "schema_has_entity"))
        entity_columns = [
            *_valid_refs(entity.get("primary_columns"), column_refs),
            *_valid_refs(entity.get("supporting_columns"), column_refs),
        ]
        for column_ref in sorted(set(entity_columns)):
            edges.append(_edge(entity_id, f"column:{column_ref}", "entity_uses_column"))

    for pattern in _as_dict_rows(payload.get("query_patterns")):
        pattern_id = str(pattern.get("id") or f"library_pattern:{_slug(json.dumps(pattern))}")
        nodes.append(
            {
                "id": pattern_id,
                "node_type": "library_pattern",
                "metrics": pattern.get("metrics") or [],
                "fact_table": pattern.get("fact_table"),
            }
        )
        for column_ref in _pattern_columns(pattern, column_refs):
            edges.append(_edge(f"column:{column_ref}", pattern_id, "column_has_library_pattern"))

    for invariant in _as_dict_rows(payload.get("invariants")):
        invariant_id = str(invariant.get("id") or f"invariant:{_slug(str(invariant.get('rule')))}")
        nodes.append(
            {
                "id": invariant_id,
                "node_type": "invariant",
                "rule": invariant.get("rule"),
                "invariant_type": invariant.get("invariant_type"),
            }
        )
        for column_ref in _valid_refs(invariant.get("columns"), column_refs):
            edges.append(_edge(f"column:{column_ref}", invariant_id, "column_has_invariant"))

    for confounder in _as_dict_rows(payload.get("confounders")):
        confounder_id = str(confounder.get("id") or f"confounder:{_slug(str(confounder.get('term')))}")
        nodes.append(
            {
                "id": confounder_id,
                "node_type": "confounder",
                "term": confounder.get("term"),
                "reason": confounder.get("reason"),
            }
        )
        for column_ref in _valid_refs(confounder.get("columns"), column_refs):
            edges.append(_edge(f"column:{column_ref}", confounder_id, "column_has_confounder"))

    return _dedupe_nodes(nodes), _dedupe_edges(edges)


def _pattern_columns(pattern: dict[str, Any], valid_refs: set[str]) -> list[str]:
    refs = [
        *_valid_refs(pattern.get("dimension_columns"), valid_refs),
        *_valid_refs(pattern.get("filter_columns"), valid_refs),
    ]
    for join in _as_dict_rows(pattern.get("canonical_joins")):
        refs.extend(_valid_refs([join.get("left_ref"), join.get("right_ref")], valid_refs))
    compact = _as_dict(pattern.get("compact_contract"))
    refs.extend(_valid_refs(compact.get("dimension_columns"), valid_refs))
    refs.extend(_valid_refs(compact.get("filter_columns"), valid_refs))
    return sorted(set(refs))


def _valid_refs(value: object, valid_refs: set[str]) -> list[str]:
    return [str(item) for item in _as_list(value) if str(item) in valid_refs]


def _edge(source: str, target: str, edge_type: str) -> dict[str, str]:
    return {"source": source, "target": target, "edge_type": edge_type}


def _dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        by_id[str(node["id"])] = node
    return [by_id[key] for key in sorted(by_id)]


def _dedupe_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for edge in edges:
        key = (edge["source"], edge["target"], edge["edge_type"])
        if key in seen:
            continue
        seen.add(key)
        result.append(edge)
    return sorted(result, key=lambda item: (item["source"], item["edge_type"], item["target"]))


def _review_pack(*, payload: dict[str, Any]) -> str:
    summary = _as_dict(payload.get("schema_summary"))
    lines = [
        "# Agentic Learning Review Pack",
        "",
        str(summary.get("short_summary") or "").strip(),
        "",
        "## Analyst Questions",
        "",
    ]
    for question in _as_dict_rows(payload.get("analyst_questions"))[:30]:
        lines.append(f"- {question.get('question')}")
    lines.extend(["", "## Candidate Invariants", ""])
    for invariant in _as_dict_rows(payload.get("invariants"))[:30]:
        lines.append(f"- {invariant.get('rule')}")
    return "\n".join(lines).strip() + "\n"


def _library_keys(settings: DiracDataSettings, run_id: str) -> dict[str, str]:
    return _artifact_keys(
        settings,
        run_id,
        {
            "manifest": "libraries/manifest.json",
            "sql_library": "libraries/sql_library.yaml",
        },
    )


def _nuance_keys(settings: DiracDataSettings, run_id: str) -> dict[str, str]:
    return _artifact_keys(
        settings,
        run_id,
        {
            "manifest": "nuance/manifest.json",
            "confounders": "nuance/confounders.jsonl",
            "invariants": "nuance/invariants.yaml",
        },
    )


def _schema_ast_keys(settings: DiracDataSettings, run_id: str) -> dict[str, str]:
    return _artifact_keys(
        settings,
        run_id,
        {
            "manifest": "schema_ast/manifest.json",
            "nodes": "schema_ast/nodes.jsonl",
            "edges": "schema_ast/edges.jsonl",
        },
    )


def _artifact_keys(
    settings: DiracDataSettings,
    run_id: str,
    relative_paths: dict[str, str],
) -> dict[str, str]:
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


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return clean[:80] or "artifact"
