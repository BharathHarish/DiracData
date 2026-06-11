"""CSV-driven UAT suite utilities for the data analyst agent."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class ExpectedBehavior(StrEnum):
    """High-level behavior expected for a UAT turn."""

    EXECUTE_SQL = "execute_sql"
    CLARIFY = "clarify"
    EXPLAIN_ONLY = "explain_only"
    INSPECT_DATA = "inspect_data"


@dataclass(frozen=True)
class UatTurn:
    """One turn inside a possibly multi-turn UAT conversation."""

    case_id: str
    turn_index: int
    category: str
    question: str
    expected_behavior: ExpectedBehavior
    expected_result: str | None = None
    expected_answer_contains: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    required_grounding_ids: tuple[str, ...] = ()
    required_tables: tuple[str, ...] = ()
    required_columns: tuple[str, ...] = ()
    required_sql_contains: tuple[str, ...] = ()
    forbidden_sql_contains: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class UatConversation:
    """A grouped UAT case that runs on one checkpointed thread."""

    case_id: str
    turns: tuple[UatTurn, ...]

    @property
    def question(self) -> str:
        return self.turns[0].question

    @property
    def follow_ups(self) -> tuple[str, ...]:
        return tuple(turn.question for turn in self.turns[1:])


@dataclass
class TraceTurn:
    """Trace facts extracted for one UAT turn."""

    turn_index: int
    question: str
    raw_text: str = ""
    final_answer: str = ""
    tool_calls: list[str] = field(default_factory=list)
    tool_outputs: dict[str, list[str]] = field(default_factory=dict)
    run_sql_outputs: list[dict[str, Any]] = field(default_factory=list)
    executed_sql: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TurnEvaluation:
    """Evaluation result for a single UAT turn."""

    case_id: str
    turn_index: int
    passed: bool
    failures: tuple[str, ...]


@dataclass(frozen=True)
class ConversationEvaluation:
    """Evaluation result for a full checkpointed UAT conversation."""

    case_id: str
    passed: bool
    turn_results: tuple[TurnEvaluation, ...]

    @property
    def failures(self) -> tuple[str, ...]:
        values: list[str] = []
        for result in self.turn_results:
            values.extend(result.failures)
        return tuple(values)


def load_uat_conversations(path: Path) -> list[UatConversation]:
    """Load grouped UAT conversations from a CSV file."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    turns = [_turn_from_row(row) for row in rows if _has_case(row)]
    grouped: dict[str, list[UatTurn]] = {}
    for turn in turns:
        grouped.setdefault(turn.case_id, []).append(turn)

    conversations = []
    for case_id, case_turns in grouped.items():
        ordered = sorted(case_turns, key=lambda turn: turn.turn_index)
        _validate_turn_order(case_id, ordered)
        conversations.append(UatConversation(case_id=case_id, turns=tuple(ordered)))
    return sorted(conversations, key=lambda conversation: conversation.case_id)


def evaluate_trace(
    *,
    trace_path: Path,
    conversation: UatConversation,
) -> ConversationEvaluation:
    """Evaluate one CLI JSONL trace against one UAT conversation."""
    trace_turns = extract_trace_turns(trace_path)
    results = []
    for expected in conversation.turns:
        actual = trace_turns.get(expected.turn_index)
        if actual is None:
            results.append(
                TurnEvaluation(
                    case_id=conversation.case_id,
                    turn_index=expected.turn_index,
                    passed=False,
                    failures=(f"missing trace for turn {expected.turn_index}",),
                )
            )
            continue
        failures = _evaluate_turn(expected, actual)
        results.append(
            TurnEvaluation(
                case_id=conversation.case_id,
                turn_index=expected.turn_index,
                passed=not failures,
                failures=tuple(failures),
            )
        )
    return ConversationEvaluation(
        case_id=conversation.case_id,
        passed=all(result.passed for result in results),
        turn_results=tuple(results),
    )


def extract_trace_turns(trace_path: Path) -> dict[int, TraceTurn]:
    """Extract turn-level trace facts from a CLI JSONL trace."""
    return _extract_trace_turns(trace_path)


def _turn_from_row(row: dict[str, str]) -> UatTurn:
    behavior_value = _required(row, "expected_behavior")
    try:
        behavior = ExpectedBehavior(behavior_value)
    except ValueError as exc:
        values = ", ".join(item.value for item in ExpectedBehavior)
        raise ValueError(f"Invalid expected_behavior={behavior_value!r}; expected one of {values}") from exc

    return UatTurn(
        case_id=_required(row, "case_id"),
        turn_index=int(_required(row, "turn_index")),
        category=row.get("category", "").strip(),
        question=_required(row, "question"),
        expected_behavior=behavior,
        expected_result=_optional(row, "expected_result"),
        expected_answer_contains=_split_cell(row.get("expected_answer_contains", "")),
        required_tools=_split_cell(row.get("required_tools", "")),
        required_grounding_ids=_split_cell(row.get("required_grounding_ids", "")),
        required_tables=_split_cell(row.get("required_tables", "")),
        required_columns=_split_cell(row.get("required_columns", "")),
        required_sql_contains=_split_cell(row.get("required_sql_contains", "")),
        forbidden_sql_contains=_split_cell(row.get("forbidden_sql_contains", "")),
        notes=row.get("notes", "").strip(),
    )


def _has_case(row: dict[str, str]) -> bool:
    return bool(row.get("case_id", "").strip())


def _required(row: dict[str, str], key: str) -> str:
    value = row.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing required UAT CSV field: {key}")
    return value


def _optional(row: dict[str, str], key: str) -> str | None:
    value = row.get(key, "").strip()
    return value or None


def _split_cell(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(part.strip() for part in value.split(";") if part.strip())


def _validate_turn_order(case_id: str, turns: list[UatTurn]) -> None:
    expected = list(range(1, len(turns) + 1))
    actual = [turn.turn_index for turn in turns]
    if actual != expected:
        raise ValueError(f"UAT case {case_id!r} has non-contiguous turn indexes: {actual}")


def _extract_trace_turns(trace_path: Path) -> dict[int, TraceTurn]:
    turns: dict[int, TraceTurn] = {}
    current: TraceTurn | None = None
    with trace_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            event_type = event.get("type") if isinstance(event, dict) else None
            if event_type == "turn_start":
                current = TraceTurn(
                    turn_index=int(event["turn_index"]),
                    question=str(event.get("question", "")),
                )
                turns[current.turn_index] = current
                current.raw_text += _json_text(event)
                continue
            if current is None:
                continue
            current.raw_text += "\n" + _json_text(event)
            if event_type == "turn_end":
                current.final_answer = str(event.get("final_answer", ""))
            _collect_tool_calls(event, current)
            _collect_tool_outputs(event, current)
    return turns


def _collect_tool_calls(value: Any, turn: TraceTurn) -> None:
    if isinstance(value, dict):
        tool_calls = value.get("tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if isinstance(call, dict):
                    name = str(call.get("name", "")).strip()
                    if name:
                        turn.tool_calls.append(name)
        for item in value.values():
            _collect_tool_calls(item, turn)
    elif isinstance(value, list):
        for item in value:
            _collect_tool_calls(item, turn)


def _collect_tool_outputs(value: Any, turn: TraceTurn) -> None:
    if isinstance(value, dict):
        if value.get("type") == "ToolMessage":
            name = str(value.get("name", "")).strip()
            content = str(value.get("content", ""))
            if name:
                turn.tool_outputs.setdefault(name, []).append(content)
            if name == "run_sql_tool":
                parsed = _parse_json_object(content)
                if isinstance(parsed, dict):
                    turn.run_sql_outputs.append(parsed)
                    sql = parsed.get("sql")
                    if isinstance(sql, str):
                        turn.executed_sql.append(sql)
        for item in value.values():
            _collect_tool_outputs(item, turn)
    elif isinstance(value, list):
        for item in value:
            _collect_tool_outputs(item, turn)


def _parse_json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _evaluate_turn(expected: UatTurn, actual: TraceTurn) -> list[str]:
    failures: list[str] = []
    raw = actual.raw_text.lower()
    final_answer = actual.final_answer.lower()
    executed_sql = "\n".join(actual.executed_sql).lower()
    tool_names = {name.lower() for name in actual.tool_calls}
    tool_names.update(name.lower() for name in actual.tool_outputs)
    ran_sql = bool(actual.run_sql_outputs)

    if expected.expected_behavior == ExpectedBehavior.EXECUTE_SQL and not ran_sql:
        failures.append("expected run_sql_tool execution but no SQL execution was traced")
    if expected.expected_behavior in {
        ExpectedBehavior.CLARIFY,
        ExpectedBehavior.EXPLAIN_ONLY,
    } and ran_sql:
        failures.append(f"expected {expected.expected_behavior.value} without SQL execution")

    for tool in expected.required_tools:
        if tool.lower() not in tool_names:
            failures.append(f"required tool was not used: {tool}")

    for grounding_id in expected.required_grounding_ids:
        if grounding_id.lower() not in raw:
            failures.append(f"required grounding id was not observed: {grounding_id}")

    if expected.expected_result and not _result_observed(expected.expected_result, actual):
        failures.append(f"expected result was not observed: {expected.expected_result}")

    for phrase in expected.expected_answer_contains:
        if phrase.lower() not in final_answer:
            failures.append(f"expected final answer phrase was missing: {phrase}")

    if expected.expected_behavior in {
        ExpectedBehavior.EXECUTE_SQL,
        ExpectedBehavior.INSPECT_DATA,
    } and executed_sql:
        for table in expected.required_tables:
            if table.lower() not in executed_sql:
                failures.append(f"required table was missing from executed SQL: {table}")
        for column in expected.required_columns:
            if column.lower() not in executed_sql:
                failures.append(f"required column was missing from executed SQL: {column}")
        for phrase in expected.required_sql_contains:
            if phrase.lower() not in executed_sql:
                failures.append(f"required SQL phrase was missing: {phrase}")
        for phrase in expected.forbidden_sql_contains:
            if phrase.lower() in executed_sql:
                failures.append(f"forbidden SQL phrase was present: {phrase}")

    return failures


def _result_observed(expected: str, actual: TraceTurn) -> bool:
    expected_clean = expected.strip()
    for output in actual.run_sql_outputs:
        for value in _walk_values(output):
            if str(value).strip() == expected_clean:
                return True
    return expected_clean.lower() in actual.final_answer.lower()


def _walk_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        values: list[Any] = []
        for item in value.values():
            values.extend(_walk_values(item))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_walk_values(item))
        return values
    return [value]


def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True)
