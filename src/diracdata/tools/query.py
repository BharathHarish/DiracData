"""Result-store query tools. `run_sql` stores the FULL result (as a `result_id`) and returns only a
compact envelope + preview; `query_result` slices a stored result without re-running the base query.
Both register their numbers in WorkingMemory so the finish gate can enforce faithfulness against them.
"""

from __future__ import annotations

import json
from typing import Any

from diracdata.config import Config
from diracdata.runtime.results import ResultStore
from diracdata.runtime.working_memory import WorkingMemory
from diracdata.utils.sql import validate_sql

_DEFAULTS = Config()


def build_query_tools(*, engine: Any, result_store: ResultStore, memory: WorkingMemory,
                      sources: Any = None, max_rows: int = _DEFAULTS.query_max_rows) -> list[Any]:
    from langchain.tools import tool

    def _engine(source: str | None):
        return sources.get(source) if (source and sources is not None) else engine

    @tool("run_sql")
    def run_sql(sql: str, source: str | None = None) -> str:
        """Execute a read-only SELECT and store the FULL result (as `result_id`); you get back the
        schema, row_count, and a preview (up to 100 rows, or ALL if <=200). Report numbers ONLY from
        this preview or query_result -- never invent them. In a MULTI-SOURCE estate, pass `source` to
        pick the data store (e.g. run_sql('SELECT ...', source='orders_pg')) and write that source's
        SQL dialect; omit `source` for the default. To combine results from different sources, reduce
        each with run_sql then join them with combine_results (DuckDB)."""
        clean = (sql or "").strip().rstrip(";")
        try:
            eng = _engine(source)
        except KeyError as exc:
            return f"Unknown source: {exc}"
        check = validate_sql(clean, available_tables=set(eng.list_tables()))
        if check.get("status") != "ok":
            return f"SQL rejected: {check.get('error') or check}"
        try:
            env = result_store.run(clean, source=source)
        except Exception as exc:  # noqa: BLE001
            return f"SQL error: {type(exc).__name__}: {exc}"
        env["source"] = eng.name
        env["dialect"] = eng.dialect
        memory.note_result(env)
        memory.register_numbers(env.get("preview"))
        return json.dumps(env, default=str)

    @tool("query_result")
    def query_result(result_id: str, sql: str) -> str:
        """Slice/aggregate a STORED result without re-running the base query. Refer to the stored
        result as the table `result`, e.g. query_result('r1', 'SELECT band, SUM(n) FROM result GROUP
        BY band'). Use for follow-up cuts, totals, null/distinct profiling of a big result."""
        if result_id not in memory.results:
            return f"No such result_id: {result_id}. Available: {list(memory.results) or 'none'}."
        try:
            out = result_store.query(result_id, (sql or "").strip().rstrip(";"), max_rows=max_rows)
        except Exception as exc:  # noqa: BLE001
            return f"query_result error: {type(exc).__name__}: {exc}"
        memory.register_numbers(out.get("rows"))   # derived totals are citable/faithful too
        return json.dumps(out, default=str)

    @tool("combine_results")
    def combine_results(result_ids: list[str], sql: str) -> str:
        """Join/aggregate MULTIPLE stored results together in one DuckDB step -- the way to reconcile
        results that came from DIFFERENT sources. Refer to each stored result by its result_id as a
        table, e.g. combine_results(['r1','r2'], 'SELECT seg, SUM(r1.rev) FROM r1 JOIN r2 USING
        (user_id) GROUP BY seg'). The combined output is stored as a NEW result_id (so its numbers
        stay faithful). Reduce each source first with run_sql, then combine the small results here."""
        ids = list(result_ids or [])
        missing = [r for r in ids if r not in memory.results]
        if not ids:
            return "combine_results needs at least one result_id."
        if missing:
            return f"No such result_id(s): {missing}. Available: {list(memory.results) or 'none'}."
        clean = (sql or "").strip().rstrip(";")
        check = validate_sql(clean, available_tables=set(ids))
        if check.get("status") != "ok":
            return f"SQL rejected: {check.get('error') or check}"
        try:
            env = result_store.combine(ids, clean)
        except Exception as exc:  # noqa: BLE001
            return f"combine_results error: {type(exc).__name__}: {exc}"
        memory.note_result(env)
        memory.register_numbers(env.get("preview"))
        return json.dumps(env, default=str)

    return [run_sql, query_result, combine_results]
