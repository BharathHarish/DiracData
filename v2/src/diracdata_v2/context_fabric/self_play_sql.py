"""Agentic SQL authoring for semantic self-play gap tasks."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from diracdata_v2.query import DuckDBEngine
from diracdata_v2.semantic_catalog.sql_analysis import analyze_sql_references
from diracdata_v2.tools.sql import validate_sql


class SemanticSelfPlaySQLGenerator(Protocol):
    """LLM adapter used to author SQL for reviewable self-play tasks."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


@dataclass(frozen=True)
class SemanticSelfPlaySQLBuildResult:
    document: dict[str, Any]
    local_path: Path | None = None
    object_key: str | None = None


class SemanticSelfPlaySQLBuilder:
    """Author SQL candidates for semantic coverage gaps.

    The generated entries are SQL-library-compatible but are written to a
    separate candidate artifact. They should be promoted into the SQL library
    only after validation and human review.
    """

    def __init__(
        self,
        *,
        generator: SemanticSelfPlaySQLGenerator | None,
        engine: DuckDBEngine | None = None,
        task_limit: int = 20,
        batch_size: int = 25,
        batch_limit: int | None = None,
        max_repair_iterations: int = 2,
        validation_rows: int = 5,
        strict_agentic: bool = False,
    ) -> None:
        self.generator = generator
        self.engine = engine
        self.task_limit = max(0, task_limit)
        self.batch_size = max(1, batch_size)
        self.batch_limit = None if batch_limit is None else max(0, batch_limit)
        self.max_repair_iterations = max(0, max_repair_iterations)
        self.validation_rows = max(1, validation_rows)
        self.strict_agentic = strict_agentic

    def build(
        self,
        *,
        semantic_coverage_document: dict[str, Any],
        metadata_descriptions: dict[str, Any],
        sql_library_document: dict[str, Any] | None = None,
        catalog: str,
        database: str,
        schema: str,
        run_id: str,
        output_dir: Path | None = None,
        object_store: Any | None = None,
        object_prefix: str = "v2/learning/artifacts",
    ) -> SemanticSelfPlaySQLBuildResult:
        table_columns = _table_columns_from_metadata(metadata_descriptions)
        tasks = _selected_tasks(semantic_coverage_document, limit=self.task_limit)
        task_batches = _batches(list(tasks.items()), self.batch_size)
        execution_batches = task_batches[: self.batch_limit] if self.batch_limit is not None else task_batches
        attempted_tasks = dict(item for batch in execution_batches for item in batch)
        entries: dict[str, dict[str, Any]] = {}
        generation_errors: list[str] = []

        if attempted_tasks and self.generator is not None:
            for batch in execution_batches:
                batch_tasks = dict(batch)
                candidates, batch_errors = self._generate_candidates(
                    tasks=batch_tasks,
                    metadata_descriptions=metadata_descriptions,
                    sql_library_document=sql_library_document,
                )
                generation_errors.extend(batch_errors)
                for candidate in candidates:
                    task_id = str(candidate.get("task_id") or "")
                    task = tasks.get(task_id)
                    if task is None:
                        generation_errors.append(f"candidate referenced unknown task_id: {task_id}")
                        continue
                    entry = self._entry_with_repairs(
                        candidate=candidate,
                        task_id=task_id,
                        task=task,
                        table_columns=table_columns,
                        metadata_descriptions=metadata_descriptions,
                        sql_library_document=sql_library_document,
                    )
                    if entry is not None:
                        entries[str(entry["entry_id"])] = entry
        elif attempted_tasks and self.generator is None:
            generation_errors.append("semantic self-play SQL generation skipped: no generator configured")

        if self.strict_agentic and attempted_tasks:
            passed_count = sum(1 for entry in entries.values() if entry.get("validation", {}).get("status") == "passed")
            if generation_errors or passed_count == 0:
                details = "; ".join(generation_errors[:3]) or "no validation-passed generated SQL entries"
                raise RuntimeError(f"agentic semantic self-play SQL generation failed: {details}")

        document = {
            "version": 1,
            "artifact_type": "semantic_self_play_sql",
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "scope": {"catalog": catalog, "database": database, "schema": schema},
            "summary": {
                "task_count": len(attempted_tasks),
                "selected_task_count": len(tasks),
                "attempted_task_count": len(attempted_tasks),
                "batch_size": self.batch_size,
                "batch_limit": self.batch_limit,
                "selected_batch_count": len(task_batches),
                "executed_batch_count": len(execution_batches),
                "entry_count": len(entries),
                "validation_passed_count": sum(
                    1 for entry in entries.values() if entry.get("validation", {}).get("status") == "passed"
                ),
                "validation_failed_count": sum(
                    1 for entry in entries.values() if entry.get("validation", {}).get("status") == "failed"
                ),
                "validation_skipped_count": sum(
                    1 for entry in entries.values() if entry.get("validation", {}).get("status") == "skipped"
                ),
                "repair_attempt_count": sum(int(entry.get("repair_attempts") or 0) for entry in entries.values()),
                "repaired_passed_count": sum(
                    1
                    for entry in entries.values()
                    if entry.get("validation", {}).get("status") == "passed" and int(entry.get("repair_attempts") or 0) > 0
                ),
                "generation_error_count": len(generation_errors),
            },
            "tasks": attempted_tasks,
            "entries": dict(sorted(entries.items())),
            "generation_errors": generation_errors,
            "promotion_guidance": {
                "status": "not_promoted",
                "note": (
                    "These entries are candidates. Promote validation-passed entries into sql_library.json "
                    "only after analyst review."
                ),
            },
        }

        local_path = None
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            local_path = output_dir / "semantic_self_play_sql.json"
            local_path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")

        object_key = None
        if object_store is not None:
            object_key = f"{object_prefix.strip('/')}/{run_id}/semantic_self_play_sql.json"
            object_store.write_json(object_key, document)

        return SemanticSelfPlaySQLBuildResult(document=document, local_path=local_path, object_key=object_key)

    def _generate_candidates(
        self,
        *,
        tasks: dict[str, dict[str, Any]],
        metadata_descriptions: dict[str, Any],
        sql_library_document: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        payload = {
            "tasks": [
                {
                    "task_id": task_id,
                    **_compact_task(task),
                    "schema_context": _schema_context_for_task(
                        task=task,
                        metadata_descriptions=metadata_descriptions,
                    ),
                    "join_context": _join_context_for_task(
                        task=task,
                        sql_library_document=sql_library_document,
                    ),
                }
                for task_id, task in tasks.items()
            ]
        }
        prompt = _author_prompt().replace("{{payload_json}}", json.dumps(payload, indent=2, sort_keys=True))
        try:
            text = self.generator.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "You author safe, read-only SQL for reviewable analytics self-play tasks. "
                            "Return only valid JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
            )
            parsed = _loads_json_object(text)
        except Exception as exc:  # noqa: BLE001
            return [], [f"SQL candidate generation failed: {type(exc).__name__}: {exc}"]
        raw_candidates = parsed.get("candidates")
        if not isinstance(raw_candidates, list):
            return [], ["SQL candidate generation returned no candidates list"]
        return [item for item in raw_candidates if isinstance(item, dict)], []

    def _entry_with_repairs(
        self,
        *,
        candidate: dict[str, Any],
        task_id: str,
        task: dict[str, Any],
        table_columns: dict[str, list[str]],
        metadata_descriptions: dict[str, Any],
        sql_library_document: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        entry = _entry_from_candidate(
            candidate=candidate,
            task_id=task_id,
            task=task,
            table_columns=table_columns,
            engine=self.engine,
            validation_rows=self.validation_rows,
        )
        if entry is None:
            return None
        history = [_validation_history_item(entry, attempt=0)]
        repair_attempts = 0

        while (
            self.generator is not None
            and self.max_repair_iterations > repair_attempts
            and entry.get("validation", {}).get("status") == "failed"
            and entry.get("sql")
            and not entry.get("blocking_reason")
        ):
            repair_attempts += 1
            repair_candidate, repair_error = self._repair_candidate(
                candidate=candidate,
                task_id=task_id,
                task=task,
                validation=entry.get("validation", {}),
                metadata_descriptions=metadata_descriptions,
                sql_library_document=sql_library_document,
                attempt=repair_attempts,
            )
            if repair_error:
                entry["validation"] = {
                    **entry.get("validation", {}),
                    "repair_error": repair_error,
                }
                history.append(_validation_history_item(entry, attempt=repair_attempts))
                break
            candidate = repair_candidate
            entry = _entry_from_candidate(
                candidate=candidate,
                task_id=task_id,
                task=task,
                table_columns=table_columns,
                engine=self.engine,
                validation_rows=self.validation_rows,
            )
            if entry is None:
                return None
            history.append(_validation_history_item(entry, attempt=repair_attempts))

        entry["repair_attempts"] = repair_attempts
        entry["validation_history"] = history
        return entry

    def _repair_candidate(
        self,
        *,
        candidate: dict[str, Any],
        task_id: str,
        task: dict[str, Any],
        validation: dict[str, Any],
        metadata_descriptions: dict[str, Any],
        sql_library_document: dict[str, Any] | None,
        attempt: int,
    ) -> tuple[dict[str, Any], str | None]:
        payload = {
            "attempt": attempt,
            "task_id": task_id,
            "task": {
                **_compact_task(task),
                "schema_context": _schema_context_for_task(
                    task=task,
                    metadata_descriptions=metadata_descriptions,
                ),
                "join_context": _join_context_for_task(
                    task=task,
                    sql_library_document=sql_library_document,
                ),
            },
            "candidate": _compact_candidate(candidate),
            "validation_failure": validation,
        }
        prompt = _repair_prompt().replace("{{payload_json}}", json.dumps(payload, indent=2, sort_keys=True))
        try:
            text = self.generator.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "You repair safe, read-only SQL for reviewable analytics self-play tasks. "
                            "Return only valid JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
            )
            parsed = _loads_json_object(text)
        except Exception as exc:  # noqa: BLE001
            return candidate, f"SQL repair failed: {type(exc).__name__}: {exc}"

        repaired = parsed.get("candidate")
        if not isinstance(repaired, dict):
            candidates = parsed.get("candidates")
            if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
                repaired = candidates[0]
        if not isinstance(repaired, dict):
            return candidate, "SQL repair returned no candidate object"
        repaired.setdefault("task_id", task_id)
        return repaired, None


def promote_validated_self_play(
    document: dict[str, Any], *, promoted_status: str = "observed"
) -> dict[str, Any]:
    """Auto-promote execution-proven, unambiguous self-play entries so recall can use them.

    The diagnosed bottleneck: self-play authors recipes but every one stays needs_review,
    and recall only surfaces trusted entries, so the whole output is invisible to the agent.
    This applies the measured-vs-guessed rule: an entry whose SQL actually executed
    (validation passed, no blocking reason) and whose task carries no ambiguity is measured
    proof -> promote it to a usable (but sub-approved) trust tier. Anything a human must judge
    -- a business-meaning ambiguity or open question -- stays needs_review. Execution proves
    the SQL runs, not that it is the right answer, so the tier is deliberately below approved.

    Pure: returns a new document; does not mutate the input.
    """

    tasks = _dict(document.get("tasks"))
    entries = _dict(document.get("entries"))
    promoted: dict[str, dict[str, Any]] = {}
    promoted_count = 0
    held_count = 0
    for entry_id, raw in entries.items():
        entry = dict(_dict(raw))
        task = _dict(tasks.get(str(entry.get("generated_from_task_id") or "")))
        validation_passed = _dict(entry.get("validation")).get("status") == "passed"
        has_block = bool(_one_line(entry.get("blocking_reason")))
        has_ambiguity = bool(_strings(task.get("ambiguity_notes")) or _list(task.get("open_questions")))
        eligible = validation_passed and not has_block and not has_ambiguity
        if eligible:
            entry["review_status"] = promoted_status
            entry["promoted_by"] = "auto_execution_proof"
            promoted_count += 1
        else:
            entry.setdefault("review_status", "needs_review")
            held_count += 1
        promoted[str(entry_id)] = entry

    result = dict(document)
    result["entries"] = promoted
    result["promotion_guidance"] = {
        "status": "auto_promoted_on_proof",
        "promoted_status": promoted_status,
        "promoted_count": promoted_count,
        "held_for_review_count": held_count,
        "note": (
            "Validation-passed, unambiguous entries were promoted to the observed tier so recall "
            "can use them at sub-approved trust. Ambiguous or unvalidated entries remain needs_review."
        ),
    }
    return result


def _author_prompt() -> str:
    return """Author SQL candidates for reviewable self-play tasks.

Requirements:
- Return only valid JSON.
- Use only the supplied tables and columns for each task.
- Use the supplied join_context when it provides a relevant learned join edge.
- SQL must be read-only DuckDB SELECT/WITH SQL.
- Prefer compact SQL that directly exercises the target refs.
- Do not invent business definitions, filter values, metrics, or joins.
- Do not invent key columns. If the schema uses reference columns, join them to the supplied record/id columns that are actually present in schema_context.
- If a task cannot be safely expressed, return an entry with empty sql and a blocking reason.

JSON shape:
{
  "candidates": [
    {
      "task_id": "task id from input",
      "canonical_question": "natural language task",
      "sql": "SELECT ...",
      "assumptions": [],
      "validation_checks": [],
      "blocking_reason": ""
    }
  ]
}

Tasks:
{{payload_json}}
"""


def _repair_prompt() -> str:
    return """Repair one SQL candidate for a reviewable self-play task.

Requirements:
- Return only valid JSON.
- Preserve the task intent and required refs.
- Use only supplied tables and columns from schema_context.
- Prefer join_context edges when they apply.
- Fix the exact validation failure. If the failure proves the task cannot be safely expressed, return empty sql with a blocking reason.
- SQL must be read-only DuckDB SELECT/WITH SQL.
- Do not invent key columns, filter values, business definitions, or joins.

JSON shape:
{
  "candidate": {
    "task_id": "task id from input",
    "canonical_question": "natural language task",
    "sql": "SELECT ...",
    "assumptions": [],
    "validation_checks": [],
    "blocking_reason": ""
  }
}

Repair payload:
{{payload_json}}
"""


def _entry_from_candidate(
    *,
    candidate: dict[str, Any],
    task_id: str,
    task: dict[str, Any],
    table_columns: dict[str, list[str]],
    engine: DuckDBEngine | None,
    validation_rows: int,
) -> dict[str, Any] | None:
    sql = _clean_sql(candidate.get("sql"))
    question = _one_line(candidate.get("canonical_question")) or _one_line(task.get("canonical_question"))
    blocking_reason = _one_line(candidate.get("blocking_reason"))
    validation = _validate_candidate_sql(
        sql=sql,
        task=task,
        table_columns=table_columns,
        engine=engine,
        blocking_reason=blocking_reason,
        validation_rows=validation_rows,
    )
    analysis = analyze_sql_references(sql, table_columns) if sql else None
    tables = list(analysis.tables) if analysis is not None else []
    columns = list(analysis.columns) if analysis is not None else []
    if not sql and not blocking_reason:
        return None
    entry_id = _entry_id(task_id=task_id, question=question, sql=sql or blocking_reason)
    return {
        "entry_id": entry_id,
        "template": question or f"Semantic self-play task {task_id}",
        "sql": sql,
        "source": "self_play",
        "self_play_mode": "semantic_gap",
        "review_status": "needs_review",
        "tables": tables,
        "columns": columns,
        "join_edges": [pair.to_dict() for pair in analysis.join_pairs] if analysis is not None else [],
        "canonical_question": question,
        "paraphrases": _strings(task.get("paraphrases")),
        "assumptions": _strings(candidate.get("assumptions")),
        "validation_checks": _strings(candidate.get("validation_checks")) or _strings(task.get("validation_checks")),
        "validation": validation,
        "generated_from_task_id": task_id,
        "target_refs": _strings(task.get("target_refs")),
        "required_tables": _strings(task.get("required_tables")),
        "required_columns": _strings(task.get("required_columns")),
        "task_rationale": _one_line(task.get("rationale")),
        "blocking_reason": blocking_reason,
        "analysis": {"parser": analysis.parser if analysis is not None else ""},
    }


def _validate_candidate_sql(
    *,
    sql: str,
    task: dict[str, Any],
    table_columns: dict[str, list[str]],
    engine: DuckDBEngine | None,
    blocking_reason: str,
    validation_rows: int,
) -> dict[str, Any]:
    if blocking_reason and not sql:
        return {"status": "failed", "error": blocking_reason}
    if not sql:
        return {"status": "failed", "error": "Generated SQL is empty"}
    safe = validate_sql(sql, available_tables=set(table_columns))
    if safe.get("status") != "ok":
        return {"status": "failed", "error": safe.get("error"), **safe}
    analysis = analyze_sql_references(sql, table_columns)
    missing_tables = sorted(set(_strings(task.get("required_tables"))) - set(analysis.tables))
    missing_columns = sorted(set(_strings(task.get("required_columns"))) - set(analysis.columns))
    if missing_tables or missing_columns:
        return {
            "status": "failed",
            "error": "Generated SQL does not cover required self-play refs",
            "missing_tables": missing_tables,
            "missing_columns": missing_columns,
            "tables": list(analysis.tables),
            "columns": list(analysis.columns),
        }
    if engine is None:
        return {
            "status": "skipped",
            "reason": "DuckDB engine not configured; structural validation only.",
            "tables": list(analysis.tables),
            "columns": list(analysis.columns),
        }
    try:
        explain_sql = f"EXPLAIN {sql.strip().rstrip(';')}"
        result = engine.query(explain_sql, max_rows=20)
        bounded_result = engine.query(sql, max_rows=validation_rows)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "tables": list(analysis.tables),
            "columns": list(analysis.columns),
        }
    return {
        "status": "passed",
        "dry_run": "explain",
        "explain_columns": result.columns,
        "explain_row_count": len(result.rows),
        "bounded_execution": "passed",
        "bounded_execution_columns": bounded_result.columns,
        "bounded_execution_row_count": len(bounded_result.rows),
        "tables": list(analysis.tables),
        "columns": list(analysis.columns),
    }


def _selected_tasks(document: dict[str, Any], *, limit: int) -> dict[str, dict[str, Any]]:
    tasks = document.get("self_play_gap_plan", {}).get("tasks", {})
    if not isinstance(tasks, dict):
        return {}
    selected = []
    for task_id, task in sorted(tasks.items()):
        if not isinstance(task, dict):
            continue
        if str(task.get("review_status") or "needs_review") == "rejected":
            continue
        selected.append((str(task_id), task))
        if len(selected) >= limit:
            break
    return dict(selected)


def _compact_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "canonical_question": _one_line(task.get("canonical_question")),
        "target_refs": _strings(task.get("target_refs")),
        "required_tables": _strings(task.get("required_tables")),
        "required_columns": _strings(task.get("required_columns")),
        "rationale": _one_line(task.get("rationale")),
        "sql_intent": task.get("sql_intent") if isinstance(task.get("sql_intent"), dict) else {},
        "validation_checks": _strings(task.get("validation_checks")),
        "ambiguity_notes": _strings(task.get("ambiguity_notes")),
        "open_questions": task.get("open_questions") if isinstance(task.get("open_questions"), list) else [],
    }


def _schema_context_for_task(*, task: dict[str, Any], metadata_descriptions: dict[str, Any]) -> dict[str, Any]:
    tables = set(_strings(task.get("required_tables")))
    for column_ref in _strings(task.get("required_columns")) + _strings(task.get("target_refs")):
        if "." in column_ref:
            tables.add(column_ref.split(".", 1)[0])
    context = {}
    for table in sorted(tables):
        table_meta = _dict(_dict(metadata_descriptions.get("tables")).get(table))
        columns = {}
        for column, column_meta in sorted(_dict(_dict(metadata_descriptions.get("columns")).get(table)).items()):
            columns[column] = {
                "short_description": _one_line(_dict(column_meta).get("short_description")),
                "long_description": _one_line(_dict(column_meta).get("long_description")),
            }
        context[table] = {
            "short_description": _one_line(table_meta.get("short_description")),
            "long_description": _one_line(table_meta.get("long_description")),
            "columns": columns,
        }
    return context


def _join_context_for_task(
    *,
    task: dict[str, Any],
    sql_library_document: dict[str, Any] | None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    if not sql_library_document:
        return []
    task_tables = _task_tables(task)
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    entries = _dict(sql_library_document.get("entries"))
    for entry_id, entry in sorted(entries.items()):
        entry_obj = _dict(entry)
        if _one_line(entry_obj.get("review_status")) == "rejected":
            continue
        for raw_edge in entry_obj.get("join_edges", []) if isinstance(entry_obj.get("join_edges"), list) else []:
            edge = _dict(raw_edge)
            left = _one_line(edge.get("left_column"))
            right = _one_line(edge.get("right_column"))
            if "." not in left or "." not in right:
                continue
            tables = {left.split(".", 1)[0], right.split(".", 1)[0]}
            if task_tables and not (tables & task_tables):
                continue
            key = " = ".join(sorted((left, right)))
            if key in seen:
                continue
            seen.add(key)
            output.append(
                {
                    "left_column": left,
                    "right_column": right,
                    "tables": sorted(tables),
                    "sql_condition": _one_line(edge.get("sql_condition")) or f"{left} = {right}",
                    "source_entry_id": str(entry_id),
                    "source": _one_line(entry_obj.get("source")),
                    "review_status": _one_line(entry_obj.get("review_status")),
                    "template": _one_line(entry_obj.get("template")),
                }
            )
            if len(output) >= limit:
                return output
    return output


def _task_tables(task: dict[str, Any]) -> set[str]:
    tables = set(_strings(task.get("required_tables")))
    for column_ref in _strings(task.get("required_columns")) + _strings(task.get("target_refs")):
        if "." in column_ref:
            tables.add(column_ref.split(".", 1)[0])
    return tables


def _table_columns_from_metadata(metadata_descriptions: dict[str, Any]) -> dict[str, list[str]]:
    return {
        str(table): sorted(str(column) for column in _dict(table_columns))
        for table, table_columns in _dict(metadata_descriptions.get("columns")).items()
    }


def _batches(items: list[tuple[str, dict[str, Any]]], size: int) -> list[list[tuple[str, dict[str, Any]]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": _one_line(candidate.get("task_id")),
        "canonical_question": _one_line(candidate.get("canonical_question")),
        "sql": _clean_sql(candidate.get("sql")),
        "assumptions": _strings(candidate.get("assumptions")),
        "validation_checks": _strings(candidate.get("validation_checks")),
        "blocking_reason": _one_line(candidate.get("blocking_reason")),
    }


def _validation_history_item(entry: dict[str, Any], *, attempt: int) -> dict[str, Any]:
    validation = _dict(entry.get("validation"))
    return {
        "attempt": attempt,
        "status": _one_line(validation.get("status")),
        "error": _one_line(validation.get("error")),
        "tables": _strings(validation.get("tables")),
        "columns": _strings(validation.get("columns")),
        "missing_tables": _strings(validation.get("missing_tables")),
        "missing_columns": _strings(validation.get("missing_columns")),
    }


def _entry_id(*, task_id: str, question: str, sql: str) -> str:
    seed = f"{task_id}|{question}|{sql}"
    return "self_play_semantic:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:14]


def _clean_sql(value: Any) -> str:
    raw = str(value or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:sql)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip().rstrip(";")


def _loads_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            raise
        payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("expected JSON object")
    return payload


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_one_line(item) for item in value if _one_line(item)]
    if isinstance(value, str) and value.strip():
        if ";" in value:
            return [_one_line(item) for item in value.split(";") if _one_line(item)]
        return [_one_line(value)]
    return []


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())
