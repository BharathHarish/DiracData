"""The CONTEXT MCP tool bundle -- the curated, client-facing surface (PRIMITIVES ONLY; no loop-control,
no nested agent). A separately-versioned product artifact: `context_tools(rt)` returns the exact tools
an external LLM (Cursor/Claude/Gemini) may call. It shares the substrate with the internal agent
(Context reads, engine + validate_sql, stewardship, the attribution primitive) but is its OWN bundle,
so agent-internal tools (plan/finish/remember/subagents) never leak to clients.

Adding a capability is an explicit choice: put it here (client-facing) and/or in the agent's tools.
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any


def context_tools(rt: Any) -> list:
    """Return the client-facing MCP tools (closures over the runtime). Registered by server.py."""
    from diracdata.utils.sql import validate_sql

    # ---- provider: the learned context ------------------------------------------------------------
    def list_tables(schema: str = "") -> str:
        """List every table in the governed model with its verified grain, kind, and column count."""
        return rt.ctx(schema).tables()

    def describe_table(table: str, schema: str = "") -> str:
        """Full governed detail for one table: grain, kind, columns (with access recipes for complex
        columns), measures, and the joins touching it."""
        return rt.ctx(schema).describe(table)

    def describe_column(table: str, column: str, schema: str = "") -> str:
        """A column's business meaning + value domain, including the exact ACCESS RECIPE for a
        complex/nested column (STRUCT/ARRAY/MAP/JSON) -- copy the recipe verbatim into your SQL."""
        d = rt.ctx(schema).column(table, column)
        return (d or {}).get("description") or f"no column {table}.{column}"

    def search_context(pattern: str, schema: str = "") -> str:
        """Grep the governed model (regex or substring) across table/column names, descriptions, access
        recipes, and metric/dimension names. Use to find where a concept lives before drilling in."""
        return rt.ctx(schema).search(pattern)

    def join_path(table: str, schema: str = "") -> str:
        """The verified join edges + CARDINALITY (many_to_one / one_to_one / many_to_many) touching a
        table. ALWAYS consult before joining, so you aggregate-then-join and never fan-out/chasm
        double-count -- the join keys may be named differently on each side."""
        return rt.ctx(schema).joins(table)

    def get_metric(name: str = "", schema: str = "") -> str:
        """A governed business metric's definition (SQL/formula + how it decomposes). Empty name lists
        all metric names. Use the governed SQL rather than inventing your own."""
        return rt.ctx(schema).metric(name)

    def find_examples(query: str, schema: str = "") -> str:
        """CALL THIS FIRST. Find proven prior queries (gold NL->SQL pairs + real query history) whose
        SQL matches your tables/business words -- adapt a working pattern instead of authoring cold.
        Pass table/column names + business words together."""
        hits = rt.ctx(schema).find_examples(query)
        if not hits:
            return "no matching examples (a fresh schema may have none yet) -- author from the context tools."
        return "\n\n".join(f"Q: {getattr(h, 'question', '') or '(from history)'}\nSQL: {getattr(h, 'sql', '')}"
                           for h in hits)

    # ---- execution (guarded) ----------------------------------------------------------------------
    def run_sql(sql: str) -> str:
        """Execute a read-only SELECT against the warehouse and return columns + rows (bounded)."""
        clean = (sql or "").strip().rstrip(";")
        check = validate_sql(clean, available_tables=set(rt.engine.list_tables()))
        if check.get("status") != "ok":
            return f"SQL rejected: {check.get('error') or check}"
        try:
            res = rt.engine.query(clean, rt.settings.query_max_rows)
        except Exception as exc:  # noqa: BLE001
            return f"SQL error: {type(exc).__name__}: {exc}"
        return json.dumps({"columns": res.columns, "rows": [list(r) for r in res.rows]}, default=str)

    def data_check(sql: str) -> str:
        """Verify a DRAFT query before trusting its number: DATA QUALITY on inputs (null rates, join
        orphan %, fan-out = grain inflation) + SANITY on the output. Run this on any multi-table query."""
        from diracdata.utils.stewardship import probe_footprint, sanity_check
        clean = (sql or "").strip().rstrip(";")
        dq = probe_footprint(rt.engine, clean)
        result = None
        if validate_sql(clean, available_tables=set(rt.engine.list_tables())).get("status") == "ok":
            try:
                r = rt.engine.query(clean, rt.settings.query_max_rows)
                result = {"columns": r.columns, "rows": [list(x) for x in r.rows], "row_count": len(r.rows)}
            except Exception:  # noqa: BLE001
                result = None
        sanity = sanity_check(clean, result) if result is not None else {}
        return json.dumps({"data_quality": dq, "sanity": sanity}, default=str)

    # ---- RCA primitive (deterministic; NO nested LLM) ---------------------------------------------
    def attribute(metric: str, period_a: Any, period_b: Any, dimensions: list | None = None,
                  schema: str = "") -> str:
        """Root-cause a governed metric's change between two periods: the COMPLETE, cited decomposition
        (driver tree + per-dimension attribution). `metric` is a defined metric name; period_a/period_b
        are the two period values (e.g. years); `dimensions` = the dims to break down by (omit for the
        primary ones). Deterministic -- computed by the engine, every figure a citable result_id."""
        ws = rt.ctx(schema).workspace
        if not getattr(ws, "semantic_layer", None):
            return "no metric tree (semantic_layer) for this schema -- run learn_schema first."
        from diracdata.rca.attribution import build_attribution_tool
        from diracdata.runtime.working_memory import WorkingMemory
        tool = build_attribution_tool(workspace=ws, engine=rt.engine, result_store=rt.result_store(schema),
                                      memory=WorkingMemory(goal=f"attribute {metric}"), config=rt.settings)[0]
        return tool.invoke({"metric": metric, "period_a": period_a, "period_b": period_b,
                            "dimensions": dimensions})

    # ---- builder: compile context for a schema ----------------------------------------------------
    def learn_schema(schema: str = "") -> str:
        """Compile the governed context (grain, discovered joins, metrics, complex-column recipes) for a
        schema by running the learning agent. Long-running -> starts in the background and returns a
        job_id; poll learn_status(job_id). Do this once for a schema before querying it."""
        sch = schema or rt.default_schema
        job_id = f"learn-{uuid.uuid4().hex[:8]}"
        with rt._lock:
            rt._jobs[job_id] = {"schema": sch, "status": "running"}

        def _work():
            try:
                from diracdata.learning import Learner
                out = Learner(schema=sch, model=rt.model, settings=rt.settings).learn()
                rt._ctx.pop(sch, None)
                with rt._lock:
                    rt._jobs[job_id] = {"schema": sch, "status": "done", "result": out.get("coverage")}
            except Exception as exc:  # noqa: BLE001
                with rt._lock:
                    rt._jobs[job_id] = {"schema": sch, "status": "error", "error": f"{type(exc).__name__}: {exc}"}

        threading.Thread(target=_work, daemon=True).start()
        return json.dumps({"job_id": job_id, "schema": sch, "status": "running",
                           "note": "poll learn_status(job_id)"})

    def learn_status(job_id: str) -> str:
        """Check a learn_schema job: running | done (+ coverage) | error."""
        with rt._lock:
            return json.dumps(rt._jobs.get(job_id, {"status": "unknown job_id"}), default=str)

    return [list_tables, describe_table, describe_column, search_context, join_path, get_metric,
            find_examples, run_sql, data_check, attribute, learn_schema, learn_status]
