"""context_mcp -- the DiracData context MCP server. Exposes a schema's GOVERNED CONTEXT (grain,
discovered joins + cardinality, metric tree, complex-column recipes, proven examples) + guarded SQL
execution + the fan-out/sanity check to any MCP client (Claude / ChatGPT / Gemini), plus a builder
tool that runs the learning agent to compile that context for a new schema.

Composes existing packages only -- diracdata.context (provider), diracdata.learning (builder),
diracdata.engines/quality (execution). No core files touched. Context defaults to LOCAL disk
(~/.diracdata) so a desktop user needs no MinIO; pass store="s3" for a shared/team context.
"""

from __future__ import annotations

import threading
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
    from diracdata.mcps.tools import context_tools
    for fn in context_tools(rt):          # the curated client-facing bundle (primitives only)
        server.tool()(fn)
    return server


_INSTRUCTIONS = (
    "DiracData gives you GOVERNED CONTEXT over the user's data warehouse so you write correct SQL. YOU "
    "are the analyst; these tools ground and verify you. Recommended flow: (1) find_examples FIRST -- "
    "reuse a proven query pattern; (2) search_context to locate a concept, describe_column for a "
    "column's meaning + nested ACCESS RECIPE, get_metric for a governed metric's SQL; (3) join_path for "
    "the VERIFIED join + cardinality BEFORE any join (keys are often named differently on each side -- "
    "aggregate-then-join, never fan-out/chasm double-count); (4) run_sql to execute (read-only); (5) "
    "data_check to verify a multi-table draft (fan-out + sanity); (6) temporal_coverage BEFORE joining "
    "two time-bearing tables in a question with an implied period ('during the campaign', 'over the "
    "same window') -- catches the silent nearest-day proxy when their calendars don't overlap; (7) "
    "attribute for the complete cited root-cause decomposition of a metric's change. If a schema is "
    "not learned yet, call learn_schema and poll learn_status."
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

    def result_store(self, schema: str | None):
        """A ResultStore over the one engine (used by the RCA attribute primitive). Cached per schema."""
        from diracdata.engines import SourceRegistry
        from diracdata.execution import make_executor
        from diracdata.runtime.results import ResultStore
        from diracdata.stores import store_from_settings
        sch = schema or self.default_schema
        key = f"__rs__{sch}"
        if key not in self._ctx:
            s = self.settings
            self._ctx[key] = ResultStore(engine=self.engine, store=store_from_settings(s), schema=sch,
                                         sources=SourceRegistry.of(self.engine), preview_rows=s.preview_rows,
                                         preview_all_max=s.preview_all_max,
                                         reconciler_memory_limit=s.reconciler_memory_limit,
                                         reconciler_temp_dir=s.reconciler_temp_dir,
                                         reconciler_threads=s.reconciler_threads, executor=make_executor(s))
        return self._ctx[key]
