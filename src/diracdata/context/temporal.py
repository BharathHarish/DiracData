"""V3-S3: temporal-coverage check between two time-bearing tables.

The silent trap: joining two facts that don't share a calendar window (campaigns 1996-98 vs stock
1998+) yields a nearest-day proxy or drops rows -- the answer looks confident but is wrong for the
period asked. This helper compares the date spans of two table.column references and reports the
overlap (as a percentage of each side) with a warning when it's small.

Substrate helper; consumed today by the MCP `temporal_coverage` tool. Kept single-purpose so it is
easy to compose (from data_check or a future analysis-plan phase) later, without SQL parsing here.
"""

from __future__ import annotations

from typing import Any


def _split_ref(ref: str) -> tuple[str, str]:
    if "." not in (ref or ""):
        raise ValueError(f"expected 'table.column', got {ref!r}")
    t, c = ref.split(".", 1)
    return t.strip(), c.strip()


def _span(engine: Any, table: str, col: str) -> dict:
    q, t = f'"{col}"', f'"{table}"'
    r = engine.query(
        f"SELECT MIN({q}), MAX({q}), COUNT({q}), COUNT(*) FROM {t}", 1).rows[0]
    return {"min": r[0], "max": r[1], "rows_with_date": int(r[2] or 0), "rows_total": int(r[3] or 0)}


def _days_between(lo: Any, hi: Any) -> float | None:
    """Days between two dates/datetimes, tolerant of None and mixed types via string coercion."""
    if lo is None or hi is None:
        return None
    try:
        import datetime as _dt
        if isinstance(lo, _dt.datetime): lo = lo.date()
        if isinstance(hi, _dt.datetime): hi = hi.date()
        if isinstance(lo, _dt.date) and isinstance(hi, _dt.date):
            return float((hi - lo).days)
        # ints (e.g. TPC-DS julian day refs) work directly
        return float(hi) - float(lo)
    except Exception:  # noqa: BLE001
        return None


def temporal_coverage(engine: Any, ref_a: str, ref_b: str) -> dict:
    """Compare the date spans of `<table_a>.<col_a>` and `<table_b>.<col_b>` and report the
    overlap. `ref_x` is a "table.column" string. The reported overlap_pct = overlap / that side's
    span, so a small pct on either side is the warning signal."""
    ta, ca = _split_ref(ref_a)
    tb, cb = _split_ref(ref_b)
    a, b = _span(engine, ta, ca), _span(engine, tb, cb)
    lo = max(v for v in (a["min"], b["min"]) if v is not None) if (a["min"] and b["min"]) else None
    hi = min(v for v in (a["max"], b["max"]) if v is not None) if (a["max"] and b["max"]) else None
    span_a = _days_between(a["min"], a["max"])
    span_b = _days_between(b["min"], b["max"])
    overlap_days = _days_between(lo, hi) if (lo is not None and hi is not None and lo <= hi) else 0.0
    pct_a = (overlap_days / span_a) if (span_a and span_a > 0) else None
    pct_b = (overlap_days / span_b) if (span_b and span_b > 0) else None
    warn = None
    if overlap_days == 0.0:
        warn = (f"NO OVERLAP: {ref_a} covers {a['min']}..{a['max']} but {ref_b} covers "
                f"{b['min']}..{b['max']}. Any join on these will silently produce a "
                "nearest-day proxy or drop rows.")
    elif (pct_a is not None and pct_a < 0.5) or (pct_b is not None and pct_b < 0.5):
        warn = (f"PARTIAL OVERLAP: overlap {lo}..{hi} covers "
                f"{(pct_a or 0)*100:.0f}% of {ref_a} and {(pct_b or 0)*100:.0f}% of {ref_b}. "
                "Restrict the query to the overlap window, or caveat the out-of-range side.")
    return {"a": {"ref": ref_a, **a}, "b": {"ref": ref_b, **b},
            "overlap": {"start": lo, "end": hi, "days": overlap_days},
            "overlap_pct_a": round(pct_a, 4) if pct_a is not None else None,
            "overlap_pct_b": round(pct_b, 4) if pct_b is not None else None,
            "warning": warn}
