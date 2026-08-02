"""Measured column facts -- the grounding behind the learning agent's `profile_column` tool.

This is not a driver and not a heuristic: it runs SQL and reports what the database says
(cardinality, null rate, whether the column is a unique key, and real distinct values for
low-cardinality columns). The learning AGENT decides when to call it and what to make of it.
"""

from __future__ import annotations

from typing import Any

from diracdata.config import Config

_DEFAULTS = Config()


def column_facts(engine: Any, table: str, column: str, *,
                 complete_max: int = _DEFAULTS.profiler_complete_max,
                 sample: int = _DEFAULTS.profiler_sample) -> dict:
    """Measured facts for one column. `complete_max` = report ALL distinct values up to this many
    (a complete domain); beyond it, a small sample + range."""
    q = _id(column)
    # One aggregate pass: total rows, non-null count, distinct count, min, max.
    r = engine.query(
        f"SELECT COUNT(*), COUNT({q}), COUNT(DISTINCT {q}), MIN({q}), MAX({q}) FROM {_id(table)}", 1).rows
    n, nn, d, mn, mx = ((int(r[0][0] or 0), int(r[0][1] or 0), int(r[0][2] or 0), r[0][3], r[0][4])
                        if r else (0, 0, 0, None, None))
    facts = {
        "table": table, "column": column, "row_count": n, "distinct": d,
        "null_pct": round(100 * (n - nn) / n, 2) if n else 0.0,   # (rows - non_null) / rows = null share
        "is_unique_key": n > 0 and d == n and nn == n,
    }
    if 0 < d <= complete_max:
        facts["domain"] = {"complete": True, "values": _distinct(engine, table, q, complete_max), "distinct_at_least": d}
    else:
        vals = [row[0] for row in engine.query(
            f"SELECT {q} FROM {_id(table)} WHERE {q} IS NOT NULL LIMIT {sample}", sample).rows]
        dom = {"complete": False, "values": vals, "distinct_at_least": d}  # already bounded by `sample`
        if mn is not None:
            dom["min"] = mn
        if mx is not None:
            dom["max"] = mx
        facts["domain"] = dom
    return facts


def _distinct(engine, table, q, limit) -> list:
    return [row[0] for row in engine.query(
        f"SELECT DISTINCT {q} FROM {_id(table)} WHERE {q} IS NOT NULL ORDER BY 1 LIMIT {limit}", limit).rows]


def _id(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'
