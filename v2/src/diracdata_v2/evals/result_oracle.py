"""Result-first evaluation helpers for NL-to-SQL runs."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from diracdata_v2.query import DuckDBEngine, QueryResult


class ResultComparisonMode(StrEnum):
    SCALAR = "scalar"
    ORDERED_ROWS = "ordered_rows"
    UNORDERED_ROWS = "unordered_rows"
    TOLERANCE = "tolerance"


@dataclass(frozen=True)
class ResultSnapshot:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    comparison_mode: ResultComparisonMode
    result_hash: str
    truncated: bool = False

    def to_dict(self, *, preview_rows: int = 10) -> dict[str, Any]:
        return {
            "columns": list(self.columns),
            "rows": [_jsonable_row(row) for row in self.rows[: max(0, preview_rows)]],
            "row_count": self.row_count,
            "preview_row_count": min(len(self.rows), max(0, preview_rows)),
            "comparison_mode": self.comparison_mode.value,
            "result_hash": self.result_hash,
            "truncated": self.truncated,
            "scalar": self.scalar_value,
        }

    @property
    def scalar_value(self) -> Any | None:
        """The single answer value of a one-row result.

        A 1x1 grid is the obvious case. But a scalar question is often written with the
        filter columns echoed back (``SELECT gender, state, year, COUNT(*)`` -> one row,
        four columns); the *answer* there is still the measure. So for a one-row result
        we take the last numeric cell, which is where an aggregate lands. Returns None
        when the row has no numeric cell (nothing scalar to compare).
        """

        if len(self.rows) != 1:
            return None
        row = self.rows[0]
        if len(row) == 1:
            return _jsonable_value(row[0])
        for cell in reversed(row):
            if isinstance(cell, bool):
                continue
            if isinstance(cell, (int, float, Decimal)):
                return _jsonable_value(cell)
        return None


@dataclass(frozen=True)
class ResultComparison:
    passed: bool
    reason: str
    expected_hash: str
    actual_hash: str
    expected_row_count: int
    actual_row_count: int
    comparison_mode: ResultComparisonMode

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "expected_hash": self.expected_hash,
            "actual_hash": self.actual_hash,
            "expected_row_count": self.expected_row_count,
            "actual_row_count": self.actual_row_count,
            "comparison_mode": self.comparison_mode.value,
        }


def execute_snapshot(
    *,
    engine: DuckDBEngine,
    sql: str,
    comparison_mode: ResultComparisonMode | str | None = None,
    max_rows: int = 1000,
) -> ResultSnapshot:
    clean_sql = sql.strip().rstrip(";")
    row_count = engine.count_rows(clean_sql)
    result = engine.query(clean_sql, max_rows=max(1, int(max_rows)))
    mode = _resolve_mode(comparison_mode, result=result, sql=clean_sql)
    rows = tuple(tuple(_jsonable_value(value) for value in row) for row in result.rows)
    truncated = row_count > len(rows)
    digest = result_hash(
        columns=tuple(map(str, result.columns)),
        rows=rows,
        comparison_mode=mode,
        row_count=row_count,
        truncated=truncated,
    )
    return ResultSnapshot(
        columns=tuple(map(str, result.columns)),
        rows=rows,
        row_count=row_count,
        comparison_mode=mode,
        result_hash=digest,
        truncated=truncated,
    )


def snapshot_from_dict(value: dict[str, Any]) -> ResultSnapshot:
    mode = ResultComparisonMode(str(value.get("comparison_mode") or ResultComparisonMode.UNORDERED_ROWS.value))
    rows = tuple(tuple(row) for row in value.get("rows", []) if isinstance(row, list))
    return ResultSnapshot(
        columns=tuple(map(str, value.get("columns", []))),
        rows=rows,
        row_count=int(value.get("row_count") or len(rows)),
        comparison_mode=mode,
        result_hash=str(value.get("result_hash") or ""),
        truncated=bool(value.get("truncated", False)),
    )


def compare_snapshots(
    expected: ResultSnapshot,
    actual: ResultSnapshot,
    *,
    abs_tol: float = 1e-6,
    rel_tol: float = 1e-6,
) -> ResultComparison:
    if expected.truncated or actual.truncated:
        return ResultComparison(
            passed=False,
            reason="truncated_result",
            expected_hash=expected.result_hash,
            actual_hash=actual.result_hash,
            expected_row_count=expected.row_count,
            actual_row_count=actual.row_count,
            comparison_mode=expected.comparison_mode,
        )
    if expected.comparison_mode == ResultComparisonMode.SCALAR:
        # Grade the ANSWER, not its presentation. "count F customers in TX in 2001" is
        # answered by 369 — whether or not the SQL echoes the filter columns back via
        # GROUP BY. Hashing the result grid scores style as correctness: it fails
        # identical answers and can pass wrong ones. SQL-shape correctness is asserted
        # separately (expected_columns / expected_join_edges).
        expected_value = expected.scalar_value
        actual_value = actual.scalar_value
        matched = (
            expected_value is not None
            and actual_value is not None
            and _cells_match(expected_value, actual_value, abs_tol=abs_tol, rel_tol=rel_tol)
        )
        reason = "passed"
        if not matched:
            reason = "scalar_not_available" if (expected_value is None or actual_value is None) else "scalar_mismatch"
        return ResultComparison(
            passed=matched,
            reason=reason,
            expected_hash=expected.result_hash,
            actual_hash=actual.result_hash,
            expected_row_count=expected.row_count,
            actual_row_count=actual.row_count,
            comparison_mode=expected.comparison_mode,
        )
    if expected.row_count != actual.row_count:
        return ResultComparison(
            passed=False,
            reason="row_count_mismatch",
            expected_hash=expected.result_hash,
            actual_hash=actual.result_hash,
            expected_row_count=expected.row_count,
            actual_row_count=actual.row_count,
            comparison_mode=expected.comparison_mode,
        )
    if expected.comparison_mode == ResultComparisonMode.TOLERANCE:
        matched = _tolerant_match(expected.rows, actual.rows, abs_tol=abs_tol, rel_tol=rel_tol)
        return ResultComparison(
            passed=matched,
            reason="passed" if matched else "tolerance_mismatch",
            expected_hash=expected.result_hash,
            actual_hash=actual.result_hash,
            expected_row_count=expected.row_count,
            actual_row_count=actual.row_count,
            comparison_mode=expected.comparison_mode,
        )
    # Grade on VALUES, not a hash of the grid. The pack stores numbers as strings
    # ('15457939.53') while the engine returns floats (15457939.53); an exact hash
    # treats those as different and fails a character-for-character-correct answer.
    # Value comparison (numeric-aware, tolerant) fixes that without ever passing a
    # wrong number. ORDERED_ROWS keeps row order; UNORDERED_ROWS is a multiset.
    if expected.comparison_mode == ResultComparisonMode.ORDERED_ROWS:
        matched = _ordered_match(expected.rows, actual.rows, abs_tol=abs_tol, rel_tol=rel_tol)
    else:
        matched = _tolerant_match(expected.rows, actual.rows, abs_tol=abs_tol, rel_tol=rel_tol)
    actual_hash = result_hash(
        columns=actual.columns,
        rows=actual.rows,
        comparison_mode=expected.comparison_mode,
        row_count=actual.row_count,
        truncated=actual.truncated,
    )
    return ResultComparison(
        passed=matched,
        reason="passed" if matched else "result_value_mismatch",
        expected_hash=expected.result_hash,
        actual_hash=actual_hash,
        expected_row_count=expected.row_count,
        actual_row_count=actual.row_count,
        comparison_mode=expected.comparison_mode,
    )


def _tolerant_match(
    expected_rows: tuple[tuple[Any, ...], ...],
    actual_rows: tuple[tuple[Any, ...], ...],
    *,
    abs_tol: float,
    rel_tol: float,
) -> bool:
    """Row-set comparison where numeric cells match within tolerance.

    Handles float aggregate drift (e.g. SUM across engines) that exact hashing
    rejects. Rows are order-insensitive; non-numeric cells must match exactly.
    """

    def sort_key(row: tuple[Any, ...]) -> str:
        return json.dumps([_jsonable_value(v) for v in row], sort_keys=True, default=str)

    expected_sorted = sorted(expected_rows, key=sort_key)
    actual_sorted = sorted(actual_rows, key=sort_key)
    if len(expected_sorted) != len(actual_sorted):
        return False
    for exp_row, act_row in zip(expected_sorted, actual_sorted, strict=False):
        if len(exp_row) != len(act_row):
            return False
        for exp_cell, act_cell in zip(exp_row, act_row, strict=False):
            if not _cells_match(exp_cell, act_cell, abs_tol=abs_tol, rel_tol=rel_tol):
                return False
    return True


def _ordered_match(
    expected_rows: tuple[tuple[Any, ...], ...],
    actual_rows: tuple[tuple[Any, ...], ...],
    *,
    abs_tol: float,
    rel_tol: float,
) -> bool:
    """Row-by-row value comparison preserving order (for ORDER BY results)."""

    if len(expected_rows) != len(actual_rows):
        return False
    for exp_row, act_row in zip(expected_rows, actual_rows, strict=False):
        if len(exp_row) != len(act_row):
            return False
        for exp_cell, act_cell in zip(exp_row, act_row, strict=False):
            if not _cells_match(exp_cell, act_cell, abs_tol=abs_tol, rel_tol=rel_tol):
                return False
    return True


def _as_number(value: Any) -> float | None:
    """Coerce numbers and numeric-looking strings to float; None otherwise.

    The pack stores aggregates as strings; the engine returns floats. Treating a
    numeric string as its number is what lets a correct answer grade as correct.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _cells_match(expected: Any, actual: Any, *, abs_tol: float, rel_tol: float) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    exp_num, act_num = _as_number(expected), _as_number(actual)
    if exp_num is not None and act_num is not None:
        diff = abs(exp_num - act_num)
        return diff <= abs_tol or diff <= rel_tol * max(abs(exp_num), abs(act_num), 1.0)
    return _jsonable_value(expected) == _jsonable_value(actual)


def result_hash(
    *,
    columns: tuple[str, ...],
    rows: tuple[tuple[Any, ...], ...],
    comparison_mode: ResultComparisonMode,
    row_count: int,
    truncated: bool,
) -> str:
    canonical_rows = _canonical_rows(rows=rows, comparison_mode=comparison_mode)
    payload = {
        "comparison_mode": comparison_mode.value,
        "columns": list(columns),
        "rows": canonical_rows,
        "row_count": row_count,
        "truncated": truncated,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compact_snapshot_text(snapshot: ResultSnapshot, *, max_chars: int = 1200) -> str:
    text = json.dumps(snapshot.to_dict(preview_rows=10), sort_keys=True, default=str)
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)] + "..."


def infer_comparison_mode(sql: str, result: QueryResult | None = None) -> ResultComparisonMode:
    """Pick the mode from the shape of the ANSWER, not the style of the gold SQL.

    A one-row result is a scalar answer even when the SQL echoes its filter columns back
    (``SELECT gender, state, year, COUNT(*) ... GROUP BY ...``) — the answer is the
    aggregate; the echoed columns are presentation. Grading those as an ordered/unordered
    grid scores style as correctness.

    ORDER BY only implies ordering when more than one row is returned: ``ORDER BY x
    LIMIT 1`` is a max, not an ordering test.
    """

    if result is not None and len(result.rows) == 1:
        return ResultComparisonMode.SCALAR
    lowered = " ".join(sql.lower().split())
    if " order by " in f" {lowered} ":
        return ResultComparisonMode.ORDERED_ROWS
    return ResultComparisonMode.UNORDERED_ROWS


def _resolve_mode(
    comparison_mode: ResultComparisonMode | str | None,
    *,
    result: QueryResult,
    sql: str,
) -> ResultComparisonMode:
    if comparison_mode:
        return ResultComparisonMode(str(comparison_mode))
    return infer_comparison_mode(sql, result)


def _canonical_rows(
    *,
    rows: tuple[tuple[Any, ...], ...],
    comparison_mode: ResultComparisonMode,
) -> list[list[Any]]:
    output = [_jsonable_row(row) for row in rows]
    if comparison_mode == ResultComparisonMode.UNORDERED_ROWS:
        return sorted(output, key=lambda row: json.dumps(row, sort_keys=True, default=str))
    return output


def _jsonable_row(row: tuple[Any, ...]) -> list[Any]:
    return [_jsonable_value(value) for value in row]


def _jsonable_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        return value
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)
