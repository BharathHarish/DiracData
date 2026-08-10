"""Measured column facts -- the grounding behind the learning agent's `profile_column` tool.

This is not a driver and not a heuristic: it runs SQL and reports what the database says
(cardinality, null rate, whether the column is a unique key, and real distinct values for
low-cardinality columns). The learning AGENT decides when to call it and what to make of it.
"""

from __future__ import annotations

from typing import Any

from diracdata.config import Config
from diracdata.learning.nested import nested_shape

_DEFAULTS = Config()


def _column_type(engine: Any, table: str, column: str) -> str | None:
    """The column's runtime type (DuckDB `typeof`), so a complex column is profiled by DESCENT rather
    than a meaningless scalar COUNT DISTINCT. None if unavailable (engine has no typeof / empty table)."""
    try:
        r = engine.query(f"SELECT typeof({_id(column)}) FROM {_id(table)} "
                         f"WHERE {_id(column)} IS NOT NULL LIMIT 1", 1).rows
        return r[0][0] if r and r[0] else None
    except Exception:  # noqa: BLE001
        return None


def _is_complex(type_str: str | None) -> bool:
    if not type_str:
        return False
    u = type_str.upper().strip()
    return u.endswith("[]") or u.startswith("STRUCT(") or u.startswith("MAP(") or u == "JSON"


def column_facts(engine: Any, table: str, column: str, *,
                 complete_max: int = _DEFAULTS.profiler_complete_max,
                 sample: int = _DEFAULTS.profiler_sample) -> dict:
    """Measured facts for one column. Complex columns (STRUCT/LIST/MAP/JSON) are profiled by DESCENT
    (inner shape + access recipes + array-length + json keys); scalars get cardinality + domain.
    `complete_max` = report ALL distinct values up to this many (a complete domain)."""
    q = _id(column)
    # Complex column: descend instead of a scalar COUNT DISTINCT (which is meaningless or errors here).
    type_str = _column_type(engine, table, column)
    if _is_complex(type_str):
        try:
            r = engine.query(f"SELECT COUNT(*), COUNT({q}) FROM {_id(table)}", 1).rows
            n, nn = (int(r[0][0] or 0), int(r[0][1] or 0)) if r else (0, 0)
        except Exception:  # noqa: BLE001
            n, nn = 0, 0
        shape = nested_shape(engine, table, column, type_str, sample=sample)
        return {"table": table, "column": column, "row_count": n,
                "null_pct": round(100 * (n - nn) / n, 2) if n else 0.0,
                "complex_type": True, **shape}
    # One aggregate pass: total rows, non-null count, distinct count, min, max. Some engines cannot
    # MIN/MAX an unorderable complex type (e.g. Postgres jsonb) -> degrade to the counts + no range.
    try:
        r = engine.query(
            f"SELECT COUNT(*), COUNT({q}), COUNT(DISTINCT {q}), MIN({q}), MAX({q}) FROM {_id(table)}", 1).rows
        n, nn, d, mn, mx = ((int(r[0][0] or 0), int(r[0][1] or 0), int(r[0][2] or 0), r[0][3], r[0][4])
                            if r else (0, 0, 0, None, None))
    except Exception:  # noqa: BLE001
        r = engine.query(f"SELECT COUNT(*), COUNT({q}), COUNT(DISTINCT {q}) FROM {_id(table)}", 1).rows
        n, nn, d = (int(r[0][0] or 0), int(r[0][1] or 0), int(r[0][2] or 0)) if r else (0, 0, 0)
        mn = mx = None
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
    try:
        return [row[0] for row in engine.query(
            f"SELECT DISTINCT {q} FROM {_id(table)} WHERE {q} IS NOT NULL ORDER BY 1 LIMIT {limit}", limit).rows]
    except Exception:  # noqa: BLE001  -- unorderable type (e.g. jsonb): drop the ORDER BY
        return [row[0] for row in engine.query(
            f"SELECT DISTINCT {q} FROM {_id(table)} WHERE {q} IS NOT NULL LIMIT {limit}", limit).rows]


def _id(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'
