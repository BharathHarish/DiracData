"""Agentic semantic coverage learning for self-play planning."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from diracdata_v2.context_fabric.contracts import NormalizedKnowledgeCase, NormalizedKnowledgeCorpus


class SemanticCoverageGenerator(Protocol):
    """LLM adapter used to learn entity maps and self-play gaps."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


@dataclass(frozen=True)
class SemanticCoverageBuildResult:
    document: dict[str, Any]
    local_path: Path | None = None
    object_key: str | None = None


class SemanticCoverageBuilder:
    """Build a schema-level semantic coverage control-plane artifact.

    LLMs propose entity semantics and self-play gap tasks. Code only validates
    references, computes coverage counts, preserves provenance, and writes the
    artifact.
    """

    def __init__(
        self,
        *,
        generator: SemanticCoverageGenerator | None = None,
        gap_limit: int = 50,
        strict_agentic: bool = False,
    ) -> None:
        self.generator = generator
        self.gap_limit = max(0, gap_limit)
        self.strict_agentic = strict_agentic

    def build(
        self,
        *,
        corpus: NormalizedKnowledgeCorpus,
        metadata_descriptions: dict[str, Any],
        schema_graph: dict[str, Any],
        catalog: str,
        database: str,
        schema: str,
        run_id: str,
        output_dir: Path | None = None,
        object_store: Any | None = None,
        object_prefix: str = "v2/learning/artifacts",
    ) -> SemanticCoverageBuildResult:
        table_columns = _table_columns_from_metadata(metadata_descriptions)
        entity_map, entity_errors = self._build_entity_map(
            metadata_descriptions=metadata_descriptions,
            schema_graph=schema_graph,
            corpus=corpus,
            table_columns=table_columns,
        )
        coverage_matrix = _coverage_matrix(
            corpus=corpus,
            entity_map=entity_map,
            table_columns=table_columns,
        )
        gap_plan, gap_errors = self._build_gap_plan(
            entity_map=entity_map,
            coverage_matrix=coverage_matrix,
            metadata_descriptions=metadata_descriptions,
            table_columns=table_columns,
        )
        if self.strict_agentic and self.generator is not None:
            generated_entities = [
                item for item in entity_map.get("entities", {}).values() if item.get("learning_mode") == "generated"
            ]
            generated_tasks = [
                item for item in gap_plan.get("tasks", {}).values() if item.get("learning_mode") == "generated"
            ]
            if entity_errors or not generated_entities:
                details = "; ".join(entity_errors[:3]) or "no generated entity map"
                raise RuntimeError(f"agentic semantic entity mapping failed: {details}")
            if coverage_matrix.get("summary", {}).get("missing_column_count", 0) and (gap_errors or not generated_tasks):
                details = "; ".join(gap_errors[:3]) or "no generated self-play gap tasks"
                raise RuntimeError(f"agentic self-play gap planning failed: {details}")

        document = {
            "version": 1,
            "artifact_type": "semantic_coverage",
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "scope": {"catalog": catalog, "database": database, "schema": schema},
            "summary": {
                "entity_count": len(entity_map.get("entities", {})),
                "table_count": len(table_columns),
                "column_count": sum(len(columns) for columns in table_columns.values()),
                "covered_table_count": coverage_matrix.get("summary", {}).get("covered_table_count", 0),
                "covered_column_count": coverage_matrix.get("summary", {}).get("covered_column_count", 0),
                "missing_column_count": coverage_matrix.get("summary", {}).get("missing_column_count", 0),
                "missing_entity_count": coverage_matrix.get("summary", {}).get("missing_entity_count", 0),
                "self_play_gap_task_count": len(gap_plan.get("tasks", {})),
                "generation_error_count": len(entity_errors) + len(gap_errors),
            },
            "entity_map": {
                **entity_map,
                "generation_errors": entity_errors,
            },
            "coverage_matrix": coverage_matrix,
            "self_play_gap_plan": {
                **gap_plan,
                "generation_errors": gap_errors,
            },
        }

        local_path = None
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            local_path = output_dir / "semantic_coverage.json"
            local_path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")

        object_key = None
        if object_store is not None:
            object_key = f"{object_prefix.strip('/')}/{run_id}/semantic_coverage.json"
            object_store.write_json(object_key, document)

        return SemanticCoverageBuildResult(document=document, local_path=local_path, object_key=object_key)

    def _build_entity_map(
        self,
        *,
        metadata_descriptions: dict[str, Any],
        schema_graph: dict[str, Any],
        corpus: NormalizedKnowledgeCorpus,
        table_columns: dict[str, list[str]],
    ) -> tuple[dict[str, Any], list[str]]:
        fallback = _entity_map_from_schema_graph(schema_graph=schema_graph, table_columns=table_columns)
        if self.generator is None:
            return fallback, []

        payload = {
            "metadata_descriptions": _compact_metadata(metadata_descriptions),
            "schema_graph_summary": _schema_graph_summary(schema_graph),
            "knowledge_summary": corpus.summary(),
        }
        prompt = _entity_prompt().replace("{{payload_json}}", json.dumps(payload, indent=2, sort_keys=True))
        try:
            text = self.generator.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "You learn schema business entities from descriptions and SQL evidence. "
                            "Return only valid JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
            )
            parsed = _loads_json_object(text)
            generated = _normalize_entity_map(parsed, table_columns=table_columns)
            if generated.get("entities"):
                return generated, []
            return fallback, ["entity generation returned no valid entities; used schema graph fallback"]
        except Exception as exc:  # noqa: BLE001
            return fallback, [f"entity generation failed: {type(exc).__name__}: {exc}"]

    def _build_gap_plan(
        self,
        *,
        entity_map: dict[str, Any],
        coverage_matrix: dict[str, Any],
        metadata_descriptions: dict[str, Any],
        table_columns: dict[str, list[str]],
    ) -> tuple[dict[str, Any], list[str]]:
        evidence = _gap_evidence(
            entity_map=entity_map,
            coverage_matrix=coverage_matrix,
            metadata_descriptions=metadata_descriptions,
            limit=self.gap_limit,
        )
        if not evidence["candidate_refs"]:
            return {"tasks": {}, "learning_mode": "not_needed"}, []
        fallback = _evidence_gap_plan(evidence=evidence, table_columns=table_columns)
        if self.generator is None:
            return fallback, []

        prompt = _gap_prompt().replace("{{payload_json}}", json.dumps(evidence, indent=2, sort_keys=True))
        try:
            text = self.generator.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "You plan reviewable self-play tasks from schema coverage gaps. "
                            "Return only valid JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
            )
            parsed = _loads_json_object(text)
            generated = _normalize_gap_plan(parsed, table_columns=table_columns)
            if generated.get("tasks"):
                return generated, []
            return fallback, ["gap generation returned no valid tasks; used evidence-only fallback"]
        except Exception as exc:  # noqa: BLE001
            return fallback, [f"gap generation failed: {type(exc).__name__}: {exc}"]


def _entity_prompt() -> str:
    return """Create a compact business entity map for an analytics schema.

Requirements:
- Return only valid JSON.
- Use only supplied table and column references.
- Do not invent business terms, tables, columns, metrics, or values.
- Keep entities useful for NL-to-SQL retrieval and self-play coverage.
- Mark ambiguous terms or competing interpretations as ambiguity_triggers.

JSON shape:
{
  "entities": [
    {
      "entity_id": "stable readable id",
      "name": "business entity name",
      "description": "short description",
      "aliases": ["optional synonyms"],
      "tables": ["known_table"],
      "columns": ["known_table.known_column"],
      "business_terms": ["terms users may say"],
      "analysis_patterns": ["kinds of questions this entity supports"],
      "ambiguity_triggers": [
        {"term": "term", "reason": "why it may need clarification"}
      ],
      "open_questions": [
        {"question": "question for analyst", "reason": "why it matters"}
      ]
    }
  ]
}

Evidence:
{{payload_json}}
"""


def _gap_prompt() -> str:
    return """Create reviewable self-play tasks from schema coverage gaps.

Requirements:
- Return only valid JSON.
- Use only supplied target_refs, tables, and columns.
- A task should close a meaningful NL-to-SQL coverage gap.
- Keep questions concrete enough to be validated with SQL later.
- Do not invent business definitions or filter values.
- If a gap is semantically unsafe, create an open question instead of a task.

JSON shape:
{
  "tasks": [
    {
      "canonical_question": "natural language task",
      "target_refs": ["table_or_table.column"],
      "required_tables": ["known_table"],
      "required_columns": ["known_table.known_column"],
      "rationale": "why this task improves coverage",
      "sql_intent": {
        "measure": "",
        "grain": "",
        "filters": [],
        "dimensions": [],
        "time_window": "",
        "exclusions": []
      },
      "validation_checks": ["what must be true before promotion"],
      "ambiguity_notes": [],
      "open_questions": []
    }
  ]
}

Coverage evidence:
{{payload_json}}
"""


def _coverage_matrix(
    *,
    corpus: NormalizedKnowledgeCorpus,
    entity_map: dict[str, Any],
    table_columns: dict[str, list[str]],
) -> dict[str, Any]:
    case_by_id = {case.case_id: case for case in corpus.cases}
    table_usage: dict[str, set[str]] = {table: set() for table in table_columns}
    column_usage: dict[str, set[str]] = {
        f"{table}.{column}": set()
        for table, columns in table_columns.items()
        for column in columns
    }
    for case in corpus.cases:
        for table in case.tables:
            if table in table_usage:
                table_usage[table].add(case.case_id)
        for column_ref in case.columns:
            if column_ref in column_usage:
                column_usage[column_ref].add(case.case_id)

    table_items = {
        table: _coverage_item(ref=table, kind="table", case_ids=case_ids, case_by_id=case_by_id)
        for table, case_ids in sorted(table_usage.items())
    }
    column_items = {
        column_ref: _coverage_item(ref=column_ref, kind="column", case_ids=case_ids, case_by_id=case_by_id)
        for column_ref, case_ids in sorted(column_usage.items())
    }
    entity_items = {}
    for entity_id, entity in sorted(_dict(entity_map.get("entities")).items()):
        refs = set(_strings(entity.get("tables"))) | set(_strings(entity.get("columns")))
        entity_case_ids: set[str] = set()
        for ref in refs:
            if ref in table_usage:
                entity_case_ids.update(table_usage[ref])
            if ref in column_usage:
                entity_case_ids.update(column_usage[ref])
        entity_items[entity_id] = _coverage_item(
            ref=entity_id,
            kind="entity",
            case_ids=entity_case_ids,
            case_by_id=case_by_id,
        )

    missing_columns = sorted(ref for ref, item in column_items.items() if not item["covered"])
    missing_tables = sorted(ref for ref, item in table_items.items() if not item["covered"])
    missing_entities = sorted(ref for ref, item in entity_items.items() if not item["covered"])
    return {
        "summary": {
            "source_case_count": len(corpus.cases),
            "table_count": len(table_items),
            "column_count": len(column_items),
            "entity_count": len(entity_items),
            "covered_table_count": len(table_items) - len(missing_tables),
            "covered_column_count": len(column_items) - len(missing_columns),
            "covered_entity_count": len(entity_items) - len(missing_entities),
            "missing_table_count": len(missing_tables),
            "missing_column_count": len(missing_columns),
            "missing_entity_count": len(missing_entities),
        },
        "tables": table_items,
        "columns": column_items,
        "entities": entity_items,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "missing_entities": missing_entities,
    }


def _coverage_item(
    *,
    ref: str,
    kind: str,
    case_ids: set[str],
    case_by_id: dict[str, NormalizedKnowledgeCase],
) -> dict[str, Any]:
    source_counts: dict[str, int] = {}
    trust_counts: dict[str, int] = {}
    examples = []
    for case_id in sorted(case_ids):
        case = case_by_id.get(case_id)
        if case is None:
            continue
        source_counts[case.source_type.value] = source_counts.get(case.source_type.value, 0) + 1
        trust_counts[case.trust_level.value] = trust_counts.get(case.trust_level.value, 0) + 1
        if len(examples) < 5:
            examples.append(
                {
                    "case_id": case.case_id,
                    "source_type": case.source_type.value,
                    "trust_level": case.trust_level.value,
                    "question": case.question,
                }
            )
    return {
        "ref": ref,
        "kind": kind,
        "covered": bool(case_ids),
        "source_case_count": len(case_ids),
        "source_counts": dict(sorted(source_counts.items())),
        "trust_counts": dict(sorted(trust_counts.items())),
        "examples": examples,
    }


def _gap_evidence(
    *,
    entity_map: dict[str, Any],
    coverage_matrix: dict[str, Any],
    metadata_descriptions: dict[str, Any],
    limit: int,
) -> dict[str, Any]:
    candidate_refs = list(coverage_matrix.get("missing_entities", [])) + list(coverage_matrix.get("missing_columns", []))
    candidate_refs = candidate_refs[: max(0, limit)]
    descriptions = {}
    for ref in candidate_refs:
        descriptions[ref] = _description_for_ref(metadata_descriptions, entity_map, ref)
    return {
        "coverage_summary": coverage_matrix.get("summary", {}),
        "candidate_refs": candidate_refs,
        "candidate_descriptions": descriptions,
        "entities": {
            entity_id: {
                "name": entity.get("name"),
                "description": entity.get("description"),
                "tables": entity.get("tables", []),
                "columns": entity.get("columns", [])[:20],
                "business_terms": entity.get("business_terms", []),
                "ambiguity_triggers": entity.get("ambiguity_triggers", []),
            }
            for entity_id, entity in _dict(entity_map.get("entities")).items()
        },
    }


def _evidence_gap_plan(*, evidence: dict[str, Any], table_columns: dict[str, list[str]]) -> dict[str, Any]:
    tasks = {}
    for ref in _strings(evidence.get("candidate_refs")):
        if ref not in table_columns and ref not in _all_column_refs(table_columns):
            continue
        required_tables = [ref] if ref in table_columns else [ref.split(".", 1)[0]]
        required_columns = [] if ref in table_columns else [ref]
        task = {
            "canonical_question": f"Create a reviewed SQL pattern that covers {ref}.",
            "target_refs": [ref],
            "required_tables": required_tables,
            "required_columns": required_columns,
            "rationale": "This reference has no current SQL-bearing coverage.",
            "sql_intent": {
                "measure": "",
                "grain": "",
                "filters": [],
                "dimensions": [],
                "time_window": "",
                "exclusions": [],
            },
            "validation_checks": ["SQL must dry-run successfully.", "SQL must use only required schema references."],
            "ambiguity_notes": [],
            "open_questions": [],
            "review_status": "needs_review",
            "learning_mode": "evidence_only",
        }
        tasks[_gap_id(task["canonical_question"], [ref])] = task
    return {"tasks": dict(sorted(tasks.items())), "learning_mode": "evidence_only"}


def _normalize_entity_map(payload: dict[str, Any], *, table_columns: dict[str, list[str]]) -> dict[str, Any]:
    valid_columns = _all_column_refs(table_columns)
    entities = {}
    raw_entities = payload.get("entities")
    if isinstance(raw_entities, dict):
        iterable = raw_entities.values()
    elif isinstance(raw_entities, list):
        iterable = raw_entities
    else:
        iterable = []
    for item in iterable:
        if not isinstance(item, dict):
            continue
        tables = sorted({table for table in _strings(item.get("tables")) if table in table_columns})
        columns = sorted({column for column in _strings(item.get("columns")) if column in valid_columns})
        if tables:
            columns = sorted(set(columns) | {f"{table}.{column}" for table in tables for column in table_columns[table]})
        if not tables and columns:
            tables = sorted({column.split(".", 1)[0] for column in columns})
        if not tables and not columns:
            continue
        entity_id = _safe_entity_id(_one_line(item.get("entity_id")) or _one_line(item.get("id")) or item.get("name"))
        entities[entity_id] = {
            "entity_id": entity_id,
            "name": _one_line(item.get("name")) or entity_id.removeprefix("entity:"),
            "description": _one_line(item.get("description")),
            "aliases": _strings(item.get("aliases"))[:12],
            "tables": tables,
            "columns": columns,
            "business_terms": _strings(item.get("business_terms"))[:24],
            "analysis_patterns": _strings(item.get("analysis_patterns"))[:12],
            "ambiguity_triggers": _dict_list(item.get("ambiguity_triggers"))[:12],
            "open_questions": _dict_list(item.get("open_questions"))[:12],
            "review_status": _one_line(item.get("review_status")) or "needs_review",
            "learning_mode": "generated",
        }
    return {"entities": dict(sorted(entities.items())), "learning_mode": "generated"}


def _normalize_gap_plan(payload: dict[str, Any], *, table_columns: dict[str, list[str]]) -> dict[str, Any]:
    valid_columns = _all_column_refs(table_columns)
    raw_tasks = payload.get("tasks")
    if isinstance(raw_tasks, dict):
        iterable = raw_tasks.values()
    elif isinstance(raw_tasks, list):
        iterable = raw_tasks
    else:
        iterable = []
    tasks = {}
    for item in iterable:
        if not isinstance(item, dict):
            continue
        target_refs = sorted(
            {
                ref
                for ref in _strings(item.get("target_refs"))
                if ref in table_columns or ref in valid_columns
            }
        )
        required_tables = sorted({table for table in _strings(item.get("required_tables")) if table in table_columns})
        required_columns = sorted({column for column in _strings(item.get("required_columns")) if column in valid_columns})
        for ref in target_refs:
            if ref in table_columns:
                required_tables.append(ref)
            elif "." in ref:
                required_columns.append(ref)
                required_tables.append(ref.split(".", 1)[0])
        required_tables = sorted(set(required_tables))
        required_columns = sorted(set(required_columns))
        if not target_refs and not required_columns and not required_tables:
            continue
        question = _one_line(item.get("canonical_question")) or _one_line(item.get("question"))
        if not question:
            continue
        task = {
            "canonical_question": question,
            "target_refs": target_refs or required_columns or required_tables,
            "required_tables": required_tables,
            "required_columns": required_columns,
            "rationale": _one_line(item.get("rationale")),
            "sql_intent": _dict(item.get("sql_intent")),
            "validation_checks": _strings(item.get("validation_checks"))[:12],
            "ambiguity_notes": _strings(item.get("ambiguity_notes"))[:12],
            "open_questions": _dict_list(item.get("open_questions"))[:12],
            "review_status": "needs_review",
            "learning_mode": "generated",
        }
        tasks[_gap_id(question, task["target_refs"])] = task
    return {"tasks": dict(sorted(tasks.items())), "learning_mode": "generated"}


def _entity_map_from_schema_graph(*, schema_graph: dict[str, Any], table_columns: dict[str, list[str]]) -> dict[str, Any]:
    nodes = {str(node.get("id")): node for node in _list(schema_graph.get("nodes")) if isinstance(node, dict)}
    children: dict[str, list[str]] = defaultdict(list)
    for edge in _list(schema_graph.get("edges")):
        if not isinstance(edge, dict) or edge.get("kind") != "contains":
            continue
        children[str(edge.get("from_node"))].append(str(edge.get("to_node")))

    entities = {}
    for node_id, node in sorted(nodes.items()):
        if node.get("kind") != "entity":
            continue
        table_ids = [child for child in children.get(node_id, []) if child.startswith("table:")]
        tables = sorted(table_id.removeprefix("table:") for table_id in table_ids if table_id.removeprefix("table:") in table_columns)
        columns = sorted(f"{table}.{column}" for table in tables for column in table_columns.get(table, []))
        entities[node_id] = {
            "entity_id": node_id,
            "name": _one_line(node.get("name")) or node_id.removeprefix("entity:"),
            "description": _one_line(node.get("description")),
            "aliases": _strings(node.get("aliases")),
            "tables": tables,
            "columns": columns,
            "business_terms": [],
            "analysis_patterns": [],
            "ambiguity_triggers": [],
            "open_questions": [],
            "review_status": "needs_review",
            "learning_mode": "schema_graph_fallback",
        }
    if not entities:
        for table, columns in sorted(table_columns.items()):
            entity_id = f"entity:{_slug(table)}"
            entities[entity_id] = {
                "entity_id": entity_id,
                "name": table,
                "description": "",
                "aliases": [],
                "tables": [table],
                "columns": [f"{table}.{column}" for column in columns],
                "business_terms": [],
                "analysis_patterns": [],
                "ambiguity_triggers": [],
                "open_questions": [],
                "review_status": "needs_review",
                "learning_mode": "table_fallback",
            }
    return {"entities": dict(sorted(entities.items())), "learning_mode": "schema_graph_fallback"}


def _compact_metadata(metadata_descriptions: dict[str, Any]) -> dict[str, Any]:
    tables = {}
    for table, description in _dict(metadata_descriptions.get("tables")).items():
        tables[table] = {
            "short_description": _one_line(_dict(description).get("short_description")),
            "long_description": _one_line(_dict(description).get("long_description")),
            "columns": {
                column: {
                    "short_description": _one_line(_dict(column_description).get("short_description")),
                    "long_description": _one_line(_dict(column_description).get("long_description")),
                }
                for column, column_description in _dict(_dict(metadata_descriptions.get("columns")).get(table)).items()
            },
        }
    return {"tables": tables}


def _schema_graph_summary(schema_graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": node.get("id"),
                "kind": node.get("kind"),
                "name": node.get("name"),
                "sql_ref": node.get("sql_ref"),
                "description": node.get("description"),
            }
            for node in _list(schema_graph.get("nodes"))
            if isinstance(node, dict) and node.get("kind") in {"domain", "entity", "table"}
        ]
    }


def _description_for_ref(
    metadata_descriptions: dict[str, Any],
    entity_map: dict[str, Any],
    ref: str,
) -> str:
    if ref.startswith("entity:"):
        return _one_line(_dict(_dict(entity_map.get("entities")).get(ref)).get("description"))
    if "." in ref:
        table, column = ref.split(".", 1)
        return _one_line(
            _dict(_dict(_dict(metadata_descriptions.get("columns")).get(table)).get(column)).get("long_description")
            or _dict(_dict(_dict(metadata_descriptions.get("columns")).get(table)).get(column)).get("short_description")
        )
    return _one_line(
        _dict(_dict(metadata_descriptions.get("tables")).get(ref)).get("long_description")
        or _dict(_dict(metadata_descriptions.get("tables")).get(ref)).get("short_description")
    )


def _table_columns_from_metadata(metadata_descriptions: dict[str, Any]) -> dict[str, list[str]]:
    columns = _dict(metadata_descriptions.get("columns"))
    return {
        str(table): sorted(str(column) for column in _dict(table_columns))
        for table, table_columns in columns.items()
    }


def _all_column_refs(table_columns: dict[str, list[str]]) -> set[str]:
    return {f"{table}.{column}" for table, columns in table_columns.items() for column in columns}


def _gap_id(question: str, refs: list[str]) -> str:
    seed = question + "|" + "|".join(sorted(refs))
    return "gap:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:14]


def _safe_entity_id(raw: Any) -> str:
    value = _one_line(raw)
    if value.startswith("entity:"):
        value = value.split(":", 1)[1]
    return f"entity:{_slug(value or 'entity')}"


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_") or "item"


def _loads_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
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
        raise ValueError("expected JSON object")
    return value


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


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
