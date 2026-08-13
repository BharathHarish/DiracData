"""Validate blessed metric SQL binds against a live engine catalog."""
from __future__ import annotations

import re
from typing import Any


def metric_bind_check(engine: Any, sql: str, *, max_rows: int = 1) -> dict[str, Any]:
    """Try to bind/execute metric SQL under LIMIT 0 (shape check).

    Returns {ok, missing_tables, missing_columns, parse_error, resolved_sql}.
    missing_* are best-effort parses from the engine error when bind fails.
    """
    raw = (sql or "").strip().rstrip(";")
    if not raw:
        return {
            "ok": False,
            "missing_tables": [],
            "missing_columns": [],
            "parse_error": "empty sql",
            "resolved_sql": raw,
        }

    if re.match(r"(?is)^\s*(with|select)\b", raw):
        probe = raw
    else:
        # Expression metrics like SUM(orders.order_amount) need FROM clauses.
        tables = list(dict.fromkeys(re.findall(r"\b([A-Za-z_][\w]*)\.", raw)))
        # drop structural prefixes that aren't tables
        skip = {"json", "map", "struct", "list", "http", "https"}
        tables = [t for t in tables if t.lower() not in skip]
        if tables:
            from_clause = ", ".join(f'"{t}"' for t in tables)
            probe = f"SELECT ({raw}) AS _m FROM {from_clause}"
        else:
            probe = f"SELECT ({raw}) AS _m"

    wrapped = f"SELECT * FROM ({probe}) AS _bind_check LIMIT 0"
    try:
        engine.query(wrapped, max_rows)
        return {
            "ok": True,
            "missing_tables": [],
            "missing_columns": [],
            "parse_error": None,
            "resolved_sql": raw,
        }
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        return {
            "ok": False,
            "missing_tables": _extract_missing(msg, kind="table"),
            "missing_columns": _extract_missing(msg, kind="column"),
            "parse_error": msg,
            "resolved_sql": raw,
        }


_TABLE_RE = re.compile(
    r"(?:Table with name\s+(\S+)|Catalog Error: Table with name\s+(\S+)|"
    r"relation\s+[\"']?(\w+)[\"']?\s+does not exist|"
    r"no such table:\s*(\S+))",
    re.I,
)
_COL_RE = re.compile(
    r"(?:Referenced column\s+[\"']?(\w+)[\"']?\s+not found|"
    r"column\s+[\"']?(\w+)[\"']?\s+does not exist|"
    r"no such column:\s*(\S+))",
    re.I,
)


def _extract_missing(msg: str, *, kind: str) -> list[str]:
    rx = _TABLE_RE if kind == "table" else _COL_RE
    found: list[str] = []
    for m in rx.finditer(msg or ""):
        name = next((g for g in m.groups() if g), None)
        if name:
            found.append(name.strip("\"'`"))
    # unique, stable
    return list(dict.fromkeys(found))
