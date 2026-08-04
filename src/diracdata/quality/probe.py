"""The DQ probe -- ONE type-aware aggregate pass that measures a table's shape cheaply.

Not 2x the SQL: a single SELECT computes, for the key columns, the facts that matter for drift --
row count, per-column null rate + distinct count, numeric range + mean, and the latest timestamp
(freshness). It is type-gated so it never asks an engine to MIN/MAX an unorderable complex type
(e.g. Postgres jsonb) -- those columns report counts only. Optional sampling keeps it cheap on huge
tables. It reports what the database says; the agent decides what to make of it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def probe_table(engine: Any, table: str, columns: list[str] | None = None, *,
                sample_pct: float | None = None) -> dict:
    """Measure `table` in one aggregate pass and return a JSON-safe snapshot::

        {"row_count": N, "columns": {col: {"null_pct", "distinct", ["min","max","avg"], ["max_ts"]}}}

    `columns` limits the probe to the key columns the answer rests on (cheaper); None probes them all.
    """
    meta = {c["column_name"]: str(c["column_type"]) for c in engine.describe_columns(table)}
    cols = [c for c in (columns or list(meta)) if c in meta] or list(meta)

    select = ["COUNT(*)"]
    plan: list[tuple[str, str]] = []            # (column, kind) in the SAME order as appended below
    for c in cols:
        q = _id(c)
        kind = _kind(meta[c])
        select.append(f"COUNT({q})")            # non-null count
        select.append(f"COUNT(DISTINCT {q})")   # distinct
        if kind in ("numeric", "temporal", "text"):
            select += [f"MIN({q})", f"MAX({q})"]
        if kind == "numeric":
            select.append(f"AVG({q})")
        plan.append((c, kind))

    src = _id(table) + _sample_clause(getattr(engine, "dialect", ""), sample_pct)
    rows = engine.query(f"SELECT {', '.join(select)} FROM {src}", 1).rows
    vals = list(rows[0]) if rows else [0] + [None] * (len(select) - 1)
    it = iter(vals)
    n = int(next(it) or 0)

    out: dict[str, Any] = {"row_count": n, "columns": {}}
    for c, kind in plan:
        nn = int(next(it) or 0)
        dist = int(next(it) or 0)
        col: dict[str, Any] = {"null_pct": round(100 * (n - nn) / n, 2) if n else 0.0, "distinct": dist}
        if kind == "numeric":                    # coerce to numbers so range/mean drift is comparable
            col["min"], col["max"], col["avg"] = _num(next(it)), _num(next(it)), _num(next(it))
        elif kind in ("temporal", "text"):
            col["min"], col["max"] = _jsonable(next(it)), _jsonable(next(it))
            if kind == "temporal":
                col["max_ts"] = col["max"]       # latest value = freshness signal
        out["columns"][c] = col
    return out


# ---- type gating ---------------------------------------------------------------------------------
def _kind(sql_type: str) -> str:
    """Map an engine type string to a probe kind. Order matters (numeric, then temporal, then the
    unorderable complex types, else text) so a probe never MIN/MAXes an unorderable type."""
    t = sql_type.lower()
    if any(k in t for k in ("int", "dec", "num", "real", "doub", "float", "money")):
        return "numeric"
    if any(k in t for k in ("timestamp", "date", "time")):
        return "temporal"
    if any(k in t for k in ("json", "struct", "map", "list", "array", "[]", "blob", "bytea",
                            "union", "variant", "geo")):
        return "complex"                          # counts only -- no MIN/MAX on an unorderable type
    return "text"


def _sample_clause(dialect: str, pct: float | None) -> str:
    if not pct:
        return ""
    d = (dialect or "").lower()
    if "duck" in d:
        return f" USING SAMPLE {pct} PERCENT (bernoulli)"
    if "postg" in d:
        return f" TABLESAMPLE BERNOULLI ({pct})"
    return ""                                     # unknown dialect -> full scan (correct over cheap)


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, bool)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    return str(v)                                 # timestamps / text -> stable string form


def _num(v: Any) -> Any:
    """Coerce a numeric aggregate to float -- Postgres returns `numeric` as a Decimal/string to keep
    precision; drift wants a comparable number. Falls back to a stable string if it isn't floatable."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return _jsonable(v)


def _id(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'
