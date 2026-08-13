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

    # ---- silent-distortion checks (calendar-side of stewardship) ---------------------------------
    def temporal_coverage(a: str, b: str) -> str:
        """Compare the date span of two 'table.column' refs (e.g. 'orders.order_date' vs
        'stock_levels.snapshot_date'). Warns when they don't overlap, or overlap only partially --
        the silent trap where a join looks fine but returns a nearest-day proxy or drops rows for
        the period actually asked about. Call BEFORE joining two time-bearing tables in a query
        with an implied period ('during the campaign', 'over the same window')."""
        from diracdata.context.temporal import temporal_coverage as _tc
        return json.dumps(_tc(rt.engine, a, b), default=str)

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
                # Use the MCP session engine (honours --data local sources) instead of
                # rebuilding a lake engine that may not match the served source.
                out = Learner(schema=sch, model=rt.model, settings=rt.settings,
                              engine=rt.engine).learn()
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

    def get_dialect(schema: str = "") -> str:
        """Dialect card + cheat-sheet. Call before first run_sql."""
        from diracdata.mcps.dialect import dialect_from_engine
        card = dialect_from_engine(rt.engine)
        card["schema"] = schema or rt.default_schema
        return json.dumps(card, default=str)

    def metric_bind_check(name: str = "", sql: str = "", schema: str = "") -> str:
        """Validate metric SQL against the live engine (LIMIT 0 bind probe)."""
        from diracdata.mcps.bind_check import metric_bind_check as _mb
        sch = schema or rt.default_schema
        probe = (sql or "").strip()
        if not probe and name:
            m = None
            try:
                m = rt.ctx(sch).workspace.metric(name)
            except Exception:  # noqa: BLE001
                m = None
            if isinstance(m, dict):
                probe = m.get("sql") or m.get("expr") or m.get("definition") or ""
            if not probe:
                # Fall back to semantic_model / semantic_layer YAML in the fabric store
                try:
                    from diracdata.context.fabric import context_store_from_settings
                    import yaml
                    store = context_store_from_settings(rt.settings)
                    for art in ("semantic_layer.yaml", "semantic_model.yaml"):
                        raw = store.read_text(sch, art) or ""
                        if not raw:
                            continue
                        sl = yaml.safe_load(raw) or {}
                        metrics = sl.get("metrics") or {}
                        if isinstance(metrics, dict) and name in metrics:
                            entry = metrics[name]
                            probe = (entry.get("sql") if isinstance(entry, dict) else "") or ""
                        elif isinstance(metrics, list):
                            for entry in metrics:
                                if isinstance(entry, dict) and entry.get("name") == name:
                                    probe = entry.get("sql") or ""
                                    break
                        if probe:
                            break
                except Exception:  # noqa: BLE001
                    pass
            if not probe:
                raw = rt.ctx(sch).metric(name)
                if isinstance(raw, str) and "SELECT" in raw.upper():
                    # crude extract: last SQL-looking chunk
                    for line in raw.splitlines():
                        if "SELECT" in line.upper() or line.strip().upper().startswith("WITH"):
                            probe = line.strip()
                            break
        if not probe:
            return json.dumps({"ok": False, "parse_error": "no sql", "name": name, "schema": sch})
        out = _mb(rt.engine, probe)
        out["name"] = name
        out["schema"] = sch
        return json.dumps(out, default=str)

    def completeness_check(schema: str = "", bind_metrics: bool = True) -> str:
        """Schema fabric completion gate (metadata + optional metric bind)."""
        from diracdata.mcps.completeness import completeness_check as _cc
        sch = schema or rt.default_schema
        store = None
        try:
            from diracdata.context.fabric import context_store_from_settings
            store = context_store_from_settings(rt.settings)
        except Exception:  # noqa: BLE001
            store = None

        def _text(_db: str, name: str):
            if store is None:
                return None
            # ContextStore.read_text(schema, name) — not a raw key
            try:
                return store.read_text(sch, name)
            except TypeError:
                return None
            except Exception:  # noqa: BLE001
                return None

        def _json(_db: str, name: str):
            if store is None:
                return None
            try:
                return store.get(sch, name)
            except Exception:  # noqa: BLE001
                return None

        def _text_metrics_aware(_db: str, name: str):
            """Map catalog-oriented names onto schema fabric artifacts."""
            if name in ("semantic_layer.yaml", "semantic_layer.yml"):
                for cand in ("semantic_layer.yaml", "semantic_layer.yml", "semantic_model.yaml"):
                    t = _text(_db, cand)
                    if t:
                        return t
                # fall back to workspace semantic layer
                try:
                    sl = rt.ctx(sch).workspace.semantic_layer or {}
                    if sl:
                        import yaml
                        return yaml.safe_dump(sl)
                except Exception:  # noqa: BLE001
                    pass
                return None
            if name == "database.md":
                # Schema MCP has no database.md; synthesize a non-stub index from tables().
                try:
                    body = rt.ctx(sch).tables()
                    if body and len(str(body)) >= 200:
                        return "# " + sch + "\n\n" + str(body)
                except Exception:  # noqa: BLE001
                    pass
                return _text(_db, name)
            if name == "join_facts.json":
                # may live only inside semantic_model / workspace
                return None
            return _text(_db, name)

        def _json_aware(_db: str, name: str):
            if name == "metadata_descriptions.json":
                meta = _json(_db, name)
                if meta:
                    return meta
                try:
                    ws = rt.ctx(sch).workspace
                    # Workspace may expose descriptions via loaded fabric
                    md = getattr(ws, "metadata_descriptions", None)
                    if md:
                        return md if isinstance(md, dict) else {"tables": md}
                except Exception:  # noqa: BLE001
                    pass
                return None
            if name == "join_facts.json":
                j = _json(_db, name)
                if j is not None:
                    return j
                try:
                    ws = rt.ctx(sch).workspace
                    joins = getattr(ws, "joins", None)
                    if joins is None and hasattr(ws, "semantic_model"):
                        joins = getattr(ws.semantic_model, "joins", None)
                    # semantic_model.yaml joins often only in YAML text
                    if not joins:
                        import yaml
                        raw = _text(_db, "semantic_model.yaml") or ""
                        sm = yaml.safe_load(raw) or {}
                        joins = sm.get("joins") or []
                    return joins or []
                except Exception:  # noqa: BLE001
                    return []
            return _json(_db, name)

        report = _cc(
            db=sch, get_text=_text_metrics_aware, get_json=_json_aware,
            list_tables=lambda _d: list(rt.engine.list_tables()),
            bind_metrics=bind_metrics, engine=rt.engine if bind_metrics else None,
        )
        report["schema"] = sch
        return json.dumps(report, default=str)

    def fabric_health(schema: str = "") -> str:
        """Lightweight schema health wrapper around completeness_check."""
        return completeness_check(schema=schema, bind_metrics=True)

    def clarify(question: str, options: str = "", context: str = "") -> str:
        """Ambiguity handler — returns needs_elicitation payload; do not silently guess."""
        opts = [o.strip() for o in options.split(",") if o.strip()] if options else []
        return json.dumps({
            "needs_elicitation": True,
            "question": question,
            "options": opts,
            "context": context or "",
        })

    def save_experience(insight: str, evidence: str = "", section: str = "GOTCHAS",
                        schema: str = "") -> str:
        """Persist a durable gotcha into schema experiences.md."""
        from diracdata.mcps.experiences import save_experience as _se
        from diracdata.context.fabric import context_store_from_settings
        sch = schema or rt.default_schema
        store = context_store_from_settings(rt.settings)
        cur = store.read_text(sch, "experiences.md") or ""
        new = _se(cur, insight=insight, evidence=evidence, section=section)
        # ContextStore has no put_text — write via underlying store
        store._store.write_text(store._fabric_key(sch, "experiences.md"), new,
                                content_type="text/markdown")
        return json.dumps({"ok": True, "schema": sch, "chars": len(new)})

    def detect_boundary_convention(column: str, sample_values: str = "",
                                   profile_hint: str = "") -> str:
        """Learn-time threshold/bucket inclusivity heuristic."""
        from diracdata.mcps.boundary import detect_boundary_convention as _db
        samples = [s.strip() for s in sample_values.split(",") if s.strip()] if sample_values else []
        return json.dumps(_db(column, samples, profile_hint=profile_hint or None), default=str)

    return [list_tables, describe_table, describe_column, search_context, join_path, get_metric,
            find_examples, run_sql, data_check, attribute, temporal_coverage,
            learn_schema, learn_status,
            get_dialect, metric_bind_check, completeness_check, fabric_health,
            clarify, save_experience, detect_boundary_convention]
