"""Agentic column nuance learning for the context fabric."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from diracdata_v2.context_fabric.contracts import NormalizedKnowledgeCorpus
from diracdata_v2.query import DuckDBEngine


class ColumnNuanceGenerator(Protocol):
    """LLM adapter used to infer column nuance from compact evidence."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


@dataclass(frozen=True)
class ColumnEvidence:
    column_ref: str
    table_name: str
    column_name: str
    description: str = ""
    table_description: str = ""
    source_case_ids: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    trust_levels: tuple[str, ...] = ()
    usage_count: int = 0
    usage_examples: tuple[dict[str, Any], ...] = ()
    data_profile: dict[str, Any] = field(default_factory=dict)
    selection_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_ref": self.column_ref,
            "table_name": self.table_name,
            "column_name": self.column_name,
            "description": self.description,
            "table_description": self.table_description,
            "source_case_ids": list(self.source_case_ids),
            "source_types": list(self.source_types),
            "trust_levels": list(self.trust_levels),
            "usage_count": self.usage_count,
            "usage_examples": [dict(item) for item in self.usage_examples],
            "data_profile": dict(self.data_profile),
            "selection_reasons": list(self.selection_reasons),
        }


@dataclass(frozen=True)
class ColumnNuanceCard:
    column_ref: str
    role: str = "unknown"
    evidence_summary: str = ""
    value_semantics: tuple[dict[str, Any], ...] = ()
    null_semantics: dict[str, Any] = field(default_factory=dict)
    default_behavior: dict[str, Any] = field(default_factory=dict)
    ambiguity_triggers: tuple[dict[str, Any], ...] = ()
    open_questions: tuple[dict[str, Any], ...] = ()
    assertions: tuple[str, ...] = ()
    review_status: str = "needs_review"
    learning_mode: str = "generated"
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_ref": self.column_ref,
            "role": self.role,
            "evidence_summary": self.evidence_summary,
            "value_semantics": [dict(item) for item in self.value_semantics],
            "null_semantics": dict(self.null_semantics),
            "default_behavior": dict(self.default_behavior),
            "ambiguity_triggers": [dict(item) for item in self.ambiguity_triggers],
            "open_questions": [dict(item) for item in self.open_questions],
            "assertions": list(self.assertions),
            "review_status": self.review_status,
            "learning_mode": self.learning_mode,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class ColumnNuanceBuildResult:
    document: dict[str, Any]
    evidence: tuple[ColumnEvidence, ...]
    cards: tuple[ColumnNuanceCard, ...]
    generation_errors: tuple[str, ...] = ()
    local_path: Path | None = None
    object_key: str | None = None


class ColumnEvidenceProfiler:
    """Collect compact evidence for SQL-affecting column nuance learning."""

    def __init__(
        self,
        *,
        engine: DuckDBEngine | None = None,
        max_columns: int = 40,
        max_values: int = 20,
        low_cardinality_threshold: int = 50,
    ) -> None:
        self.engine = engine
        self.max_columns = max(1, max_columns)
        self.max_values = max(1, max_values)
        self.low_cardinality_threshold = max(1, low_cardinality_threshold)

    def profile(
        self,
        *,
        corpus: NormalizedKnowledgeCorpus,
        metadata_descriptions: dict[str, Any],
    ) -> tuple[ColumnEvidence, ...]:
        usage = _column_usage(corpus)
        selected = self._select_columns(usage)
        evidence = []
        for column_ref in selected:
            if "." not in column_ref:
                continue
            table, column = column_ref.split(".", 1)
            data_profile = self._profile_data(table=table, column=column)
            evidence.append(
                ColumnEvidence(
                    column_ref=column_ref,
                    table_name=table,
                    column_name=column,
                    description=_column_description(metadata_descriptions, table, column),
                    table_description=_table_description(metadata_descriptions, table),
                    source_case_ids=tuple(sorted(usage[column_ref]["case_ids"])),
                    source_types=tuple(sorted(usage[column_ref]["source_types"])),
                    trust_levels=tuple(sorted(usage[column_ref]["trust_levels"])),
                    usage_count=int(usage[column_ref]["count"]),
                    usage_examples=tuple(usage[column_ref]["examples"][:3]),
                    data_profile=data_profile,
                    selection_reasons=tuple(self._selection_reasons(usage[column_ref], data_profile)),
                )
            )
        return tuple(evidence)

    def _select_columns(self, usage: dict[str, dict[str, Any]]) -> list[str]:
        ranked = sorted(
            usage,
            key=lambda ref: (
                -int(usage[ref]["count"]),
                ref,
            ),
        )
        return ranked[: self.max_columns]

    def _selection_reasons(self, usage: dict[str, Any], data_profile: dict[str, Any]) -> list[str]:
        reasons = ["used_in_sql_sources"]
        if usage.get("trust_levels"):
            reasons.append("has_trusted_or_observed_source")
        if data_profile.get("status") == "ok":
            reasons.append("data_profile_available")
            distinct_count = _int(data_profile.get("distinct_count"))
            if 0 <= distinct_count <= self.low_cardinality_threshold:
                reasons.append("low_cardinality_candidate")
            if _float(data_profile.get("null_rate")) > 0:
                reasons.append("nullable_candidate")
        return reasons

    def _profile_data(self, *, table: str, column: str) -> dict[str, Any]:
        if self.engine is None:
            return {"status": "not_configured"}
        if table not in set(self.engine.list_tables()):
            return {"status": "missing_table"}
        if column not in set(self.engine.list_columns(table)):
            return {"status": "missing_column"}
        try:
            stats_sql = (
                f"SELECT COUNT(*) AS row_count, "
                f"COUNT({_identifier(column)}) AS non_null_count, "
                f"COUNT(DISTINCT {_identifier(column)}) AS distinct_count "
                f"FROM {_identifier(table)}"
            )
            stats_result = self.engine.query(stats_sql, max_rows=1)
            stats = dict(zip(stats_result.columns, stats_result.rows[0], strict=False)) if stats_result.rows else {}
            value_sql = (
                f"SELECT {_identifier(column)} AS value, COUNT(*) AS row_count "
                f"FROM {_identifier(table)} "
                f"WHERE {_identifier(column)} IS NOT NULL "
                f"GROUP BY 1 "
                f"ORDER BY row_count DESC, value "
                f"LIMIT {int(self.max_values)}"
            )
            value_result = self.engine.query(value_sql, max_rows=self.max_values)
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
                "top_values": [
                    {"value": _json_value(row[0]), "row_count": int(row[1])}
                    for row in value_result.rows
                ],
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


class ColumnNuanceBuilder:
    """Use a model to convert column evidence into nuance cards."""

    def __init__(
        self,
        *,
        generator: ColumnNuanceGenerator | None = None,
        profiler: ColumnEvidenceProfiler | None = None,
        batch_size: int = 10,
        strict_agentic: bool = False,
    ) -> None:
        self.generator = generator
        self.profiler = profiler or ColumnEvidenceProfiler()
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
    ) -> ColumnNuanceBuildResult:
        evidence = self.profiler.profile(corpus=corpus, metadata_descriptions=metadata_descriptions)
        cards, generation_errors = self._build_cards(evidence)
        if self.strict_agentic and self.generator is not None:
            generated_count = sum(1 for card in cards if card.learning_mode == "generated")
            if generation_errors or generated_count != len(evidence):
                details = "; ".join(generation_errors[:3]) or "not all evidence cards were generated"
                raise RuntimeError(f"agentic column nuance generation failed: {details}")
        document = _document(
            evidence=evidence,
            cards=cards,
            generation_errors=generation_errors,
            catalog=catalog,
            database=database,
            schema=schema,
            run_id=run_id,
        )
        local_path = None
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            local_path = output_dir / "column_nuances.json"
            local_path.write_text(json.dumps(document, indent=2, sort_keys=True, default=str), encoding="utf-8")
        object_key = None
        if object_store is not None:
            object_key = f"{object_prefix.strip('/')}/{run_id}/column_nuances.json"
            object_store.write_json(object_key, document)
        return ColumnNuanceBuildResult(
            document=document,
            evidence=evidence,
            cards=tuple(cards),
            generation_errors=tuple(generation_errors),
            local_path=local_path,
            object_key=object_key,
        )

    def _build_cards(self, evidence: tuple[ColumnEvidence, ...]) -> tuple[list[ColumnNuanceCard], list[str]]:
        if self.generator is None:
            return [_evidence_only_card(item) for item in evidence], []
        generated: dict[str, ColumnNuanceCard] = {}
        errors: list[str] = []
        for batch_index, batch in enumerate(_batches(list(evidence), self.batch_size), start=1):
            prompt = _nuance_prompt().replace(
                "{{evidence_json}}",
                json.dumps([item.to_dict() for item in batch], indent=2, sort_keys=True, default=str),
            )
            try:
                text = self.generator.complete(
                    [
                        {
                            "role": "system",
                            "content": (
                                "You learn SQL-affecting column nuances from supplied evidence. "
                                "Return only valid JSON."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ]
                )
                parsed_cards = _parse_generated_cards(text, evidence=batch)
                if not parsed_cards:
                    errors.append(
                        f"batch {batch_index} returned no parseable cards for "
                        f"{', '.join(item.column_ref for item in batch)}"
                    )
                for card in parsed_cards:
                    generated[card.column_ref] = card
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    f"batch {batch_index} failed for "
                    f"{', '.join(item.column_ref for item in batch)}: {type(exc).__name__}: {exc}"
                )
        for item in evidence:
            if item.column_ref not in generated:
                generated[item.column_ref] = _evidence_only_card(item)
        return [generated[column_ref] for column_ref in sorted(generated)], errors


def _column_usage(corpus: NormalizedKnowledgeCorpus) -> dict[str, dict[str, Any]]:
    usage: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "case_ids": set(),
            "source_types": set(),
            "trust_levels": set(),
            "examples": [],
        }
    )
    for case in corpus.cases:
        for column_ref in case.columns:
            entry = usage[column_ref]
            entry["count"] += 1
            entry["case_ids"].add(case.case_id)
            entry["source_types"].add(case.source_type.value)
            entry["trust_levels"].add(case.trust_level.value)
            if len(entry["examples"]) < 5:
                entry["examples"].append(
                    {
                        "case_id": case.case_id,
                        "question": case.question,
                        "source_type": case.source_type.value,
                        "trust_level": case.trust_level.value,
                    }
                )
    return dict(usage)


def _document(
    *,
    evidence: tuple[ColumnEvidence, ...],
    cards: list[ColumnNuanceCard],
    generation_errors: list[str],
    catalog: str,
    database: str,
    schema: str,
    run_id: str,
) -> dict[str, Any]:
    review_counts = Counter(card.review_status for card in cards)
    mode_counts = Counter(card.learning_mode for card in cards)
    return {
        "version": 1,
        "artifact_type": "column_nuance_library",
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "scope": {"catalog": catalog, "database": database, "schema": schema},
        "summary": {
            "evidence_count": len(evidence),
            "card_count": len(cards),
            "review_counts": dict(sorted(review_counts.items())),
            "learning_mode_counts": dict(sorted(mode_counts.items())),
            "generation_error_count": len(generation_errors),
        },
        "generation_errors": list(generation_errors),
        "evidence": {item.column_ref: item.to_dict() for item in evidence},
        "cards": {card.column_ref: card.to_dict() for card in cards},
    }


def _evidence_only_card(evidence: ColumnEvidence) -> ColumnNuanceCard:
    return ColumnNuanceCard(
        column_ref=evidence.column_ref,
        evidence_summary=(
            f"Evidence collected from {evidence.usage_count} SQL-bearing source(s); "
            "semantic interpretation was not generated."
        ),
        value_semantics=tuple(
            {
                "value": item.get("value"),
                "row_count": item.get("row_count"),
                "meaning": "",
                "evidence": "observed_value",
                "review_status": "needs_review",
            }
            for item in evidence.data_profile.get("top_values", [])
        ),
        null_semantics={
            "null_rate": evidence.data_profile.get("null_rate"),
            "meaning": "",
            "review_status": "needs_review",
        },
        review_status="needs_review",
        learning_mode="evidence_only",
        evidence={"column_evidence": evidence.to_dict()},
    )


def _parse_generated_cards(text: str, *, evidence: list[ColumnEvidence]) -> list[ColumnNuanceCard]:
    payload = _json_object(text)
    raw_cards = payload.get("cards", [])
    if not isinstance(raw_cards, list):
        return []
    evidence_by_column = {item.column_ref: item for item in evidence}
    cards = []
    for raw in raw_cards:
        if not isinstance(raw, dict):
            continue
        card = _normalize_card(raw, evidence_by_column=evidence_by_column)
        if card is not None:
            cards.append(card)
    return cards


def _normalize_card(
    value: dict[str, Any],
    *,
    evidence_by_column: dict[str, ColumnEvidence],
) -> ColumnNuanceCard | None:
    column_ref = _one_line(value.get("column_ref"))
    evidence = evidence_by_column.get(column_ref)
    if evidence is None:
        return None
    observed_values = {
        str(item.get("value"))
        for item in evidence.data_profile.get("top_values", [])
    }
    value_semantics = []
    for item in _dicts(value.get("value_semantics")):
        if "value" not in item:
            continue
        if observed_values and str(item.get("value")) not in observed_values:
            continue
        item["review_status"] = _generated_review_status(item.get("review_status"))
        value_semantics.append(item)
    review_status = _generated_review_status(value.get("review_status"))
    null_semantics = _dict(value.get("null_semantics"))
    if null_semantics:
        null_semantics["review_status"] = _generated_review_status(null_semantics.get("review_status"))
    return ColumnNuanceCard(
        column_ref=column_ref,
        role=_one_line(value.get("role")) or "unknown",
        evidence_summary=_one_line(value.get("evidence_summary")),
        value_semantics=tuple(value_semantics),
        null_semantics=null_semantics,
        default_behavior=_dict(value.get("default_behavior")),
        ambiguity_triggers=tuple(_dicts(value.get("ambiguity_triggers"))),
        open_questions=tuple(_dicts(value.get("open_questions"))),
        assertions=tuple(_strings(value.get("assertions"))),
        review_status=review_status,
        learning_mode="generated",
        evidence={"column_evidence": evidence.to_dict()},
    )


def _generated_review_status(value: Any) -> str:
    clean = _one_line(value)
    if clean == "approved":
        return "observed"
    if clean in {"observed", "needs_review"}:
        return clean
    return "needs_review"


def _nuance_prompt() -> str:
    return """Create SQL-affecting column nuance cards from evidence.

Return only valid JSON:
{
  "cards": [
    {
      "column_ref": "table.column",
      "role": "categorical_filter_dimension | measure | date_key | identifier | unknown",
      "evidence_summary": "one sentence grounded in the supplied evidence",
      "value_semantics": [
        {
          "value": "observed value exactly as supplied",
          "meaning": "business meaning if supported by description or usage",
          "default_behavior": "include | exclude | ask | neutral",
          "evidence": "description | query_usage | observed_value | insufficient",
          "review_status": "approved | observed | needs_review"
        }
      ],
      "null_semantics": {
        "meaning": "supported meaning or empty string",
        "default_grouping_behavior": "include_as_bucket | exclude | ask | unknown",
        "default_filter_behavior": "do_not_exclude_without_user_intent | exclude | ask | unknown",
        "evidence": "description | query_usage | observed_nulls | insufficient",
        "review_status": "approved | observed | needs_review"
      },
      "default_behavior": {
        "filtering": "how SQL should treat this column by default",
        "grouping": "how SQL should treat this column by default"
      },
      "ambiguity_triggers": [
        {
          "phrase": "natural language phrase",
          "reason": "why SQL could change",
          "action": "ask_user | use_approved_mapping | verify_values"
        }
      ],
      "open_questions": [
        {
          "question": "question for analyst",
          "reason": "why it changes SQL"
        }
      ],
      "assertions": [
        "generic SQL assertion for this column"
      ],
      "review_status": "approved | observed | needs_review"
    }
  ]
}

Rules:
- Use only supplied evidence. Do not invent tables, columns, or values.
- Value semantics may mention only observed values shown in data_profile.top_values.
- If meaning is not supported, leave it empty or mark needs_review.
- SQL-affecting uncertainty must become an ambiguity trigger or open question.
- Keep assertions generic and tied to the supplied column evidence.
- Do not include schema-specific rules that are not supported by evidence.

EVIDENCE:
{{evidence_json}}
"""


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


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


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
        raise ValueError("column nuance response must be a JSON object")
    return value


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        clean = _one_line(value)
        return [clean] if clean else []
    if isinstance(value, list):
        return [_one_line(item) for item in value if _one_line(item)]
    return []


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _batches(values: list[ColumnEvidence], size: int) -> list[list[ColumnEvidence]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
