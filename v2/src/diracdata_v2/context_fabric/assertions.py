"""Semantic assertion learning for the context fabric."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from diracdata_v2.context_fabric.contracts import NormalizedKnowledgeCase, NormalizedKnowledgeCorpus
from diracdata_v2.tools.semantic_assertions import profile_sql
from diracdata_v2.tools.sql import validate_sql


class SemanticAssertionGenerator(Protocol):
    """LLM adapter used to generate SQL semantic assertions from evidence."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


ASSERTION_TYPES = {
    "grain",
    "clause_coverage",
    "value_grounding",
    "date_scope",
    "join_path",
    "fanout",
    "null_semantics",
    "negative_cohort",
    "data_availability",
    "evidence_bundle",
}
SEVERITIES = {"blocking", "warning", "info"}
REVIEW_STATUSES = {"observed", "needs_review"}


@dataclass(frozen=True)
class SemanticAssertionEvidence:
    case_id: str
    source_type: str
    trust_level: str
    question: str
    sql: str
    sql_profile: dict[str, Any] = field(default_factory=dict)
    pathways: tuple[dict[str, Any], ...] = ()
    nuances: tuple[dict[str, Any], ...] = ()
    join_edges: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "source_type": self.source_type,
            "trust_level": self.trust_level,
            "question": self.question,
            "sql": self.sql,
            "sql_profile": dict(self.sql_profile),
            "pathways": [dict(item) for item in self.pathways],
            "nuances": [dict(item) for item in self.nuances],
            "join_edges": [dict(item) for item in self.join_edges],
        }


@dataclass(frozen=True)
class SemanticAssertion:
    assertion_id: str
    assertion_type: str
    title: str
    statement: str
    severity: str
    check_guidance: str
    failure_mode: str = ""
    tables: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    join_edge_ids: tuple[str, ...] = ()
    pathway_ids: tuple[str, ...] = ()
    nuance_refs: tuple[str, ...] = ()
    source_case_ids: tuple[str, ...] = ()
    mutant_checks: tuple[dict[str, Any], ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    review_status: str = "needs_review"
    learning_mode: str = "generated"

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "assertion_type": self.assertion_type,
            "title": self.title,
            "statement": self.statement,
            "severity": self.severity,
            "check_guidance": self.check_guidance,
            "failure_mode": self.failure_mode,
            "tables": list(self.tables),
            "columns": list(self.columns),
            "join_edge_ids": list(self.join_edge_ids),
            "pathway_ids": list(self.pathway_ids),
            "nuance_refs": list(self.nuance_refs),
            "source_case_ids": list(self.source_case_ids),
            "mutant_checks": [dict(item) for item in self.mutant_checks],
            "evidence": dict(self.evidence),
            "review_status": self.review_status,
            "learning_mode": self.learning_mode,
        }


@dataclass(frozen=True)
class SemanticAssertionBuildResult:
    document: dict[str, Any]
    assertions: tuple[SemanticAssertion, ...]
    evidence: tuple[SemanticAssertionEvidence, ...]
    generation_errors: tuple[str, ...] = ()
    local_path: Path | None = None
    object_key: str | None = None


@dataclass(frozen=True)
class RetrievedSemanticAssertion:
    """A compact learned assertion selected for one steward review."""

    assertion_id: str
    score: float
    assertion: dict[str, Any]
    matched_tables: tuple[str, ...] = ()
    matched_columns: tuple[str, ...] = ()
    matched_join_edge_ids: tuple[str, ...] = ()

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "score": round(self.score, 4),
            "assertion_type": self.assertion.get("assertion_type"),
            "severity": self.assertion.get("severity"),
            "review_status": self.assertion.get("review_status"),
            "title": self.assertion.get("title"),
            "statement": self.assertion.get("statement"),
            "check_guidance": self.assertion.get("check_guidance"),
            "failure_mode": self.assertion.get("failure_mode"),
            "tables": list(_strings(self.assertion.get("tables"))),
            "columns": list(_strings(self.assertion.get("columns"))),
            "join_edge_ids": list(_strings(self.assertion.get("join_edge_ids"))),
            "source_case_ids": list(_strings(self.assertion.get("source_case_ids"))),
            "matched_tables": list(self.matched_tables),
            "matched_columns": list(self.matched_columns),
            "matched_join_edge_ids": list(self.matched_join_edge_ids),
            "mutant_checks": _compact_mutant_checks(self.assertion.get("mutant_checks")),
        }


class SemanticAssertionLibrary:
    """Runtime retrieval over learned semantic assertion artifacts."""

    def __init__(self, document: dict[str, Any], *, source_path: Path | None = None) -> None:
        self.document = document
        self.source_path = source_path
        raw_assertions = document.get("assertions", {})
        if isinstance(raw_assertions, dict):
            self.assertions = {str(key): value for key, value in raw_assertions.items() if isinstance(value, dict)}
        elif isinstance(raw_assertions, list):
            self.assertions = {
                str(item.get("assertion_id")): item
                for item in raw_assertions
                if isinstance(item, dict) and item.get("assertion_id")
            }
        else:
            self.assertions = {}

    @classmethod
    def from_file(cls, path: str | Path) -> "SemanticAssertionLibrary":
        resolved = Path(path)
        return cls(json.loads(resolved.read_text(encoding="utf-8")), source_path=resolved)

    def retrieve(
        self,
        *,
        question: str = "",
        sql_profile: dict[str, Any] | None = None,
        max_assertions: int = 8,
    ) -> tuple[RetrievedSemanticAssertion, ...]:
        profile = sql_profile or {}
        profile_tables = set(_strings(profile.get("tables")))
        profile_columns = set(_strings(profile.get("columns")))
        profile_join_ids = {
            _join_edge_id(str(edge.get("left_column") or ""), str(edge.get("right_column") or ""))
            for edge in profile.get("join_edges", [])
            if isinstance(edge, dict) and (edge.get("left_column") or edge.get("right_column"))
        }
        query_tokens = _text_tokens(question)
        retrieved = []
        for assertion_id, assertion in self.assertions.items():
            item = self._score_assertion(
                assertion_id=assertion_id,
                assertion=assertion,
                profile_tables=profile_tables,
                profile_columns=profile_columns,
                profile_join_ids=profile_join_ids,
                query_tokens=query_tokens,
            )
            if item.score > 0:
                retrieved.append(item)
        retrieved.sort(key=lambda item: (-item.score, item.assertion_id))
        return tuple(retrieved[: max(0, max_assertions)])

    def _score_assertion(
        self,
        *,
        assertion_id: str,
        assertion: dict[str, Any],
        profile_tables: set[str],
        profile_columns: set[str],
        profile_join_ids: set[str],
        query_tokens: set[str],
    ) -> RetrievedSemanticAssertion:
        assertion_tables = set(_strings(assertion.get("tables")))
        assertion_columns = set(_strings(assertion.get("columns")))
        assertion_join_ids = set(_strings(assertion.get("join_edge_ids")))
        matched_tables = tuple(sorted(profile_tables & assertion_tables))
        matched_columns = tuple(sorted(profile_columns & assertion_columns))
        matched_join_ids = tuple(sorted(profile_join_ids & assertion_join_ids))
        score = len(matched_columns) * 6.0 + len(matched_join_ids) * 5.0 + len(matched_tables) * 2.0
        score += _severity_weight(assertion.get("severity"))
        if assertion.get("review_status") == "observed":
            score += 0.5
        searchable_text = " ".join(
            _one_line(assertion.get(key))
            for key in ("assertion_type", "title", "statement", "check_guidance", "failure_mode")
        )
        overlap = query_tokens & _text_tokens(searchable_text)
        if overlap:
            score += min(len(overlap), 6) * 0.25
        if not matched_tables and not matched_columns and not matched_join_ids:
            score = min(score, 1.0 if overlap else 0.0)
        return RetrievedSemanticAssertion(
            assertion_id=assertion_id,
            score=score,
            assertion=assertion,
            matched_tables=matched_tables,
            matched_columns=matched_columns,
            matched_join_edge_ids=matched_join_ids,
        )


class SemanticAssertionBuilder:
    """Generate reviewable semantic assertions from learned context evidence."""

    def __init__(
        self,
        *,
        generator: SemanticAssertionGenerator | None = None,
        limit: int | None = None,
        batch_size: int = 6,
        max_pathways_per_case: int = 3,
        max_nuances_per_case: int = 10,
        max_joins_per_case: int = 12,
        strict_agentic: bool = False,
    ) -> None:
        self.generator = generator
        self.limit = limit
        self.batch_size = max(1, batch_size)
        self.max_pathways_per_case = max(0, max_pathways_per_case)
        self.max_nuances_per_case = max(0, max_nuances_per_case)
        self.max_joins_per_case = max(0, max_joins_per_case)
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
        pathway_document: dict[str, Any] | None = None,
        nuance_document: dict[str, Any] | None = None,
        join_document: dict[str, Any] | None = None,
        output_dir: Path | None = None,
        object_store: Any | None = None,
        object_prefix: str = "v2/learning/artifacts",
    ) -> SemanticAssertionBuildResult:
        table_columns = _table_columns_from_metadata(metadata_descriptions)
        source_cases = list(corpus.cases)
        if self.limit is not None:
            source_cases = source_cases[: max(0, self.limit)]
        evidence = tuple(
            self._evidence_for_case(
                case=case,
                table_columns=table_columns,
                pathway_document=pathway_document or {},
                nuance_document=nuance_document or {},
                join_document=join_document or {},
            )
            for case in source_cases
        )
        assertions, generation_errors = self._build_assertions(evidence=evidence, table_columns=table_columns)
        if self.strict_agentic and self.generator is not None:
            generated_count = sum(1 for item in assertions if item.learning_mode == "generated")
            if generation_errors or generated_count == 0:
                details = "; ".join(generation_errors[:3]) or "no generated semantic assertions"
                raise RuntimeError(f"agentic semantic assertion generation failed: {details}")
        document = _document(
            assertions=assertions,
            evidence=evidence,
            generation_errors=generation_errors,
            catalog=catalog,
            database=database,
            schema=schema,
            run_id=run_id,
        )
        local_path = None
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            local_path = output_dir / "semantic_assertions.json"
            local_path.write_text(json.dumps(document, indent=2, sort_keys=True, default=str), encoding="utf-8")
        object_key = None
        if object_store is not None:
            object_key = f"{object_prefix.strip('/')}/{run_id}/semantic_assertions.json"
            object_store.write_json(object_key, document)
        return SemanticAssertionBuildResult(
            document=document,
            assertions=tuple(assertions),
            evidence=evidence,
            generation_errors=tuple(generation_errors),
            local_path=local_path,
            object_key=object_key,
        )

    def _evidence_for_case(
        self,
        *,
        case: NormalizedKnowledgeCase,
        table_columns: dict[str, list[str]],
        pathway_document: dict[str, Any],
        nuance_document: dict[str, Any],
        join_document: dict[str, Any],
    ) -> SemanticAssertionEvidence:
        sql_profile = profile_sql(
            sql=case.sql,
            table_columns=table_columns,
            available_tables=set(table_columns),
        )
        return SemanticAssertionEvidence(
            case_id=case.case_id,
            source_type=case.source_type.value,
            trust_level=case.trust_level.value,
            question=case.question,
            sql=case.sql,
            sql_profile=sql_profile,
            pathways=_matching_pathways(case=case, document=pathway_document, limit=self.max_pathways_per_case),
            nuances=_matching_nuances(case=case, document=nuance_document, limit=self.max_nuances_per_case),
            join_edges=_matching_join_edges(case=case, document=join_document, limit=self.max_joins_per_case),
        )

    def _build_assertions(
        self,
        *,
        evidence: tuple[SemanticAssertionEvidence, ...],
        table_columns: dict[str, list[str]],
    ) -> tuple[list[SemanticAssertion], list[str]]:
        if self.generator is None:
            return [_evidence_only_assertion(item) for item in evidence], []
        assertions: dict[str, SemanticAssertion] = {}
        errors = []
        for batch_index, batch in enumerate(_batches(list(evidence), self.batch_size), start=1):
            prompt = _assertion_prompt().replace(
                "{{assertion_evidence_json}}",
                json.dumps([item.to_dict() for item in batch], indent=2, sort_keys=True, default=str),
            )
            try:
                text = self.generator.complete(
                    [
                        {
                            "role": "system",
                            "content": (
                                "You generate SQL semantic assertions from supplied evidence. "
                                "Return only valid JSON."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ]
                )
                parsed = _parse_generated_assertions(
                    text,
                    source_evidence=batch,
                    table_columns=table_columns,
                )
                if not parsed:
                    errors.append(f"batch {batch_index} returned no parseable assertions")
                for assertion in parsed:
                    assertions[assertion.assertion_id] = assertion
            except Exception as exc:  # noqa: BLE001
                errors.append(f"batch {batch_index} failed: {type(exc).__name__}: {exc}")
        if not assertions:
            return [_evidence_only_assertion(item) for item in evidence], errors
        return sorted(assertions.values(), key=lambda item: item.assertion_id), errors


def _evidence_only_assertion(evidence: SemanticAssertionEvidence) -> SemanticAssertion:
    return SemanticAssertion(
        assertion_id=_assertion_id(
            {
                "type": "evidence_bundle",
                "source_case_ids": [evidence.case_id],
                "statement": "Evidence collected; semantic assertion was not generated.",
            }
        ),
        assertion_type="evidence_bundle",
        title="Evidence bundle requires semantic review",
        statement="Evidence collected; semantic assertion was not generated.",
        severity="info",
        check_guidance="Review the attached SQL profile, pathways, nuances, and joins before using this case as an assertion.",
        source_case_ids=(evidence.case_id,),
        evidence={"case": evidence.to_dict()},
        review_status="needs_review",
        learning_mode="evidence_only",
    )


def _parse_generated_assertions(
    text: str,
    *,
    source_evidence: list[SemanticAssertionEvidence],
    table_columns: dict[str, list[str]],
) -> list[SemanticAssertion]:
    payload = _json_object(text)
    raw_assertions = payload.get("assertions", [])
    if not isinstance(raw_assertions, list):
        return []
    source_by_case = {item.case_id: item for item in source_evidence}
    allowed_cases = set(source_by_case)
    allowed_tables = set(table_columns)
    allowed_columns = {
        f"{table}.{column}"
        for table, columns in table_columns.items()
        for column in columns
    }
    allowed_join_ids = {
        str(edge.get("edge_id"))
        for evidence in source_evidence
        for edge in evidence.join_edges
        if edge.get("edge_id")
    }
    allowed_pathway_ids = {
        str(pathway.get("pathway_id"))
        for evidence in source_evidence
        for pathway in evidence.pathways
        if pathway.get("pathway_id")
    }
    allowed_nuance_refs = {
        str(nuance.get("column_ref"))
        for evidence in source_evidence
        for nuance in evidence.nuances
        if nuance.get("column_ref")
    }
    assertions = []
    for raw in raw_assertions:
        if not isinstance(raw, dict):
            continue
        assertion = _normalize_assertion(
            raw,
            source_by_case=source_by_case,
            allowed_cases=allowed_cases,
            allowed_tables=allowed_tables,
            allowed_columns=allowed_columns,
            allowed_join_ids=allowed_join_ids,
            allowed_pathway_ids=allowed_pathway_ids,
            allowed_nuance_refs=allowed_nuance_refs,
            table_columns=table_columns,
        )
        if assertion is not None:
            assertions.append(assertion)
    return assertions


def _normalize_assertion(
    raw: dict[str, Any],
    *,
    source_by_case: dict[str, SemanticAssertionEvidence],
    allowed_cases: set[str],
    allowed_tables: set[str],
    allowed_columns: set[str],
    allowed_join_ids: set[str],
    allowed_pathway_ids: set[str],
    allowed_nuance_refs: set[str],
    table_columns: dict[str, list[str]],
) -> SemanticAssertion | None:
    assertion_type = _one_line(raw.get("assertion_type"))
    if assertion_type not in ASSERTION_TYPES:
        return None
    source_case_ids = tuple(_valid_refs(raw.get("source_case_ids"), allowed_cases))
    if not source_case_ids:
        return None
    statement = _one_line(raw.get("statement"))
    title = _one_line(raw.get("title"))
    check_guidance = _one_line(raw.get("check_guidance"))
    if not statement or not title or not check_guidance:
        return None
    tables = tuple(_valid_refs(raw.get("tables"), allowed_tables))
    columns = tuple(_valid_refs(raw.get("columns"), allowed_columns))
    join_edge_ids = tuple(_valid_refs(raw.get("join_edge_ids"), allowed_join_ids))
    pathway_ids = tuple(_valid_refs(raw.get("pathway_ids"), allowed_pathway_ids))
    nuance_refs = tuple(_valid_refs(raw.get("nuance_refs"), allowed_nuance_refs))
    evidence_cases = [source_by_case[case_id].to_dict() for case_id in source_case_ids if case_id in source_by_case]
    normalized = {
        "assertion_type": assertion_type,
        "title": title,
        "statement": statement,
        "severity": _severity(raw.get("severity")),
        "check_guidance": check_guidance,
        "failure_mode": _one_line(raw.get("failure_mode")),
        "tables": tables,
        "columns": columns,
        "join_edge_ids": join_edge_ids,
        "pathway_ids": pathway_ids,
        "nuance_refs": nuance_refs,
        "source_case_ids": source_case_ids,
    }
    return SemanticAssertion(
        assertion_id=_assertion_id(normalized),
        assertion_type=assertion_type,
        title=title,
        statement=statement,
        severity=normalized["severity"],
        check_guidance=check_guidance,
        failure_mode=normalized["failure_mode"],
        tables=tables,
        columns=columns,
        join_edge_ids=join_edge_ids,
        pathway_ids=pathway_ids,
        nuance_refs=nuance_refs,
        source_case_ids=source_case_ids,
        mutant_checks=tuple(
            _valid_mutants(
                raw.get("mutant_checks"),
                table_columns=table_columns,
                available_tables=set(table_columns),
            )
        ),
        evidence={"cases": evidence_cases},
        review_status=_review_status(raw.get("review_status")),
        learning_mode="generated",
    )


def _valid_mutants(
    value: Any,
    *,
    table_columns: dict[str, list[str]],
    available_tables: set[str],
) -> list[dict[str, Any]]:
    output = []
    if not isinstance(value, list):
        return output
    for item in value:
        if not isinstance(item, dict):
            continue
        mutation_type = _one_line(item.get("mutation_type"))
        expected_failure = _one_line(item.get("expected_failure"))
        mutant_sql = _one_line_sql(item.get("mutant_sql"))
        if not mutation_type or not expected_failure:
            continue
        payload = {
            "mutation_type": mutation_type,
            "expected_failure": expected_failure,
        }
        if mutant_sql:
            validation = validate_sql(mutant_sql, available_tables=available_tables)
            if validation.get("status") != "ok":
                continue
            profile = profile_sql(sql=mutant_sql, table_columns=table_columns, available_tables=available_tables)
            if profile.get("status") != "ok":
                continue
            payload["mutant_sql"] = mutant_sql
            payload["sql_profile"] = {
                "tables": profile.get("tables", []),
                "columns": profile.get("columns", []),
                "join_edges": profile.get("join_edges", []),
                "predicates": profile.get("predicates", []),
                "negative_constructs": profile.get("negative_constructs", []),
            }
        output.append(payload)
    return output


def _document(
    *,
    assertions: list[SemanticAssertion],
    evidence: tuple[SemanticAssertionEvidence, ...],
    generation_errors: list[str],
    catalog: str,
    database: str,
    schema: str,
    run_id: str,
) -> dict[str, Any]:
    return {
        "version": 1,
        "artifact_type": "semantic_assertion_library",
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "scope": {"catalog": catalog, "database": database, "schema": schema},
        "summary": {
            "assertion_count": len(assertions),
            "evidence_count": len(evidence),
            "assertion_type_counts": _counts(item.assertion_type for item in assertions),
            "severity_counts": _counts(item.severity for item in assertions),
            "review_counts": _counts(item.review_status for item in assertions),
            "learning_mode_counts": _counts(item.learning_mode for item in assertions),
            "generation_error_count": len(generation_errors),
        },
        "generation_errors": list(generation_errors),
        "evidence": {item.case_id: item.to_dict() for item in evidence},
        "assertions": {item.assertion_id: item.to_dict() for item in assertions},
        "indexes": _indexes(assertions),
    }


def _indexes(assertions: list[SemanticAssertion]) -> dict[str, Any]:
    by_type: dict[str, list[str]] = {}
    by_table: dict[str, list[str]] = {}
    by_column: dict[str, list[str]] = {}
    by_case: dict[str, list[str]] = {}
    for assertion in assertions:
        by_type.setdefault(assertion.assertion_type, []).append(assertion.assertion_id)
        for table in assertion.tables:
            by_table.setdefault(table, []).append(assertion.assertion_id)
        for column in assertion.columns:
            by_column.setdefault(column, []).append(assertion.assertion_id)
        for case_id in assertion.source_case_ids:
            by_case.setdefault(case_id, []).append(assertion.assertion_id)
    return {
        "assertions_by_type": _sorted_index(by_type),
        "assertions_by_table": _sorted_index(by_table),
        "assertions_by_column": _sorted_index(by_column),
        "assertions_by_source_case": _sorted_index(by_case),
    }


def _matching_pathways(
    *,
    case: NormalizedKnowledgeCase,
    document: dict[str, Any],
    limit: int,
) -> tuple[dict[str, Any], ...]:
    output = []
    for pathway in _dict_values(document.get("pathways")):
        source_case_ids = set(_strings(pathway.get("source_case_ids")))
        if case.case_id not in source_case_ids:
            continue
        output.append(_compact_pathway(pathway))
        if len(output) >= limit:
            break
    return tuple(output)


def _matching_nuances(
    *,
    case: NormalizedKnowledgeCase,
    document: dict[str, Any],
    limit: int,
) -> tuple[dict[str, Any], ...]:
    case_columns = set(case.columns)
    output = []
    for card in _dict_values(document.get("cards")):
        if str(card.get("column_ref") or "") not in case_columns:
            continue
        output.append(_compact_nuance(card))
        if len(output) >= limit:
            break
    return tuple(output)


def _matching_join_edges(
    *,
    case: NormalizedKnowledgeCase,
    document: dict[str, Any],
    limit: int,
) -> tuple[dict[str, Any], ...]:
    case_edge_ids = {
        _join_edge_id(str(edge.get("left_column") or ""), str(edge.get("right_column") or ""))
        for edge in case.join_edges
    }
    output = []
    for edge in _dict_values(document.get("join_edges")):
        edge_id = str(edge.get("edge_id") or "")
        if edge_id not in case_edge_ids:
            continue
        output.append(_compact_join(edge))
        if len(output) >= limit:
            break
    return tuple(output)


def _compact_pathway(pathway: dict[str, Any]) -> dict[str, Any]:
    return {
        "pathway_id": pathway.get("pathway_id"),
        "canonical_question": pathway.get("canonical_question"),
        "intent_signature": pathway.get("intent_signature", {}),
        "tables": pathway.get("tables", []),
        "columns": pathway.get("columns", []),
        "join_paths": pathway.get("join_paths", []),
        "trust_level": pathway.get("trust_level"),
        "review_status": pathway.get("review_status"),
    }


def _compact_nuance(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "column_ref": card.get("column_ref"),
        "role": card.get("role"),
        "null_semantics": card.get("null_semantics", {}),
        "default_behavior": card.get("default_behavior", {}),
        "ambiguity_triggers": card.get("ambiguity_triggers", []),
        "assertions": card.get("assertions", []),
        "review_status": card.get("review_status"),
    }


def _compact_join(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "edge_id": edge.get("edge_id"),
        "sql_condition": edge.get("sql_condition"),
        "relationship_type": edge.get("relationship_type"),
        "grain_effect": edge.get("grain_effect"),
        "evidence_type": edge.get("evidence_type"),
        "observed_count": edge.get("observed_count"),
        "review_status": edge.get("review_status"),
        "rationale": edge.get("rationale"),
    }


def _assertion_prompt() -> str:
    return """Generate SQL semantic assertions from evidence.

Return only valid JSON:
{
  "assertions": [
    {
      "assertion_type": "grain | clause_coverage | value_grounding | date_scope | join_path | fanout | null_semantics | negative_cohort | data_availability",
      "title": "short assertion name",
      "statement": "what must remain true for SQL to match the learned pattern",
      "severity": "blocking | warning | info",
      "check_guidance": "how a steward should verify this assertion from SQL profile, intent, and evidence",
      "failure_mode": "what SQL mistake this catches",
      "tables": ["known table names only"],
      "columns": ["known table.column refs only"],
      "join_edge_ids": ["known edge ids only"],
      "pathway_ids": ["known pathway ids only"],
      "nuance_refs": ["known column refs from nuance cards only"],
      "source_case_ids": ["source case ids from evidence only"],
      "mutant_checks": [
        {
          "mutation_type": "drop_filter | wrong_join | wrong_date_scope | remove_distinct | broaden_negative_scope | drop_null_handling",
          "mutant_sql": "optional read-only SQL that should fail this assertion",
          "expected_failure": "why this mutant should fail"
        }
      ],
      "review_status": "observed | needs_review"
    }
  ]
}

Rules:
- Use only supplied evidence. Do not invent tables, columns, joins, values, or business definitions.
- Assertions must be generic SQL-quality checks grounded in the source case.
- Do not turn source-case literal values into reusable defaults. If evidence has literals, describe them as examples and require the steward to match the current user intent plus verified column values.
- Prefer checks that catch semantic mistakes: wrong grain, missing clause, wrong date scope, unsafe join, ungrounded value, incorrect negative cohort, or NULL/default mismatch.
- Do not create universal business rules unless directly supported by evidence.
- Candidate-only joins and uncertain nuances should produce needs_review assertions or warnings.
- Mutant SQL is optional. If included, it must be read-only and use only known tables.
- Keep each assertion compact and actionable for a future steward.

EVIDENCE:
{{assertion_evidence_json}}
"""


def _table_columns_from_metadata(metadata: dict[str, Any]) -> dict[str, list[str]]:
    columns = metadata.get("columns", {})
    if not isinstance(columns, dict):
        return {}
    output = {}
    for table, table_columns in columns.items():
        if isinstance(table_columns, dict):
            output[str(table)] = sorted(str(column) for column in table_columns)
    return output


def _dict_values(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [dict(item) for item in value.values() if isinstance(item, dict)]
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _valid_refs(value: Any, allowed: set[str]) -> list[str]:
    return sorted(set(item for item in _strings(value) if item in allowed))


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_one_line(value)] if _one_line(value) else []
    if isinstance(value, list):
        return [_one_line(item) for item in value if _one_line(item)]
    if isinstance(value, tuple):
        return [_one_line(item) for item in value if _one_line(item)]
    return []


def _severity(value: Any) -> str:
    clean = _one_line(value)
    return clean if clean in SEVERITIES else "warning"


def _review_status(value: Any) -> str:
    clean = _one_line(value)
    if clean == "approved":
        return "observed"
    return clean if clean in REVIEW_STATUSES else "needs_review"


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _one_line_sql(value: Any) -> str:
    return _one_line(value).rstrip(";")


def _join_edge_id(left: str, right: str) -> str:
    ordered = tuple(sorted((left, right)))
    return f"join:{ordered[0]}:{ordered[1]}"


def _text_tokens(value: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9_]+", value or "") if len(token) > 1}


def _severity_weight(value: Any) -> float:
    severity = _one_line(value)
    if severity == "blocking":
        return 1.0
    if severity == "warning":
        return 0.5
    return 0.0


def _compact_mutant_checks(value: Any) -> list[dict[str, Any]]:
    output = []
    if not isinstance(value, list):
        return output
    for item in value[:3]:
        if not isinstance(item, dict):
            continue
        output.append(
            {
                "mutation_type": item.get("mutation_type"),
                "expected_failure": item.get("expected_failure"),
                "tables": ((item.get("sql_profile") or {}).get("tables") or [])[:8]
                if isinstance(item.get("sql_profile"), dict)
                else [],
                "columns": ((item.get("sql_profile") or {}).get("columns") or [])[:12]
                if isinstance(item.get("sql_profile"), dict)
                else [],
            }
        )
    return output


def _assertion_id(value: dict[str, Any]) -> str:
    digest = hashlib.sha1(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    return f"assertion:{digest}"


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
        raise ValueError("semantic assertion response must be a JSON object")
    return value


def _counts(values: Any) -> dict[str, int]:
    output: dict[str, int] = {}
    for value in values:
        output[str(value)] = output.get(str(value), 0) + 1
    return dict(sorted(output.items()))


def _sorted_index(values: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: sorted(set(items)) for key, items in sorted(values.items())}


def _batches(values: list[SemanticAssertionEvidence], size: int) -> list[list[SemanticAssertionEvidence]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
