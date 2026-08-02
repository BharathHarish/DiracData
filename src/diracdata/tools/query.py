"""Result-store query tools. `run_sql` stores the FULL result (as a `result_id`) and returns only a
compact envelope + preview; `query_result` slices a stored result without re-running the base query.
Both register their numbers in WorkingMemory so the finish gate can enforce faithfulness against them.
"""

from __future__ import annotations

import json
from typing import Any

from diracdata.config import Config
from diracdata.memory.results import ResultStore
from diracdata.memory.working_memory import WorkingMemory
from diracdata.utils.sql import validate_sql

_DEFAULTS = Config()


def build_query_tools(*, engine: Any, result_store: ResultStore, memory: WorkingMemory,
                      max_rows: int = _DEFAULTS.query_max_rows) -> list[Any]:
    from langchain.tools import tool

    @tool("run_sql")
    def run_sql(sql: str) -> str:
        """Execute a read-only SELECT. The FULL result is stored (as `result_id`); you get back the
        schema, row_count, and a preview (up to 100 rows, or ALL if <=200). Report numbers ONLY from
        this preview or from query_result -- never invent them. To drill into a large result, use
        query_result(result_id, ...) instead of re-running the query."""
        clean = (sql or "").strip().rstrip(";")
        check = validate_sql(clean, available_tables=set(engine.list_tables()))
        if check.get("status") != "ok":
            return f"SQL rejected: {check.get('error') or check}"
        try:
            env = result_store.run(clean)
        except Exception as exc:  # noqa: BLE001
            return f"SQL error: {type(exc).__name__}: {exc}"
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

    return [run_sql, query_result]
