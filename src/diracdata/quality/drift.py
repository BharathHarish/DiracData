"""Drift detection -- compare a fresh probe to the previous snapshot(s) and surface EVIDENCE.

Evidence, not a verdict: each finding is a measured change (row count moved, a null spike, a range
shift, a distinct collapse, staleness) with from/to numbers. Soft thresholds (ENV) decide only what
is worth SURFACING; whether a surfaced change is MATERIAL to the answer is the agent's / verifier's
agentic call -- there is no pass/fail here.
"""

from __future__ import annotations

from typing import Any


def detect_drift(current: dict, history: list[dict], *, drift_pct: float, null_delta: float) -> list[dict]:
    """Findings comparing `current` (a fresh, not-yet-appended snapshot) to the newest snapshot in
    `history`. Empty history -> a single 'baseline' note (nothing to compare yet)."""
    if not history:
        return [{"kind": "baseline",
                 "note": "first observation for this table -- snapshot recorded as the baseline; "
                         "no history to compare drift against yet."}]

    prev = history[-1]
    findings: list[dict] = []
    _rel(findings, "row_count", None, prev.get("row_count"), current.get("row_count"), drift_pct)

    cur_cols, prev_cols = current.get("columns", {}), prev.get("columns", {})
    for col, cur in cur_cols.items():
        p = prev_cols.get(col)
        if not p:
            continue
        d = abs(float(cur.get("null_pct", 0.0)) - float(p.get("null_pct", 0.0)))
        if d >= null_delta:
            findings.append({"kind": "null_pct", "column": col, "from": p.get("null_pct"),
                             "to": cur.get("null_pct"), "delta_pts": round(d, 2)})
        _rel(findings, "distinct", col, p.get("distinct"), cur.get("distinct"), drift_pct)
        _rel(findings, "avg", col, p.get("avg"), cur.get("avg"), drift_pct)
        _rel(findings, "min", col, p.get("min"), cur.get("min"), drift_pct)
        _rel(findings, "max", col, p.get("max"), cur.get("max"), drift_pct)
        if "max_ts" in cur and cur.get("max_ts") != p.get("max_ts"):
            findings.append({"kind": "freshness", "column": col, "from": p.get("max_ts"),
                             "to": cur.get("max_ts"),
                             "note": "latest timestamp moved -- compare against the expected load cadence."})
    return findings


def _rel(findings: list[dict], metric: str, column: str | None, before: Any, after: Any,
         drift_pct: float) -> None:
    """Append a finding when a numeric metric moved by more than `drift_pct` relative to `before`.
    Non-numeric values (e.g. text/timestamp min/max) are skipped -- freshness handles timestamps."""
    b, a = _f(before), _f(after)
    if b is None or a is None:
        return
    base = abs(b)
    if base == 0.0:
        if a == 0.0:
            return
        pct = 100.0                               # 0 -> non-zero is a full swing
    else:
        pct = abs(a - b) / base * 100.0
    if pct >= drift_pct:
        f = {"kind": metric, "from": before, "to": after, "change_pct": round(pct, 1)}
        if column is not None:
            f["column"] = column
        findings.append(f)


def _f(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None
