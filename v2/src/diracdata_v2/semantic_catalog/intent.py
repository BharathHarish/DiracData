"""Intent-frame extraction for semantic-catalog context compilation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from diracdata_v2.tools.hybrid import tokenize


class IntentFrameExtractor(Protocol):
    def extract(self, question: str, *, catalog_summary: dict[str, Any] | None = None) -> "QueryIntentFrame": ...


@dataclass(frozen=True)
class QueryIntentFrame:
    search_queries: tuple[str, ...] = ()
    measures: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    time_windows: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    definition_required_terms: tuple[dict[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    source: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "search_queries": list(self.search_queries),
            "measures": list(self.measures),
            "dimensions": list(self.dimensions),
            "filters": list(self.filters),
            "time_windows": list(self.time_windows),
            "entities": list(self.entities),
            "definition_required_terms": [dict(item) for item in self.definition_required_terms],
            "notes": list(self.notes),
            "source": self.source,
        }


class DeterministicIntentFrameExtractor:
    """Lossless fallback when no model-backed extractor is configured."""

    def extract(self, question: str, *, catalog_summary: dict[str, Any] | None = None) -> QueryIntentFrame:
        del catalog_summary
        return QueryIntentFrame(search_queries=tuple(_phrase_queries(question)), source="deterministic")


class LLMIntentFrameExtractor:
    """Small model-backed query understanding step for runtime retrieval."""

    def __init__(self, *, model: Any) -> None:
        self._model = model

    def extract(self, question: str, *, catalog_summary: dict[str, Any] | None = None) -> QueryIntentFrame:
        from langchain_core.messages import HumanMessage, SystemMessage

        response = self._model.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=json.dumps(
                        {
                            "question": question,
                            "catalog_summary": catalog_summary or {},
                        },
                        indent=2,
                        sort_keys=True,
                    )
                ),
            ]
        )
        payload = _loads_json_object(_message_text(response))
        return QueryIntentFrame(
            search_queries=tuple(_strings(payload.get("search_queries"))) or tuple(_phrase_queries(question)),
            measures=tuple(_strings(payload.get("measures"))),
            dimensions=tuple(_strings(payload.get("dimensions"))),
            filters=tuple(_strings(payload.get("filters"))),
            time_windows=tuple(_strings(payload.get("time_windows"))),
            entities=tuple(_strings(payload.get("entities"))),
            definition_required_terms=tuple(_definition_terms(payload.get("definition_required_terms"))),
            notes=tuple(_strings(payload.get("notes"))),
            source="llm",
        )


_SYSTEM_PROMPT = """You create compact retrieval intent frames for a semantic data catalog.

Return only valid JSON with this shape:
{
  "search_queries": ["focused retrieval phrase"],
  "measures": ["requested metric or aggregate"],
  "dimensions": ["requested grouping/slicing concepts"],
  "filters": ["requested filters and exclusions"],
  "time_windows": ["requested time periods"],
  "entities": ["business entities, products, places, channels, people, objects"],
  "definition_required_terms": [
    {"term": "business phrase", "reason": "why SQL semantics need an approved definition"}
  ],
  "notes": ["optional compact retrieval notes"]
}

Rules:
- Keep phrases short and business-friendly.
- Preserve negation, thresholds, and time constraints.
- Search queries should include the whole intent plus focused entity phrases.
- Mark definition_required_terms only when a phrase needs organization-specific SQL semantics before execution.
- Do not mark ordinary schema entities, places, dates, product names, or categorical values as definition-required.
- Do not mark join relationships, ranking instructions, ordinary filters, or metric phrases as definition-required just because they need SQL planning.
- Do not mark narrative/report-shaping requests such as asking for a summary, explanation, or insights as definition-required unless they introduce an undefined metric, comparison baseline, or cohort rule that changes SQL.
- Put narrative/report-shaping requests in notes.
- Put relationship, join, ranking, grain, or metric-calculation cautions in notes unless the phrase is truly undefined business policy.
- Do not invent tables, columns, values, or definitions.
"""


def _phrase_queries(text: str) -> list[str]:
    tokens = tokenize(text)
    queries = [" ".join(str(text).split())]
    for size in (4, 3, 2):
        for index in range(0, max(0, len(tokens) - size + 1)):
            queries.append(" ".join(tokens[index : index + size]))
    queries.extend(tokens)
    return _dedupe(queries)[:32]


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
        raise ValueError("intent extractor response must be a JSON object")
    return payload


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content)


def _definition_terms(value: Any) -> list[dict[str, str]]:
    output = []
    if not isinstance(value, list):
        return output
    for item in value:
        if isinstance(item, str):
            term = _one_line(item)
            if term:
                output.append({"term": term, "reason": "Needs explicit business definition."})
        elif isinstance(item, dict):
            term = _one_line(item.get("term") or item.get("name"))
            reason = _one_line(item.get("reason")) or "Needs explicit business definition."
            if term:
                output.append({"term": term, "reason": reason})
    return output


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_one_line(value)] if _one_line(value) else []
    if isinstance(value, list):
        return [_one_line(item) for item in value if _one_line(item)]
    return []


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output = []
    for value in values:
        clean = _one_line(value)
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            output.append(clean)
    return output
