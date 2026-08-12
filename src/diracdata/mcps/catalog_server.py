"""dirac-catalog-mcp — the catalog-aware MCP server.

Unified surface for BOTH observation (a query agent using the tools to answer
questions) AND learning-time authoring (Cursor's LLM using the tools to build
the fabric for a catalog's databases). Per Decision #4, this is ONE MCP server —
not two.

Compared to the existing schema-scoped diracdata-mcp:
  - takes --catalog <name> instead of --schema <name>
  - session state tracks a CURRENT database (set via use_database); every tool
    that operates per-DB defaults to it, but can be overridden per call
  - handles heterogeneous per-DB engines: for spider2_* catalogs, ATTACHes
    SQLite files (downloaded from MinIO on demand); for other catalogs, reuses
    the existing DuckDBEngine

Tool families:
  Navigation (observation):
    list_databases                 — [{db, table_count, size_mb, description}]
    describe_database              — full database.md content (or fallback)
    use_database                   — set session default; downloads SQLite if needed
    get_catalog_index              — full catalog.md content
    get_database_index             — full database.md content

  Per-DB observation:
    list_tables[db]                — every table in the DB
    describe_table[db]             — columns + types + row count + sample
    describe_column[db, table]     — column detail + nested-recipe if any
    sample_rows[db, table, n]      — quick data peek
    run_sql[db]                    — read-only DuckDB query (SQLite-attached if needed)
    find_examples[db]              — gold NL-SQL matching
    get_metric[db]                 — blessed metric lookup

  Learning-time authoring (called by Cursor's LLM to compile fabric):
    propose_table_description[db]
    propose_column_description[db]
    propose_join[db]
    propose_metric[db]
    save_semantic_model[db]        — flush proposals → semantic_model.yaml
    refresh_database_md[db]        — rewrite database.md via LLM
    refresh_catalog_md             — rewrite catalog.md via LLM
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

# ------------------------------- runtime ------------------------------------ #

class _CatalogRuntime:
    """State the tools call into. One instance per MCP server process."""

    def __init__(self, *, catalog: str, settings: Any, model: Any = None) -> None:
        from diracdata.config import settings_from_env  # local import to avoid heavy chain at module load
        from diracdata.stores import store_from_settings
        from diracdata.context.catalog_store import CatalogStore

        self.catalog = catalog
        self.settings = settings
        self.model = model
        self.store = store_from_settings(settings)
        self.cs = CatalogStore(self.store)
        self._current_db: Optional[str] = None
        # per-database DuckDB engines (lazy attach)
        self._engines: Dict[str, Any] = {}
        # accumulated authoring proposals per database (semantic_model deltas)
        self._proposals: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        # sqlite local cache dir (only used for engine='duckdb+sqlite' catalogs)
        self._sqlite_cache = Path(
            os.getenv("DIRACDATA_CATALOG_SQLITE_CACHE",
                       str(Path.home() / ".diracdata" / "catalog_sqlite_cache" / catalog))
        )
        self._sqlite_cache.mkdir(parents=True, exist_ok=True)

    # ---- session ----------------------------------------------------------

    def resolve_db(self, db: Optional[str]) -> str:
        db = (db or "").strip() or self._current_db
        if not db:
            raise RuntimeError("no database in session — call use_database(db) first, "
                               "or pass db= to this tool")
        return db

    def use_database(self, db: str) -> Dict:
        db = (db or "").strip()
        if not db:
            raise ValueError("db is required")
        available = self.cs.list_databases(self.catalog)
        if db not in available:
            raise ValueError(f"database {db!r} not in catalog {self.catalog!r}. "
                              f"available: {available}")
        # attach engine now to fail fast if it can't connect
        self._engine_for(db)
        with self._lock:
            self._current_db = db
        return {"catalog": self.catalog, "current_database": db}

    # ---- engines ----------------------------------------------------------

    def _engine_for(self, db: str) -> Any:
        """Lazily construct or fetch a DuckDB engine for a database."""
        with self._lock:
            if db in self._engines:
                return self._engines[db]

        if self._is_sqlite_catalog():
            eng = self._attach_sqlite_engine(db)
        else:
            # Object-store parquet lake — reuse existing DuckDBEngine (schema-shaped)
            from diracdata.engines import DuckDBEngine
            eng = DuckDBEngine.from_settings(self.settings, db)

        with self._lock:
            self._engines[db] = eng
        return eng

    def _is_sqlite_catalog(self) -> bool:
        """Catalogs whose databases are SQLite files (like spider2_local) need ATTACH-based engines."""
        # First check catalog.json (canonical) then catalog.yaml (back-compat) for engine hint
        cat_meta = (self.cs.get_catalog(self.catalog, "catalog.json", default={})
                     or self.cs.get_catalog(self.catalog, "catalog.yaml", default={}) or {})
        engine = (cat_meta.get("engine") or "").lower()
        if "sqlite" in engine:
            return True
        # Convention: catalogs starting with 'spider2' or containing 'sqlite' hold .sqlite blobs
        if self.catalog.startswith("spider2") or "sqlite" in self.catalog:
            return True
        return False

    def _attach_sqlite_engine(self, db: str):
        """Download the SQLite blob from MinIO if needed, then create a DuckDB engine that
        ATTACHes it as 'main'. Uses the same on-demand cache pattern as SpiderStore."""
        local = self._download_sqlite(db)
        # Minimal engine wrapper matching the shape callers use (query, list_tables)
        return _SqliteBackedDuckDB(local, db, max_rows=self.settings.query_max_rows)

    def _download_sqlite(self, db: str) -> Path:
        """Fetch a SQLite blob from the LAKE bucket (data lives in the data lake, not the
        fabric artifact store). Tries candidate keys in preference order and caches locally."""
        local = self._sqlite_cache / f"{db}.sqlite"
        if local.exists() and local.stat().st_size > 0:
            return local
        # SQLite blobs live in the data lake bucket. Try in-catalog convention first,
        # then the Spider convention (spider2/sqlite/<db>.sqlite).
        candidate_keys = [
            f"catalogs/{self.catalog}/sqlite/{db}.sqlite",
            f"{self.catalog}/sqlite/{db}.sqlite",
            f"spider2/sqlite/{db}.sqlite",
        ]
        import boto3
        from botocore.config import Config as BC
        s3 = boto3.client(
            "s3",
            endpoint_url=os.getenv("DIRACDATA_S3_ENDPOINT_URL", "http://localhost:9000"),
            aws_access_key_id=os.getenv("DIRACDATA_AWS_ACCESS_KEY_ID", "minioadmin"),
            aws_secret_access_key=os.getenv("DIRACDATA_AWS_SECRET_ACCESS_KEY", "minioadmin"),
            config=BC(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )
        lake_bucket = os.getenv("DIRACDATA_LAKE_BUCKET", "lake")
        for key in candidate_keys:
            try:
                data = s3.get_object(Bucket=lake_bucket, Key=key)["Body"].read()
            except Exception:
                continue
            local.write_bytes(data)
            return local
        raise FileNotFoundError(
            f"no SQLite blob found for {self.catalog}/{db} in bucket {lake_bucket!r} "
            f"(tried {candidate_keys})"
        )


class _SqliteBackedDuckDB:
    """DuckDB engine over one ATTACHed SQLite file. Presents just the surface the MCP tools use:
    .list_tables(), .query(sql, max_rows) → object with .columns + .rows."""

    def __init__(self, sqlite_path: Path, name: str, max_rows: int = 2000):
        import duckdb
        self._con = duckdb.connect(":memory:")
        self._con.execute("INSTALL sqlite; LOAD sqlite;")
        self._con.execute(f"ATTACH '{sqlite_path}' AS spider_db (TYPE SQLITE)")
        self._con.execute("USE spider_db")
        self.name = name
        self.max_rows = max_rows

    def list_tables(self) -> List[str]:
        try:
            rows = self._con.execute("SHOW TABLES").fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []

    def query(self, sql: str, max_rows: Optional[int] = None):
        limit = max_rows or self.max_rows
        result = self._con.execute(sql)
        rows = result.fetchmany(limit)
        cols = [d[0] for d in result.description] if result.description else []
        return _Res(cols, rows)


class _Res:
    def __init__(self, columns, rows):
        self.columns = columns
        self.rows = rows


# ------------------------------- tools -------------------------------------- #

def catalog_tools(rt: _CatalogRuntime) -> list:
    """Return the list of tool callables to register with the MCP server."""

    # -------- navigation (observation) --------

    def list_databases() -> str:
        """List every database in the current catalog with size, table count, and description.
        Call this FIRST when a question arrives to pick the right database."""
        dbs = rt.cs.list_databases(rt.catalog)
        out = []
        for db in dbs:
            db_yaml = (rt.cs.get(rt.catalog, db, "database.json", default={})
                        or rt.cs.get(rt.catalog, db, "database.yaml", default={}) or {})
            out.append({
                "database":    db,
                "table_count": db_yaml.get("table_count", "?"),
                "size_mb":     db_yaml.get("size_mb", "?"),
                "description": (db_yaml.get("description") or "")[:200],
            })
        return json.dumps({"catalog": rt.catalog, "count": len(out), "databases": out}, default=str)

    def use_database(db: str) -> str:
        """Set the CURRENT database for this session. Subsequent per-DB tools default to it.
        Fails if the database isn't in the catalog. For SQLite catalogs (like spider2), also
        downloads the SQLite blob from MinIO to a local cache on first use."""
        info = rt.use_database(db)
        return json.dumps(info)

    def get_catalog_index() -> str:
        """The full catalog.md — top-level hierarchical index (all databases with descriptions
        + table lists). ~5-30 KB. Read this FIRST to route to the right database(s)."""
        md = rt.cs.get_catalog_text(rt.catalog, "catalog.md")
        if not md:
            return f"(no catalog.md authored yet for {rt.catalog}; try refresh_catalog_md)"
        return md

    def get_database_index(db: str = "") -> str:
        """The full database.md for a database — table list with grain + 1-line description +
        key columns. ~2-10 KB. Read this once the agent has picked a database to dive into."""
        d = rt.resolve_db(db)
        md = rt.cs.get_text(rt.catalog, d, "database.md")
        if not md:
            return f"(no database.md authored yet for {rt.catalog}/{d}; try refresh_database_md db={d})"
        return md

    def describe_database(db: str = "") -> str:
        """One-shot description of a database — a compact form of get_database_index for framing.
        Returns first ~2K chars of the database.md, or database.yaml stats if md not authored."""
        d = rt.resolve_db(db)
        md = rt.cs.get_text(rt.catalog, d, "database.md")
        if md:
            return md[:2000] + ("\n..." if len(md) > 2000 else "")
        # Fallback: assemble from database.yaml
        y = (rt.cs.get(rt.catalog, d, "database.json", default={})
              or rt.cs.get(rt.catalog, d, "database.yaml", default={}) or {})
        return json.dumps({"database": d, "engine": y.get("engine", "?"),
                            "tables": y.get("table_count","?"), "size_mb": y.get("size_mb","?"),
                            "description": y.get("description","")})

    # -------- per-DB observation --------

    def list_tables(db: str = "") -> str:
        """List all tables in the given (or current) database."""
        d = rt.resolve_db(db)
        eng = rt._engine_for(d)
        try:
            tables = eng.list_tables()
            return json.dumps({"database": d, "count": len(tables), "tables": tables})
        except Exception as ex:
            return f"error listing tables in {d}: {ex}"

    def describe_table(table: str, db: str = "") -> str:
        """Full detail for one table: columns + types + row count + sample rows.
        For fabric-heavy catalogs, prefer the semantic model via search_context / describe_column."""
        d = rt.resolve_db(db)
        eng = rt._engine_for(d)
        try:
            schema = eng.query(f'DESCRIBE "{table}"')
            n = eng.query(f'SELECT COUNT(*) FROM "{table}"').rows[0][0]
            sample = eng.query(f'SELECT * FROM "{table}" LIMIT 5')
            return json.dumps({
                "database": d, "table": table, "row_count": int(n),
                "columns": [dict(zip(schema.columns, r)) for r in schema.rows],
                "sample_rows": [dict(zip(sample.columns, [str(v) for v in r])) for r in sample.rows],
            }, default=str)
        except Exception as ex:
            return f"error describing {d}.{table}: {ex}"

    def describe_column(table: str, column: str, db: str = "") -> str:
        """One column's business meaning, incl. any nested-type access recipe from the fabric."""
        d = rt.resolve_db(db)
        # Prefer fabric metadata_descriptions.json if present
        md = rt.cs.get(rt.catalog, d, "metadata_descriptions.json", default={}) or {}
        col_meta = ((md.get("tables") or {}).get(table) or {}).get("columns", {}).get(column)
        if col_meta:
            return json.dumps({"database": d, "table": table, "column": column, **col_meta}, default=str)
        # Fallback: live column stats
        eng = rt._engine_for(d)
        try:
            r = eng.query(f'SELECT COUNT(DISTINCT "{column}"), COUNT(*), '
                           f'MIN("{column}"), MAX("{column}") FROM "{table}"').rows[0]
            return json.dumps({"database": d, "table": table, "column": column,
                                "distinct_count": r[0], "row_count": r[1],
                                "min": str(r[2]), "max": str(r[3]),
                                "source": "engine-live (no fabric metadata yet)"}, default=str)
        except Exception as ex:
            return f"error describing {d}.{table}.{column}: {ex}"

    def sample_rows(table: str, db: str = "", n: int = 5) -> str:
        """Quick peek at n rows of a table."""
        d = rt.resolve_db(db)
        eng = rt._engine_for(d)
        try:
            r = eng.query(f'SELECT * FROM "{table}" LIMIT {int(n)}')
            return json.dumps({"database": d, "table": table,
                                "rows": [dict(zip(r.columns, [str(v) for v in row])) for row in r.rows]},
                                default=str)
        except Exception as ex:
            return f"error sampling {d}.{table}: {ex}"

    def run_sql(sql: str, db: str = "") -> str:
        """Execute a read-only SELECT against the current (or given) database. Bounded by
        query_max_rows. For SQLite-backed catalogs the query runs against the ATTACHed spider_db."""
        d = rt.resolve_db(db)
        eng = rt._engine_for(d)
        clean = (sql or "").strip().rstrip(";")
        try:
            res = eng.query(clean, rt.settings.query_max_rows)
            return json.dumps({"database": d, "columns": res.columns,
                                "rows": [list(r) for r in res.rows]}, default=str)
        except Exception as ex:
            return f"SQL error on {d}: {type(ex).__name__}: {ex}"

    def find_examples(query: str, db: str = "") -> str:
        """Find proven gold NL→SQL pairs matching a natural-language query in the given DB's fabric."""
        d = rt.resolve_db(db)
        gp = rt.cs.get(rt.catalog, d, "gold_pairs.json", default=[]) or []
        q = query.lower()
        hits = [g for g in gp if q in (g.get("nl_query","") or "").lower()]
        if not hits:
            return f"(no gold examples matching {query!r} in {d})"
        return json.dumps({"database": d, "matches": hits[:5]}, default=str)

    def get_metric(name: str = "", db: str = "") -> str:
        """Blessed metric SQL from the DB's semantic_layer.yaml. Empty name lists all metric names."""
        d = rt.resolve_db(db)
        sl_text = rt.cs.get_text(rt.catalog, d, "semantic_layer.yaml") or ""
        if not sl_text:
            return f"(no semantic layer for {d})"
        import yaml
        sl = yaml.safe_load(sl_text) or {}
        metrics = sl.get("metrics") or []
        if not name:
            return json.dumps({"database": d, "metric_names": [m.get("name") for m in metrics]})
        for m in metrics:
            if m.get("name") == name:
                return json.dumps({"database": d, "metric": m}, default=str)
        return f"(no metric {name!r} in {d})"

    # -------- authoring (for Cursor-driven learning) --------

    def propose_table_description(table: str, description: str, db: str = "") -> str:
        """Author or update a table's business description. Accumulates in memory until you call
        save_semantic_model(db). Use during Cursor-driven learning to build the fabric."""
        d = rt.resolve_db(db)
        with rt._lock:
            p = rt._proposals.setdefault(d, {"tables": {}, "columns": {}, "joins": [], "metrics": []})
            t = p["tables"].setdefault(table, {})
            t["description"] = description
        return json.dumps({"ok": True, "database": d, "table": table})

    def propose_column_description(table: str, column: str, description: str,
                                    access_recipe: str = "", db: str = "") -> str:
        """Author or update a column's description. access_recipe is optional and used for nested
        types (STRUCT/LIST/MAP/JSON — verbatim SQL to access the value). Accumulates until save."""
        d = rt.resolve_db(db)
        with rt._lock:
            p = rt._proposals.setdefault(d, {"tables": {}, "columns": {}, "joins": [], "metrics": []})
            c = p["columns"].setdefault(f"{table}.{column}", {})
            c["description"] = description
            if access_recipe:
                c["access_recipe"] = access_recipe
        return json.dumps({"ok": True, "database": d, "table": table, "column": column})

    def propose_join(left_table: str, left_col: str, right_table: str, right_col: str,
                     cardinality: str = "", disposition: str = "", db: str = "") -> str:
        """Author a join fact within a database. cardinality ∈ {'1-1','1-N','N-M','N-1'}.
        disposition ∈ {'INNER','LEFT','RIGHT','FULL'} — hint for planners. Accumulates until save."""
        d = rt.resolve_db(db)
        with rt._lock:
            p = rt._proposals.setdefault(d, {"tables": {}, "columns": {}, "joins": [], "metrics": []})
            p["joins"].append({"left_table": left_table, "left_col": left_col,
                                "right_table": right_table, "right_col": right_col,
                                "cardinality": cardinality, "disposition": disposition})
        return json.dumps({"ok": True, "database": d, "n_joins_pending": len(p["joins"])})

    def propose_metric(name: str, sql: str, description: str = "", grain: str = "", db: str = "") -> str:
        """Author a blessed metric — SQL + description + grain. Accumulates until save."""
        d = rt.resolve_db(db)
        with rt._lock:
            p = rt._proposals.setdefault(d, {"tables": {}, "columns": {}, "joins": [], "metrics": []})
            p["metrics"].append({"name": name, "sql": sql,
                                  "description": description, "grain": grain})
        return json.dumps({"ok": True, "database": d, "metric": name})

    def save_semantic_model(db: str = "") -> str:
        """Flush proposals for this database to fabric/catalogs/<cat>/databases/<db>/:
          - metadata_descriptions.json (tables + columns)
          - join_facts.json (joins)
          - semantic_layer.yaml (metrics — appended if a layer already exists)
        Returns a summary of what was written."""
        d = rt.resolve_db(db)
        with rt._lock:
            p = rt._proposals.get(d) or {"tables": {}, "columns": {}, "joins": [], "metrics": []}
        # Merge tables + columns into metadata_descriptions.json shape
        current = rt.cs.get(rt.catalog, d, "metadata_descriptions.json", default={}) or {"tables": {}, "columns": {}}
        for t, tdata in p["tables"].items():
            current.setdefault("tables", {}).setdefault(t, {}).update(tdata)
        for tc, cdata in p["columns"].items():
            table, col = tc.split(".", 1)
            current.setdefault("tables", {}).setdefault(table, {}).setdefault("columns", {}).setdefault(col, {}).update(cdata)
        rt.cs.put(rt.catalog, d, "metadata_descriptions.json", current)
        # Joins
        if p["joins"]:
            existing_joins = rt.cs.get(rt.catalog, d, "join_facts.json", default=[]) or []
            existing_joins.extend(p["joins"])
            rt.cs.put(rt.catalog, d, "join_facts.json", existing_joins)
        # Metrics — merge into semantic_layer.yaml (or create if absent)
        if p["metrics"]:
            import yaml
            current_sl_text = rt.cs.get_text(rt.catalog, d, "semantic_layer.yaml") or "metrics: []\n"
            sl = yaml.safe_load(current_sl_text) or {}
            sl.setdefault("metrics", []).extend(p["metrics"])
            rt.cs.put_text(rt.catalog, d, "semantic_layer.yaml", yaml.safe_dump(sl, sort_keys=False),
                            content_type="text/yaml")
        # Clear proposals for this DB
        with rt._lock:
            rt._proposals.pop(d, None)
        return json.dumps({
            "ok": True, "database": d,
            "tables_updated": len(p["tables"]),
            "columns_updated": len(p["columns"]),
            "joins_added": len(p["joins"]),
            "metrics_added": len(p["metrics"]),
        })

    def refresh_database_md(db: str = "") -> str:
        """Rewrite database.md via an LLM call using the freshly-saved semantic model.
        Requires FIREWORKS_API_KEY (or DIRACDATA_FIREWORKS_API_KEY) in env."""
        d = rt.resolve_db(db)
        from diracdata.learning.catalog_index import build_database_md
        llm = _make_fireworks_llm()
        md = build_database_md(rt.cs, catalog=rt.catalog, database=d, llm=llm)
        return json.dumps({"ok": True, "database": d, "chars": len(md), "preview": md[:400]})

    def refresh_catalog_md() -> str:
        """Rewrite catalog.md via an LLM call rolling up all databases' database.md files."""
        from diracdata.learning.catalog_index import build_catalog_md
        llm = _make_fireworks_llm()
        md = build_catalog_md(rt.cs, catalog=rt.catalog, llm=llm)
        return json.dumps({"ok": True, "catalog": rt.catalog, "chars": len(md), "preview": md[:400]})

    return [
        # navigation
        list_databases, use_database, get_catalog_index, get_database_index, describe_database,
        # observation
        list_tables, describe_table, describe_column, sample_rows, run_sql, find_examples, get_metric,
        # authoring
        propose_table_description, propose_column_description, propose_join, propose_metric,
        save_semantic_model, refresh_database_md, refresh_catalog_md,
    ]


# ---- helpers -------------------------------------------------------------- #

def _make_fireworks_llm(model_id: str = "accounts/fireworks/models/deepseek-v4-flash-0731"):
    """Small Fireworks-backed LLM used by refresh_*_md. Kept inline so the MCP has no LangChain dep."""
    from openai import OpenAI
    api_key = os.environ.get("FIREWORKS_API_KEY") or os.environ.get("DIRACDATA_FIREWORKS_API_KEY")
    if not api_key:
        raise RuntimeError("no FIREWORKS_API_KEY / DIRACDATA_FIREWORKS_API_KEY in env")
    client = OpenAI(base_url="https://api.fireworks.ai/inference/v1", api_key=api_key)
    def _call(prompt: str) -> str:
        resp = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": prompt}], temperature=0.1,
        )
        return resp.choices[0].message.content or ""
    return _call


# ---- entrypoint ----------------------------------------------------------- #

_INSTRUCTIONS = (
    "DiracData CATALOG MCP: this server exposes a whole data catalog (many databases). "
    "Recommended flow for querying: (1) get_catalog_index for the top-level map; (2) "
    "use_database(db) to pick one; (3) get_database_index + list_tables + describe_table; "
    "(4) run_sql to execute. For LEARNING (compiling fabric for a database): (5) list_tables "
    "then describe_table on each; (6) propose_table_description / propose_column_description / "
    "propose_join / propose_metric to author the fabric; (7) save_semantic_model when a DB is "
    "done; (8) refresh_database_md, then refresh_catalog_md at the end."
)


def catalog_mcp(*, catalog: str, env_file: str | None = None, model: Any = None,
                name: str = "dirac-catalog") -> Any:
    """Build the catalog-aware MCP server."""
    from diracdata.config import settings_from_env
    settings = settings_from_env(env_file)
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("catalog_mcp requires the MCP SDK") from exc

    rt = _CatalogRuntime(catalog=catalog, settings=settings, model=model)
    server = MCPServer(name=name, instructions=_INSTRUCTIONS)
    for fn in catalog_tools(rt):
        server.tool()(fn)
    return server
