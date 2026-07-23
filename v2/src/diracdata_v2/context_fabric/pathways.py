"""Build semantic pathways from normalized knowledge cases."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from diracdata_v2.context_fabric.contracts import (
    NormalizedKnowledgeCase,
    NormalizedKnowledgeCorpus,
)


class SemanticPathwayGenerator(Protocol):
    """LLM adapter used to translate SQL cases into semantic pathways."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


@dataclass(frozen=True)
class SemanticPathway:
    pathway_id: str
    canonical_question: str
    intent_signature: dict[str, Any]
    sql_skeleton: str
    slot_bindings: dict[str, str] = field(default_factory=dict)
    tables: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    join_paths: tuple[dict[str, Any], ...] = ()
    assertion_ids: tuple[str, ...] = ()
    source_case_ids: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    trust_level: str = "observed"
    review_status: str = ""
    learning_mode: str = "passthrough"
    assumptions: tuple[str, ...] = ()
    open_questions: tuple[dict[str, Any], ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "pathway_id": self.pathway_id,
            "canonical_question": self.canonical_question,
            "intent_signature": dict(self.intent_signature),
            "sql_skeleton": self.sql_skeleton,
            "slot_bindings": dict(self.slot_bindings),
            "tables": list(self.tables),
            "columns": list(self.columns),
            "join_paths": [dict(item) for item in self.join_paths],
            "assertion_ids": list(self.assertion_ids),
            "source_case_ids": list(self.source_case_ids),
            "source_types": list(self.source_types),
            "trust_level": self.trust_level,
            "review_status": self.review_status,
            "learning_mode": self.learning_mode,
            "assumptions": list(self.assumptions),
            "open_questions": [dict(item) for item in self.open_questions],
            "evidence": [dict(item) for item in self.evidence],
        }


@dataclass(frozen=True)
class SemanticPathwayBuildResult:
    document: dict[str, Any]
    pathways: tuple[SemanticPathway, ...]
    local_path: Path | None = None
    object_key: str | None = None


class SemanticPathwayBuilder:
    """Create reusable semantic pathways from normalized cases.

    When a generator is supplied, it performs the semantic translation. Without
    a generator, the builder emits conservative passthrough pathways that
    preserve observed SQL, references, joins, and provenance without inventing
    intent semantics.
    """

    def __init__(
        self,
        *,
        generator: SemanticPathwayGenerator | None = None,
        batch_size: int = 20,
        limit: int | None = None,
    ) -> None:
        self.generator = generator
        self.batch_size = max(1, batch_size)
        self.limit = limit

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
    ) -> SemanticPathwayBuildResult:
        source_cases = list(corpus.cases)
        if self.limit is not None:
            source_cases = source_cases[: max(0, self.limit)]

        pathways = self._build_pathways(
            cases=source_cases,
            metadata_descriptions=metadata_descriptions,
        )
        document = _document(
            pathways=pathways,
            catalog=catalog,
            database=database,
            schema=schema,
            run_id=run_id,
            source_case_count=len(source_cases),
        )

        local_path = None
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            local_path = output_dir / "semantic_pathways.json"
            local_path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")

        object_key = None
        if object_store is not None:
            object_key = f"{object_prefix.strip('/')}/{run_id}/semantic_pathways.json"
            object_store.write_json(object_key, document)

        return SemanticPathwayBuildResult(
            document=document,
            pathways=tuple(pathways),
            local_path=local_path,
            object_key=object_key,
        )

    def _build_pathways(
        self,
        *,
        cases: list[NormalizedKnowledgeCase],
        metadata_descriptions: dict[str, Any],
    ) -> list[SemanticPathway]:
        if not cases:
            return []
        if self.generator is None:
            return [_passthrough_pathway(case) for case in cases]

        generated: dict[str, SemanticPathway] = {}
        for batch in _batches(cases, self.batch_size):
            prompt = _pathway_prompt().replace(
                "{{payload_json}}",
                json.dumps(
                    {
                        "cases": [_case_payload(case, metadata_descriptions=metadata_descriptions) for case in batch],
                    },
                    indent=2,
                    sort_keys=True,
                ),
            )
            try:
                text = self.generator.complete(
                    [
                        {
                            "role": "system",
                            "content": (
                                "You convert observed SQL cases into compact, generic semantic pathways. "
                                "Return only valid JSON."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ]
                )
                for pathway in _parse_generated_pathways(text, cases=batch):
                    generated[pathway.pathway_id] = pathway
            except Exception:  # noqa: BLE001
                continue

        covered_case_ids = {
            source_case_id
            for pathway in generated.values()
            for source_case_id in pathway.source_case_ids
        }
        for case in cases:
            if case.case_id not in covered_case_ids:
                pathway = _passthrough_pathway(case)
                generated[pathway.pathway_id] = pathway
        return sorted(generated.values(), key=lambda item: item.pathway_id)


def _document(
    *,
    pathways: list[SemanticPathway],
    catalog: str,
    database: str,
    schema: str,
    run_id: str,
    source_case_count: int,
) -> dict[str, Any]:
    trust_counts: dict[str, int] = {}
    mode_counts: dict[str, int] = {}
    for pathway in pathways:
        trust_counts[pathway.trust_level] = trust_counts.get(pathway.trust_level, 0) + 1
        mode_counts[pathway.learning_mode] = mode_counts.get(pathway.learning_mode, 0) + 1
    return {
        "version": 1,
        "artifact_type": "semantic_pathway_library",
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "scope": {"catalog": catalog, "database": database, "schema": schema},
        "summary": {
            "source_case_count": source_case_count,
            "pathway_count": len(pathways),
            "trust_counts": dict(sorted(trust_counts.items())),
            "learning_mode_counts": dict(sorted(mode_counts.items())),
        },
        "pathways": {
            pathway.pathway_id: pathway.to_dict()
            for pathway in pathways
        },
    }


def _passthrough_pathway(case: NormalizedKnowledgeCase) -> SemanticPathway:
    question = case.question or f"Observed SQL pattern using {', '.join(case.tables)}"
    return SemanticPathway(
        pathway_id=_pathway_id([case.case_id], question + case.sql),
        canonical_question=question,
        intent_signature={
            "measure": "",
            "grain": "",
            "filters": [],
            "dimensions": [],
            "time_window": "",
            "exclusions": [],
        },
        sql_skeleton=case.sql,
        tables=case.tables,
        columns=case.columns,
        join_paths=case.join_edges,
        source_case_ids=(case.case_id,),
        source_types=(case.source_type.value,),
        trust_level=case.trust_level.value,
        review_status=case.review_status,
        learning_mode="passthrough",
        evidence=(
            {
                "source_case_id": case.case_id,
                "source_type": case.source_type.value,
                "trust_level": case.trust_level.value,
            },
        ),
    )


def _case_payload(case: NormalizedKnowledgeCase, *, metadata_descriptions: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "source_type": case.source_type.value,
        "trust_level": case.trust_level.value,
        "review_status": case.review_status,
        "question": case.question,
        "sql": case.sql,
        "tables": list(case.tables),
        "columns": list(case.columns),
        "join_edges": [dict(item) for item in case.join_edges],
        "schema_context": _schema_context(case=case, metadata_descriptions=metadata_descriptions),
    }


def _schema_context(*, case: NormalizedKnowledgeCase, metadata_descriptions: dict[str, Any]) -> dict[str, Any]:
    table_descriptions = metadata_descriptions.get("tables", {})
    column_descriptions = metadata_descriptions.get("columns", {})
    context = {"tables": {}, "columns": {}}
    if not isinstance(table_descriptions, dict) or not isinstance(column_descriptions, dict):
        return context
    for table in case.tables:
        value = table_descriptions.get(table)
        if value is not None:
            context["tables"][table] = _description(value)
    for column_ref in case.columns:
        if "." not in column_ref:
            continue
        table, column = column_ref.split(".", 1)
        value = column_descriptions.get(table, {})
        if isinstance(value, dict) and column in value:
            context["columns"][column_ref] = _description(value[column])
    return context


def _description(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(
            str(value.get(key) or "")
            for key in ("short_description", "long_description", "description")
            if value.get(key)
        )
    return str(value or "")


def _parse_generated_pathways(text: str, *, cases: list[NormalizedKnowledgeCase]) -> list[SemanticPathway]:
    payload = _json_object(text)
    raw_pathways = payload.get("pathways", [])
    if not isinstance(raw_pathways, list):
        return []
    cases_by_id = {case.case_id: case for case in cases}
    output = []
    for raw in raw_pathways:
        if not isinstance(raw, dict):
            continue
        pathway = _normalize_generated_pathway(raw, cases_by_id=cases_by_id)
        if pathway is not None:
            output.append(pathway)
    return output


def _normalize_generated_pathway(
    value: dict[str, Any],
    *,
    cases_by_id: dict[str, NormalizedKnowledgeCase],
) -> SemanticPathway | None:
    source_ids = tuple(
        item
        for item in _strings(value.get("source_case_ids"))
        if item in cases_by_id
    )
    if not source_ids:
        return None
    source_cases = [cases_by_id[source_id] for source_id in source_ids]
    valid_tables = sorted({table for case in source_cases for table in case.tables})
    valid_columns = sorted({column for case in source_cases for column in case.columns})
    generated_tables = _valid_subset(_strings(value.get("tables")), valid_tables) or valid_tables
    generated_columns = _valid_subset(_strings(value.get("columns")), valid_columns) or valid_columns
    slot_bindings = _slot_bindings(value.get("slot_bindings"), valid_columns=generated_columns)
    joins = _generated_joins(value.get("join_paths"), source_cases=source_cases)
    trust_level = _strongest_trust(source_cases)
    canonical = _one_line(value.get("canonical_question")) or source_cases[0].question
    sql_skeleton = _one_line(value.get("sql_skeleton")) or source_cases[0].sql
    return SemanticPathway(
        pathway_id=_pathway_id(source_ids, canonical + sql_skeleton),
        canonical_question=canonical,
        intent_signature=_intent_signature(value.get("intent_signature")),
        sql_skeleton=sql_skeleton,
        slot_bindings=slot_bindings,
        tables=tuple(generated_tables),
        columns=tuple(generated_columns),
        join_paths=tuple(joins),
        assertion_ids=tuple(_strings(value.get("assertion_ids"))),
        source_case_ids=source_ids,
        source_types=tuple(sorted({case.source_type.value for case in source_cases})),
        trust_level=trust_level,
        review_status=_review_status(source_cases),
        learning_mode="generated",
        assumptions=tuple(_strings(value.get("assumptions"))),
        open_questions=tuple(_dicts(value.get("open_questions"))),
        evidence=tuple(
            {
                "source_case_id": case.case_id,
                "source_type": case.source_type.value,
                "trust_level": case.trust_level.value,
            }
            for case in source_cases
        ),
    )


def _intent_signature(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "measure": _one_line(source.get("measure")),
        "grain": _one_line(source.get("grain")),
        "filters": _strings(source.get("filters")),
        "dimensions": _strings(source.get("dimensions")),
        "time_window": _one_line(source.get("time_window")),
        "exclusions": _strings(source.get("exclusions")),
    }


def _slot_bindings(value: Any, *, valid_columns: list[str]) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    valid = set(valid_columns)
    output = {}
    for slot, column in value.items():
        clean_slot = _one_line(slot)
        clean_column = _one_line(column)
        if clean_slot and clean_column in valid:
            output[clean_slot] = clean_column
    return dict(sorted(output.items()))


def _generated_joins(value: Any, *, source_cases: list[NormalizedKnowledgeCase]) -> list[dict[str, Any]]:
    observed = {
        str(edge.get("sql_condition") or ""): dict(edge)
        for case in source_cases
        for edge in case.join_edges
        if edge.get("sql_condition")
    }
    if not isinstance(value, list):
        return list(observed.values())
    output = []
    for item in value:
        if not isinstance(item, dict):
            continue
        condition = _one_line(item.get("sql_condition"))
        if condition in observed:
            output.append(observed[condition])
    return output or list(observed.values())


def _valid_subset(values: list[str], valid_values: list[str]) -> tuple[str, ...]:
    valid = set(valid_values)
    return tuple(sorted({value for value in values if value in valid}))


def _strongest_trust(cases: list[NormalizedKnowledgeCase]) -> str:
    order = {
        "gold": 0,
        "approved": 1,
        "observed": 2,
        "synthetic": 3,
        "needs_review": 4,
    }
    values = sorted((case.trust_level.value for case in cases), key=lambda item: order.get(item, 99))
    return values[0] if values else "observed"


def _review_status(cases: list[NormalizedKnowledgeCase]) -> str:
    statuses = sorted({case.review_status for case in cases if case.review_status})
    if "approved" in statuses:
        return "approved"
    return statuses[0] if statuses else ""


def _pathway_id(source_case_ids: tuple[str, ...] | list[str], text: str) -> str:
    raw = "|".join(sorted(source_case_ids)) + "|" + text
    return "pathway:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _pathway_prompt() -> str:
    return """Convert the SQL cases into compact semantic pathways for a generic NL-to-SQL context fabric.

Return only valid JSON:
{
  "pathways": [
    {
      "source_case_ids": ["case id"],
      "canonical_question": "business-friendly question",
      "intent_signature": {
        "measure": "requested metric or aggregate",
        "grain": "business grain",
        "filters": ["filter concept"],
        "dimensions": ["grouping concept"],
        "time_window": "time period or empty string",
        "exclusions": ["negative cohort or anti-filter"]
      },
      "sql_skeleton": "compact reusable SQL template or original SQL",
      "slot_bindings": {"business slot": "table.column"},
      "tables": ["table"],
      "columns": ["table.column"],
      "join_paths": [{"sql_condition": "table_a.col = table_b.col"}],
      "assumptions": [],
      "open_questions": []
    }
  ]
}

Rules:
- Use only the supplied cases and schema context.
- Do not invent tables, columns, values, joins, metrics, or business definitions.
- Keep slot bindings only to supplied table.column references.
- Preserve negative/exclusion logic in the intent signature.
- If a case has no natural-language question, create a cautious canonical question from the SQL and schema context.
- If unsure about business meaning, leave the field empty or add an open question.

PAYLOAD:
{{payload_json}}
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
        raise ValueError("semantic pathway response must be a JSON object")
    return value


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        clean = _one_line(value)
        return [clean] if clean else []
    if isinstance(value, list):
        return [_one_line(item) for item in value if _one_line(item)]
    return []


def _dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _batches(values: list[NormalizedKnowledgeCase], size: int) -> list[list[NormalizedKnowledgeCase]]:
    return [values[index : index + size] for index in range(0, len(values), size)]

