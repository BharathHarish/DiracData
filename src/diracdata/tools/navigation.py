"""The analyst's tools -- tiered, column-first retrieval plus join/define/examples/run/check.

Retrieval is progressive so the analyst never has to read a whole wide table at once (which
truncated, and made it guess columns): get_tables -> get_columns (compact) -> describe_column
(deep) -> profile_column (real distinct values, cached). Everything is read-only.
"""

from __future__ import annotations

import json
from typing import Any

from diracdata.config import Config
from diracdata.utils.duckdb_engine import DuckDBEngine
from diracdata.utils.sql import validate_sql

from diracdata.context.valuecache import ColumnValueCache
from diracdata.context.workspace import Workspace

_DEFAULTS = Config()


def build_navigation_tools(*, workspace: Workspace, engine: DuckDBEngine,
                           max_rows: int = _DEFAULTS.nav_max_rows,
                           value_cache: ColumnValueCache | None = None,
                           sources: Any = None) -> list[Any]:
    from langchain.tools import tool

    cache = value_cache if value_cache is not None else ColumnValueCache(None)
    multi = sources is not None and len(sources.names()) > 1
    default_name = getattr(engine, "name", None)

    def _engine_for(source: str | None):
        return sources.get(source) if (source and sources is not None) else engine

    def _resolve(table_name: str, source: str | None):
        """(engine, source_name) for a table: an explicit source, else the source that has the table,
        else the default. Lets the analyst explore ANY source's tables, not just the default fabric."""
        if source:
            return sources.get(source), source
        if not multi:
            return engine, default_name
        for nm in sources.names():
            if table_name in set(sources.get(nm).list_tables()):
                return sources.get(nm), nm
        return engine, default_name

    def _key(nm: str | None, table: str) -> str:
        return f"{nm}.{table}" if multi else table

    # --- Tiered retrieval: scan (short) -> detail (long, on demand as a tie-breaker) --------
    @tool("get_tables")
    def get_tables() -> str:
        """SCAN: list EVERY table with a one-line description. Start here to see what data exists
        and pick the few tables relevant to the question. In a MULTI-SOURCE estate tables are grouped
        by source -- note the source, then pass source= to get_columns/profile_column/run_sql."""
        if not multi:
            return "\n".join(f"- {name}: {desc}" for name, desc in workspace.tables())
        blocks = []
        for nm in sources.names():
            ws_tables = workspace.tables() if (nm == default_name and workspace is not None) else None
            if ws_tables:
                rows = [f"  - {name}: {desc}" for name, desc in ws_tables]
            else:
                rows = [f"  - {t}" for t in sources.get(nm).list_tables()]
            blocks.append(f"[source: {nm}]\n" + ("\n".join(rows) or "  (no tables)"))
        return ("TABLES across the estate -- pass source= to get_columns / profile_column / run_sql:\n\n"
                + "\n\n".join(blocks))

    @tool("describe_tables")
    def describe_tables(tables: list[str], source: str | None = None) -> str:
        """DETAIL: the full description (and column count) for one or more candidate tables. Use
        to tie-break which table you need when the one-liners from get_tables() aren't decisive.
        Pass source= for a non-default store."""
        out = []
        for t in tables or []:
            eng, nm = _resolve(t, source)
            if nm == default_name and workspace is not None and workspace.table_description(t) is not None:
                n = len(workspace.column_names(t) or [])
                out.append(f"TABLE {t} ({n} columns): {workspace.table_description(t)}")
            elif eng is not None and t in set(eng.list_tables()):
                out.append(f"TABLE {nm}.{t} ({len(eng.list_columns(t))} columns) [source={nm}]")
            else:
                out.append(f"No such table: {t}.")
        return "\n\n".join(out) or "No tables given."

    @tool("get_columns")
    def get_columns(table_name: str, source: str | None = None) -> str:
        """SCAN: a table's columns, compact -- name, one-line description (if learned), and a few
        example values. Read this to pick the right columns; describe_columns() for a full
        description; profile_column() for the real distinct values. Pass source= for a non-default
        store (e.g. get_columns('payments', source='orders_pg'))."""
        eng, nm = _resolve(table_name, source)
        if nm == default_name and workspace is not None:
            rows = workspace.columns_compact(table_name)
            if rows is not None:
                lines = [f"COLUMNS of {table_name}:"]
                for c in rows:
                    ex = f"  [ex: {c['examples']}]" if c.get("examples") else ""
                    lines.append(f"  - {c['name']}: {c['description']}{ex}")
                return "\n".join(lines)
        cols = eng.describe_columns(table_name) if eng is not None else []
        if not cols:
            return f"No such table: {table_name}" + (f" in source '{nm}'" if multi else "") + ". Call get_tables()."
        head = f"COLUMNS of {nm}.{table_name} (source={nm}):" if multi else f"COLUMNS of {table_name}:"
        return head + "\n" + "\n".join(f"  - {c['column_name']} ({c['column_type']})" for c in cols)

    @tool("describe_columns")
    def describe_columns(table_name: str, columns: list[str], source: str | None = None) -> str:
        """DETAIL: the full description + value domain (if learned), else the type, for one or more
        columns. Use to tie-break near-synonym columns. Pass source= for a non-default store."""
        eng, nm = _resolve(table_name, source)
        types = None
        if not (nm == default_name and workspace is not None and workspace.column_names(table_name)):
            types = {c["column_name"]: c["column_type"] for c in (eng.describe_columns(table_name) if eng else [])}
        out = []
        for c in columns or []:
            if types is None:
                d = workspace.column_detail(table_name, c)
                if d is None:
                    out.append(f"No column '{c}' in {table_name}.")
                    continue
                line = f"{table_name}.{c}: {d['description']}"
                if d.get("values"):
                    line += f"\n  [{d['values']}]"
                out.append(line)
            elif c in types:
                out.append(f"{nm}.{table_name}.{c}: type {types[c]}")
            else:
                out.append(f"No column '{c}' in {nm}.{table_name}.")
        return "\n\n".join(out) or f"No columns given for {table_name}."

    @tool("profile_column")
    def profile_column(table_name: str, column_name: str, source: str | None = None) -> str:
        """Return a column's actual DISTINCT values (up to 1000), from cache or by scanning it. Use
        before filtering to confirm the exact values that exist (casing, codes, a NULL bucket). Pass
        source= for a non-default store (e.g. profile_column('orders','status',source='orders_pg'))."""
        eng, nm = _resolve(table_name, source)
        if eng is None or table_name not in set(eng.list_tables()):
            return f"No such table: {table_name}" + (f" in source '{nm}'" if multi else "") + "."
        if column_name not in eng.list_columns(table_name):
            return f"No column '{column_name}' in {nm}.{table_name}. Check get_columns."
        cap = _DEFAULTS.profile_distinct_cap
        show = _DEFAULTS.profile_values_display
        vals = cache.get(_key(nm, table_name), column_name)
        if vals is None:
            try:
                res = eng.query(
                    f'SELECT DISTINCT "{column_name}" AS v FROM "{table_name}" '
                    f'WHERE "{column_name}" IS NOT NULL ORDER BY 1 LIMIT {cap}', cap)
            except Exception as exc:  # noqa: BLE001
                return f"Could not profile {table_name}.{column_name}: {type(exc).__name__}: {exc}"
            vals = [r[0] for r in res.rows]
            cache.put(_key(nm, table_name), column_name, vals)
        n = len(vals)
        shown = ", ".join(str(v) for v in vals[:show])
        capped = f" (capped at {cap})" if n >= cap else ""
        tail = " ..." if n > show else ""
        return f"{table_name}.{column_name}: {n} distinct value(s){capped}: {shown}{tail}"

    @tool("join_path")
    def join_path(table_a: str, table_b: str) -> str:
        """Return the join path between two tables as ON conditions -- composed through
        intermediate tables for 3- or 4-way joins. Use this to join correctly instead of
        guessing keys. If it returns 'no known path', work it out from the columns, verify with
        run_sql, and it will be learned for next time."""
        path = workspace.join_path(table_a, table_b)
        if path is None:
            return f"no known path between {table_a} and {table_b} -- discover it from the columns and verify with run_sql"
        if not path:
            return "same table"
        return " AND ".join(path)

    @tool("define")
    def define(name: str) -> str:
        """Get the customer's exact definition of a business term (e.g. 'active_online_buyer',
        'new_online_buyer') or a metric (e.g. 'online_revenue', 'aov'). ALWAYS bind to the
        defined SQL/logic verbatim -- do not reinvent what a business term means."""
        return workspace.define(name)

    @tool("find_examples")
    def find_examples(query: str) -> str:
        """Find prior solved queries (gold NL-SQL pairs + query history + learned experiences)
        whose SQL uses the tables/columns you name, or whose question matches your words. Use
        this to reuse a proven pattern instead of authoring cold. Pass table/column names and
        business words together, e.g. 'clients addresses first_sale gender state'."""
        hits = workspace.find_examples(query, limit=5)
        if not hits:
            return "No matching examples. Try different table/column names or business words."
        blocks = []
        for i, ex in enumerate(hits, 1):
            q = f"Q: {ex.question}" if ex.question else "Q: (from query history, no NL)"
            blocks.append(f"[{i}] ({ex.source}) {q}\nSQL: {ex.sql}")
        return "\n\n".join(blocks)

    @tool("run_sql")
    def run_sql(sql: str, source: str | None = None) -> str:
        """Execute a read-only SELECT and return the columns and rows. Use it to check a CTE, a
        filter's selectivity, or a join before you trust it. Pass source= for a non-default store."""
        eng = _engine_for(source)
        clean = (sql or "").strip().rstrip(";")
        check = validate_sql(clean, available_tables=set(eng.list_tables()))
        if check.get("status") != "ok":
            return f"SQL rejected: {check.get('error') or check}"
        try:
            result = eng.query(clean, max_rows=max_rows)
        except Exception as exc:  # noqa: BLE001
            return f"SQL error: {type(exc).__name__}: {exc}"
        rows = [list(r) for r in result.rows]
        return json.dumps({"columns": result.columns, "rows": rows, "row_count": len(rows)}, default=str)

    @tool("data_check")
    def data_check(sql: str, source: str | None = None) -> str:
        """Run both stewardship gates on a query before you trust its number:
        DATA QUALITY on the inputs (null rates, value ranges/negatives/zeros, join orphan % =
        referential integrity, fan-out = grain inflation) AND SANITY on the output it returns
        (empty result, NULL cells, a rate/share out of [0,100], negative counts, grain leak).
        Pass your draft final query; use it to catch a distortion before committing."""
        from diracdata.utils.stewardship import probe_footprint, sanity_check
        eng = _engine_for(source)
        dq = probe_footprint(eng, sql)
        result = None
        clean = (sql or "").strip().rstrip(";")
        if validate_sql(clean, available_tables=set(eng.list_tables())).get("status") == "ok":
            try:
                r = eng.query(clean, max_rows=max_rows)
                result = {"columns": r.columns, "rows": [list(x) for x in r.rows], "row_count": len(r.rows)}
            except Exception:  # noqa: BLE001
                result = None
        sanity = sanity_check(sql, result) if result is not None else {}
        if not dq and not sanity:
            return "No checks could be run from that SQL."
        return json.dumps({"data_quality": dq, "sanity": sanity}, default=str)

    tools = [get_tables, describe_tables, get_columns, describe_columns, profile_column,
             join_path, find_examples, run_sql, data_check]
    if workspace.semantic_layer:
        tools.insert(5, define)
    return tools
