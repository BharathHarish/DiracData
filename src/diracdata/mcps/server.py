"""context_mcp -- the DiracData context MCP server. Exposes a schema's GOVERNED CONTEXT (grain,
discovered joins + cardinality, metric tree, complex-column recipes, proven examples) + guarded SQL
execution + the fan-out/sanity check to any MCP client (Claude / ChatGPT / Gemini), plus a builder
tool that runs the learning agent to compile that context for a new schema.

Composes existing packages only -- diracdata.context (provider), diracdata.learning (builder),
diracdata.engines/quality (execution). No core files touched. Context defaults to LOCAL disk
(~/.diracdata) so a desktop user needs no MinIO; pass store="s3" for a shared/team context.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any


def context_mcp(*, schema: str | None = None, data: str | None = None, store: str = "local",
                model: Any = None, context_dir: str | None = None, env_file: str | None = None,
                settings: Any = None, name: str = "diracdata"):
    """Build the DiracData context MCP server.

    schema      default schema for the tools (each tool also takes an explicit schema).
    data        a local source: a .duckdb file, or a dir / single .parquet|.csv (else the s3 lake).
    store       where the learned context lives: "local" (~/.diracdata, default) or "s3" (shared).
    model       LLM for learn_schema (a diracdata.model_providers Provider, a profile id, or None=ENV).
    """
    from diracdata.config import settings_from_env
    from diracdata.context import Context
    from diracdata.engines import DuckDBEngine
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("context_mcp requires the MCP SDK -- install diracdata[mcp]") from exc

    settings = settings if settings is not None else settings_from_env(env_file)
    if store == "local":
        cdir = str(Path(context_dir or "~/.diracdata").expanduser())
        settings = replace(settings, object_store="local", local_artifact_root=cdir)
    from diracdata.model_providers import _Provider
    if isinstance(model, _Provider):
        settings = model.apply(settings)
    elif isinstance(model, str):
        settings = replace(settings, agent_model_profile=model)
    default_schema = schema or "default"

    # one engine: a local file source, else the configured (s3/local-parquet) lake engine
    if data:
        from diracdata.mcps.duckdb_source import DuckDBFileEngine
        engine = DuckDBFileEngine(path=data, name=default_schema)
    else:
        engine = DuckDBEngine.from_settings(settings, default_schema)

    rt = _Runtime(settings=settings, default_schema=default_schema, engine=engine, model=model)
    server = MCPServer(name=name, instructions=_INSTRUCTIONS)
    _register(server, rt, Context)
    return server


_INSTRUCTIONS = (
    "DiracData gives you GOVERNED CONTEXT over the user's data warehouse so you write correct SQL. "
    "Before authoring a query: use search_context to find where a concept lives, describe_column for a "
    "column's meaning + nested access recipe, join_path for the VERIFIED join + cardinality (so you "
    "aggregate-then-join and never fan-out/chasm double-count), and get_metric for a governed metric's "
    "SQL. Execute with run_sql (read-only) and verify a draft with data_check (fan-out + sanity). If a "
    "schema hasn't been learned yet, call learn_schema first and poll learn_status."
)


class _Runtime:
    def __init__(self, *, settings, default_schema, engine, model) -> None:
        self.settings = settings
        self.default_schema = default_schema
        self.engine = engine
        self.model = model
        self._ctx: dict = {}
        self._jobs: dict = {}
        self._lock = threading.Lock()

    def ctx(self, schema: str | None):
        from diracdata.context import Context
        sch = schema or self.default_schema
        if sch not in self._ctx:
            self._ctx[sch] = Context.load(sch, settings=self.settings)
        return self._ctx[sch]


def _register(server, rt: _Runtime, Context) -> None:
    from diracdata.utils.sql import validate_sql

    # ---- provider (the learned context) ----------------------------------------------------------
    @server.tool()
    def list_tables(schema: str = "") -> str:
        """List every table in the governed model with its verified grain, kind, and column count."""
        return rt.ctx(schema).tables()

    @server.tool()
    def describe_table(table: str, schema: str = "") -> str:
        """Full governed detail for one table: grain, kind, columns (with access recipes for complex
        columns), measures, and the joins touching it."""
        return rt.ctx(schema).describe(table)

    @server.tool()
    def describe_column(table: str, column: str, schema: str = "") -> str:
        """A column's business meaning + value domain, including the exact ACCESS RECIPE for a
        complex/nested column (copy it verbatim for the SQL)."""
        d = rt.ctx(schema).column(table, column)
        return (d or {}).get("description") or f"no column {table}.{column}"

    @server.tool()
    def search_context(pattern: str, schema: str = "") -> str:
        """Grep the governed model (regex or substring) across table/column names, descriptions,
        access recipes, and metric/dimension names. Find where a concept lives before drilling in."""
        return rt.ctx(schema).search(pattern)

    @server.tool()
    def join_path(table: str, schema: str = "") -> str:
        """The verified join edges + CARDINALITY (many_to_one / one_to_one / many_to_many) touching a
        table. Consult before joining so you aggregate-then-join and never fan-out/chasm double-count."""
        return rt.ctx(schema).joins(table)

    @server.tool()
    def get_metric(name: str = "", schema: str = "") -> str:
        """A governed business metric's definition (SQL/formula). Empty name lists all metric names."""
        return rt.ctx(schema).metric(name)

    @server.tool()
    def find_examples(query: str, schema: str = "") -> str:
        """Proven prior queries (gold pairs + history) whose SQL matches your tables/words -- reuse a
        pattern instead of authoring cold."""
        hits = rt.ctx(schema).find_examples(query)
        if not hits:
            return "no matching examples"
        return "\n\n".join(f"Q: {getattr(h, 'question', '') or '(from history)'}\nSQL: {getattr(h, 'sql', '')}"
                           for h in hits)

    # ---- execution (guarded) ---------------------------------------------------------------------
    @server.tool()
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

    @server.tool()
    def data_check(sql: str) -> str:
        """Run the stewardship gates on a draft query: DATA QUALITY on inputs (null rates, join orphan
        %, fan-out = grain inflation) + SANITY on the output. Use before trusting a number."""
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

    # ---- builder (compile context for a schema) --------------------------------------------------
    @server.tool()
    def learn_schema(schema: str = "") -> str:
        """Compile the governed context (grain, joins, metrics, recipes) for a schema by running the
        learning agent. Long-running -> starts in the background and returns a job_id; poll
        learn_status(job_id). Do this once for a schema before asking questions over it."""
        sch = schema or rt.default_schema
        job_id = f"learn-{uuid.uuid4().hex[:8]}"
        with rt._lock:
            rt._jobs[job_id] = {"schema": sch, "status": "running"}

        def _work():
            try:
                from diracdata.learning import Learner
                out = Learner(schema=sch, model=rt.model, settings=rt.settings).learn()
                rt._ctx.pop(sch, None)     # force a fresh Context.load next read
                with rt._lock:
                    rt._jobs[job_id] = {"schema": sch, "status": "done", "result": out.get("coverage")}
            except Exception as exc:  # noqa: BLE001
                with rt._lock:
                    rt._jobs[job_id] = {"schema": sch, "status": "error", "error": f"{type(exc).__name__}: {exc}"}

        threading.Thread(target=_work, daemon=True).start()
        return json.dumps({"job_id": job_id, "schema": sch, "status": "running",
                           "note": "poll learn_status(job_id)"})

    @server.tool()
    def learn_status(job_id: str) -> str:
        """Check a learn_schema job: running | done (+ coverage) | error."""
        with rt._lock:
            return json.dumps(rt._jobs.get(job_id, {"status": "unknown job_id"}), default=str)
