"""Tools the learning agent uses to measure a schema. Read-only; the AGENT decides what to call.

`profile_column` is grounded convenience (real cardinality/nulls/values in one call); `run_sql`
lets the agent measure anything else it's curious about (grain, cross-column checks, nuances).
"""

from __future__ import annotations

import json
from typing import Any

from diracdata.config import Config
from diracdata.utils.duckdb_engine import DuckDBEngine
from diracdata.utils.sql import validate_sql

from diracdata.learning.profiler import column_facts

_DEFAULTS = Config()


def build_learning_tools(*, engine: DuckDBEngine, max_rows: int = _DEFAULTS.learn_max_rows) -> list[Any]:
    from langchain.tools import tool

    @tool("get_columns")
    def get_columns(table: str) -> str:
        """List a table's columns and their SQL types. Start here for a table you're profiling."""
        cols = engine.describe_columns(table)
        if not cols:
            return f"No such table: {table}."
        return "\n".join(f"  - {c['column_name']}: {c['column_type']}" for c in cols)

    @tool("profile_column")
    def profile_column(table: str, column: str) -> str:
        """Measure one column: row count, distinct count, null %, whether it is a unique key, and
        -- for low-cardinality columns -- the actual distinct values. Use this to ground every
        description in real data before you write it."""
        if table not in set(engine.list_tables()):
            return f"No such table: {table}."
        if column not in engine.list_columns(table):
            return f"No column '{column}' in {table}."
        return json.dumps(column_facts(engine, table, column), default=str)

    @tool("run_sql")
    def run_sql(sql: str) -> str:
        """Run a read-only SELECT to measure anything else -- table grain, cross-column checks,
        distributions, a nuance you want to confirm before describing it."""
        clean = (sql or "").strip().rstrip(";")
        if validate_sql(clean, available_tables=set(engine.list_tables())).get("status") != "ok":
            return "SQL rejected (must be a read-only SELECT over known tables)."
        try:
            r = engine.query(clean, max_rows=max_rows)
        except Exception as exc:  # noqa: BLE001
            return f"SQL error: {type(exc).__name__}: {exc}"
        return json.dumps({"columns": r.columns, "rows": [list(x) for x in r.rows]}, default=str)

    @tool("verify_join")
    def verify_join(left_table: str, left_col: str, right_table: str, right_col: str) -> str:
        """Verify a candidate join edge by EXECUTING it. Orients fact->dimension automatically and
        reports: matched rows, orphan % (referential integrity, from the fact side), fan-out, and
        grain (1:1 vs 1:many). Accept an edge only if it matches rows with low orphan %; REJECT
        edges that match nothing, orphan heavily, or explode fan-out (a shared attribute like
        'state'/'year' that isn't a real key)."""
        tables = set(engine.list_tables())
        if left_table not in tables or right_table not in tables:
            return "unknown table"
        if left_col not in engine.list_columns(left_table) or right_col not in engine.list_columns(right_table):
            return "unknown column"
        try:
            ln, ld = _counts(engine, left_table, left_col)
            rn, rd = _counts(engine, right_table, right_col)
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"
        if not ln or not rn:
            return json.dumps({"verdict": "reject", "reason": "empty table"})
        # dimension = the more-unique side (its key identifies rows); fact = the other side.
        if rd / rn >= ld / ln:
            (ft, fc, fn), (dt, dc), dim_uniq = (left_table, left_col, ln), (right_table, right_col), rd / rn
        else:
            (ft, fc, fn), (dt, dc), dim_uniq = (right_table, right_col, rn), (left_table, left_col), ld / ln
        on = f'F.{_id(fc)} = D.{_id(dc)}'
        orphan = int(_scalar(engine, f'SELECT COUNT(*) FROM {_id(ft)} F LEFT JOIN {_id(dt)} D ON {on} WHERE D.{_id(dc)} IS NULL') or 0)
        joined = int(_scalar(engine, f'SELECT COUNT(*) FROM {_id(ft)} F JOIN {_id(dt)} D ON {on}') or 0)
        matched = fn - orphan
        orphan_pct = round(100 * orphan / fn, 2) if fn else None
        fan_out = round(joined / matched, 2) if matched else None
        grain = "1:1" if dim_uniq > 0.999 and (fan_out or 0) <= 1.001 else "1:many"
        return json.dumps({"fact": ft, "dimension": dt, "matched_rows": matched, "orphan_pct": orphan_pct,
                           "fan_out": fan_out, "grain": grain,
                           "verdict": "reject" if matched == 0 else "accept"}, default=str)

    return [get_columns, profile_column, run_sql, verify_join]


def _counts(engine, table, col):
    r = engine.query(f'SELECT COUNT(*), COUNT(DISTINCT {_id(col)}) FROM {_id(table)}', 1).rows[0]
    return int(r[0] or 0), int(r[1] or 0)


def _scalar(engine, sql):
    r = engine.query(sql, 1).rows
    return r[0][0] if r else None


def _id(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'
