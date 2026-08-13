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
    describe_table[db]             — columns + types + row count + sample (soft-fail)
    describe_column[db, table]     — column detail + nested-recipe if any
    sample_rows[db, table, n]      — quick data peek
    run_sql[db]                    — read-only DuckDB query (SQLite-attached if needed)
    find_examples[db]              — gold NL-SQL matching
    get_metric[db]                 — blessed metric lookup

    Harness (search / profile / verify — prefer these over browsing all markdown):
    search_fabric[query, db?]      — grep learned fabric text across DBs
    search_schema[pattern, db]     — find tables/columns by name pattern
    profile[target, db]            — table or table.column measured facts
    sql_diff[sql_a, sql_b, db]     — A/B two SELECTs (rowcount + numeric deltas)
    data_check[sql, db?]           — join orphan/fan-out/grain amplification + result sanity
    join_path[table?, db?]         — measured join cards (cardinality, children/parent)

  Learning-time authoring (called by Cursor's LLM to compile fabric):
    propose_table_description[db]
    propose_column_description[db]
    propose_join[db]
    propose_metric[db]
    save_semantic_model[db]        — flush proposals → semantic_model.yaml
    refresh_database_md[db]        — rewrite database.md via LLM
    refresh_catalog_md             — rewrite catalog.md via LLM

  Gates / dialect / compounding (MCP phases 1-5):
    completeness_check, fabric_health, metric_bind_check, get_dialect,
    clarify, save_experience, detect_boundary_convention, upsert_ontology

  Resources: index:// metric:// table:// example:// dialect:// ontology:// experiences://
  Prompts: learn-database, executive-scorecard
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

    def list_columns(self, table_name: str) -> List[str]:
        return [c["column_name"] for c in self.describe_columns(table_name)]

    def describe_columns(self, table_name: str) -> List[Dict[str, str]]:
        try:
            rows = self._con.execute(f'DESCRIBE "{table_name}"').fetchall()
            return [{"column_name": str(r[0]), "column_type": str(r[1])} for r in rows]
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
        Soft-fail: each step is independent; partial JSON + warnings on bad rows/types."""
        from diracdata.mcps.harness import bounded_query, describe_relation
        d = rt.resolve_db(db)
        eng = rt._engine_for(d)
        n = int(rt.settings.query_max_rows)
        ident = '"' + str(table).replace('"', '""') + '"'
        out: Dict[str, Any] = {"database": d, "table": table, "warnings": []}
        try:
            schema = describe_relation(eng, table, max_rows=n)
            out["columns"] = [dict(zip(schema.columns, r)) for r in schema.rows]
        except Exception as ex:
            out["warnings"].append(f"describe: {ex}")
            # Fabric fallback for column names when live DESCRIBE fails
            md = rt.cs.get(rt.catalog, d, "metadata_descriptions.json", default={}) or {}
            cols = ((md.get("tables") or {}).get(table) or {}).get("columns") or {}
            if cols:
                out["columns"] = [{"column_name": c, "source": "fabric"} for c in cols]
                out["warnings"].append("columns from fabric metadata_descriptions.json")
        try:
            out["row_count"] = int(bounded_query(eng, f"SELECT COUNT(*) FROM {ident}", 1).rows[0][0])
        except Exception as ex:
            out["warnings"].append(f"count: {ex}")
        try:
            sample = bounded_query(eng, f"SELECT * FROM {ident} LIMIT 5", 5)
            out["sample_rows"] = [
                dict(zip(sample.columns, [str(v) for v in r])) for r in sample.rows
            ]
        except Exception as ex:
            out["warnings"].append(f"sample: {ex}")
        if not out.get("columns") and not out.get("row_count") and not out.get("sample_rows"):
            return f"error describing {d}.{table}: {'; '.join(out['warnings']) or 'unknown'}"
        return json.dumps(out, default=str)

    def describe_column(table: str, column: str, db: str = "") -> str:
        """One column's business meaning + nested ACCESS RECIPE + verified runnable_example.

        Merges fabric metadata_descriptions with live engine stats. Complex columns
        without authored recipes get deterministic recipes from the type tree
        (UNNEST leaves + CTE-staged runnable example)."""
        from diracdata.mcps.harness import describe_column_enriched
        d = rt.resolve_db(db)
        try:
            eng = rt._engine_for(d)
            out = describe_column_enriched(
                rt.cs, rt.catalog, d, eng, table, column,
                max_rows=int(rt.settings.query_max_rows),
            )
            return json.dumps(out, default=str)
        except Exception as ex:
            return f"error describing {d}.{table}.{column}: {ex}"

    def sample_rows(table: str, db: str = "", n: int = 5) -> str:
        """Quick peek at n rows of a table."""
        from diracdata.mcps.harness import _ident, bounded_query
        d = rt.resolve_db(db)
        eng = rt._engine_for(d)
        cap = max(1, min(int(n), int(rt.settings.query_max_rows)))
        try:
            r = bounded_query(eng, f"SELECT * FROM {_ident(table)} LIMIT {cap}", cap)
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

    def data_check(sql: str, db: str = "") -> str:
        """Verify a DRAFT multi-table query before trusting its number.
        DATA QUALITY on inputs (null rates, join orphan %, fan-out, children-per-parent grain
        amplification) + SANITY on the output. Call on every multi-table draft."""
        from diracdata.utils.sql import validate_sql
        from diracdata.utils.stewardship import probe_footprint, sanity_check
        d = rt.resolve_db(db)
        eng = rt._engine_for(d)
        clean = (sql or "").strip().rstrip(";")
        try:
            tables = set(eng.list_tables() or [])
        except Exception:  # noqa: BLE001
            tables = set()
        dq = probe_footprint(eng, clean)
        result = None
        if validate_sql(clean, available_tables=tables).get("status") == "ok":
            try:
                r = eng.query(clean, rt.settings.query_max_rows)
                result = {"columns": r.columns, "rows": [list(x) for x in r.rows],
                          "row_count": len(r.rows)}
            except Exception:  # noqa: BLE001
                result = None
        sanity = sanity_check(clean, result) if result is not None else {}
        flags = list((dq or {}).get("flags") or []) + list((sanity or {}).get("flags") or [])
        return json.dumps({
            "database": d,
            "ok": not flags,
            "flags": flags,
            "data_quality": dq,
            "sanity": sanity,
        }, default=str)

    def join_path(table: str = "", db: str = "") -> str:
        """Measured join cards for a table (or all joins if table empty): cardinality,
        match rate, children-per-parent amplification. ALWAYS call before joining."""
        from diracdata.mcps.harness import join_path_cards
        d = rt.resolve_db(db)
        eng = None
        try:
            eng = rt._engine_for(d)
        except Exception:  # noqa: BLE001
            eng = None
        try:
            return json.dumps(join_path_cards(rt.cs, rt.catalog, d, table=table or "",
                                              engine=eng), default=str)
        except Exception as ex:
            return f"join_path error on {d}: {ex}"

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
        """Blessed metric SQL from the DB's semantic_layer.yaml. Empty name lists all metric names.
        Metric payloads may include companions[], binding_status, boundary_convention."""
        d = rt.resolve_db(db)
        sl_text = rt.cs.get_text(rt.catalog, d, "semantic_layer.yaml") or ""
        if not sl_text:
            return f"(no semantic layer for {d})"
        import yaml
        sl = yaml.safe_load(sl_text) or {}
        metrics = sl.get("metrics") or []
        if isinstance(metrics, dict):
            metrics = [{"name": k, **(v if isinstance(v, dict) else {})} for k, v in metrics.items()]
        if not name:
            return json.dumps({"database": d, "metric_names": [
                (m.get("name") if isinstance(m, dict) else m) for m in metrics
            ]})
        for m in metrics:
            if isinstance(m, dict) and m.get("name") == name:
                return json.dumps({"database": d, "metric": m,
                                   "companions": m.get("companions") or []}, default=str)
        return f"(no metric {name!r} in {d})"

    # -------- harness (search / profile / verify) --------

    def search_fabric(query: str, db: str = "", limit: int = 25) -> str:
        """Grep learned fabric (catalog.md, database.md, metadata, joins, metrics, gold) for
        query tokens. Pass db= to scope one database; omit to scan the whole catalog.
        Prefer this over reading full catalog.md / database.md when routing a question."""
        from diracdata.mcps.harness import search_fabric as _search
        d = (db or "").strip() or None
        if d:
            d = rt.resolve_db(d)
        try:
            return json.dumps(_search(rt.cs, rt.catalog, query, db=d, limit=int(limit) or 25),
                              default=str)
        except Exception as ex:
            return f"search_fabric error: {ex}"

    def search_schema(pattern: str, db: str = "", limit: int = 50) -> str:
        """Find tables/columns whose names match pattern (substring or *glob*). Uses live
        DESCRIBE with fabric metadata fallback. Requires a database (session or db=)."""
        from diracdata.mcps.harness import search_schema as _search
        d = rt.resolve_db(db)
        eng = rt._engine_for(d)
        meta = rt.cs.get(rt.catalog, d, "metadata_descriptions.json", default={}) or {}
        try:
            out = _search(eng, pattern, limit=int(limit) or 50, fabric_meta=meta,
                          max_rows=int(rt.settings.query_max_rows))
            out["database"] = d
            return json.dumps(out, default=str)
        except Exception as ex:
            return f"search_schema error on {d}: {ex}"

    def profile(target: str, db: str = "") -> str:
        """Measured facts for `table` or `table.column` (row_count, distinct, null_pct, min/max,
        domain sample). Use before writing filters/aggregates when enums or ranges are unclear."""
        from diracdata.mcps.harness import profile_target
        d = rt.resolve_db(db)
        eng = rt._engine_for(d)
        try:
            out = profile_target(eng, target, max_rows=int(rt.settings.query_max_rows))
            out["database"] = d
            return json.dumps(out, default=str)
        except Exception as ex:
            return f"profile error on {d}.{target}: {ex}"

    def sql_diff(sql_a: str, sql_b: str, db: str = "") -> str:
        """Run two read-only SELECTs and compare row counts + numeric column sums.
        Use when a measure is ambiguous (e.g. spend with vs without discount)."""
        from diracdata.mcps.harness import sql_diff as _diff
        d = rt.resolve_db(db)
        eng = rt._engine_for(d)
        try:
            out = _diff(eng, sql_a, sql_b, max_rows=rt.settings.query_max_rows)
            out["database"] = d
            return json.dumps(out, default=str)
        except Exception as ex:
            return f"sql_diff error on {d}: {ex}"

    # -------- authoring (for Cursor-driven learning) --------

    def propose_table_description(table: str, description: str, db: str = "",
                                   grain: str = "", scd_type: str = "",
                                   time_column: str = "", cutoff_notes: str = "") -> str:
        """Author or update a table's business description (+ optional grain/scd/time fields).
        Accumulates until save_semantic_model(db)."""
        from diracdata.mcps.fabric_fields import enrich_table_fields
        d = rt.resolve_db(db)
        with rt._lock:
            p = rt._proposals.setdefault(d, {"tables": {}, "columns": {}, "joins": [], "metrics": []})
            t = p["tables"].setdefault(table, {})
            t.update(enrich_table_fields(
                {"description": description},
                grain=grain or None, scd_type=scd_type or None,
                time_column=time_column or None, cutoff_notes=cutoff_notes or None,
            ))
        return json.dumps({"ok": True, "database": d, "table": table})

    def propose_column_description(table: str, column: str, description: str,
                                    access_recipe: str = "", db: str = "",
                                    null_meaning: str = "", sentinel_values: str = "",
                                    boundary_convention: str = "") -> str:
        """Author or update a column's description. access_recipe for nested types.
        Optional: null_meaning, sentinel_values (comma-separated), boundary_convention."""
        from diracdata.mcps.fabric_fields import enrich_column_fields
        d = rt.resolve_db(db)
        sentinels = [s.strip() for s in sentinel_values.split(",") if s.strip()] if sentinel_values else None
        with rt._lock:
            p = rt._proposals.setdefault(d, {"tables": {}, "columns": {}, "joins": [], "metrics": []})
            c = p["columns"].setdefault(f"{table}.{column}", {})
            c.update(enrich_column_fields(
                {"description": description},
                null_meaning=null_meaning or None,
                sentinel_values=sentinels,
                boundary_convention=boundary_convention or None,
            ))
            if access_recipe:
                c["access_recipe"] = access_recipe
        return json.dumps({"ok": True, "database": d, "table": table, "column": column})

    def verify_join(left_table: str, left_col: str, right_table: str, right_col: str,
                    db: str = "") -> str:
        """LEARN: measure a candidate join (orphan %, fan-out, children/parent, verdict).
        Call before propose_join — reject edges that match nothing or explode."""
        from diracdata.mcps.harness import measure_join
        d = rt.resolve_db(db)
        eng = rt._engine_for(d)
        return json.dumps({"database": d, **measure_join(
            eng, left_table, left_col, right_table, right_col
        )}, default=str)

    def propose_join(left_table: str, left_col: str, right_table: str, right_col: str,
                     cardinality: str = "", disposition: str = "", db: str = "") -> str:
        """Author a join fact within a database. ALWAYS measures the edge live first.
        Rejects when matched_rows=0. Attaches measured cardinality/disposition/orphan/fan-out.
        cardinality/disposition args are optional overrides after measurement."""
        from diracdata.mcps.harness import measure_join
        d = rt.resolve_db(db)
        eng = rt._engine_for(d)
        measured = measure_join(eng, left_table, left_col, right_table, right_col)
        if measured.get("verdict") == "reject":
            return json.dumps({
                "ok": False, "database": d, "rejected": True,
                "reason": measured.get("reason") or "join matched 0 rows",
                "measurement": measured,
            }, default=str)
        card = cardinality or measured.get("cardinality_measured") or ""
        disp = disposition or measured.get("disposition") or ""
        edge = {
            "left_table": left_table, "left_col": left_col,
            "right_table": right_table, "right_col": right_col,
            "cardinality": card, "disposition": disp,
            "match_rate": measured.get("match_rate"),
            "fan_out_avg": measured.get("fan_out"),
            "children_per_parent_avg": measured.get("children_per_parent_avg"),
            "verified_by": measured.get("verified_by"),
            "orphan_pct": measured.get("orphan_pct"),
        }
        warnings = []
        cpp = measured.get("children_per_parent_avg") or 0
        if cpp > 1.5:
            warnings.append(
                f"~{cpp}x children/parent — aggregate-then-join before averaging parent measures"
            )
        with rt._lock:
            p = rt._proposals.setdefault(d, {"tables": {}, "columns": {}, "joins": [], "metrics": []})
            p["joins"].append(edge)
        return json.dumps({
            "ok": True, "database": d, "n_joins_pending": len(p["joins"]),
            "measurement": measured, "warnings": warnings, "edge": edge,
        }, default=str)

    def propose_metric(name: str, sql: str, description: str = "", grain: str = "", db: str = "",
                       companions: str = "", boundary_convention: str = "") -> str:
        """Author a blessed metric — SQL + description + grain.
        Optional companions (comma-separated metric names) and boundary_convention."""
        from diracdata.mcps.fabric_fields import enrich_metric_fields
        d = rt.resolve_db(db)
        comps = [c.strip() for c in companions.split(",") if c.strip()] if companions else None
        metric = enrich_metric_fields(
            {"name": name, "sql": sql, "description": description, "grain": grain},
            companions=comps, boundary_convention=boundary_convention or None,
            binding_status="proposed",
        )
        with rt._lock:
            p = rt._proposals.setdefault(d, {"tables": {}, "columns": {}, "joins": [], "metrics": []})
            p["metrics"].append(metric)
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

    # -------- gates / dialect / compounding (phases 1-5) --------

    def completeness_check(db: str = "", bind_metrics: bool = True) -> str:
        """Fabric completion gate for one database. Do NOT claim LEARN done unless ok=true.
        Returns tables_missing_grain, columns_missing_desc, metrics_unbound, joins_without_facts,
        indexes_stub, missing_database_md."""
        from diracdata.mcps.completeness import completeness_check as _cc
        d = rt.resolve_db(db)

        def _text(db_: str, name: str):
            return rt.cs.get_text(rt.catalog, db_, name)

        def _json(db_: str, name: str):
            return rt.cs.get(rt.catalog, db_, name, default=None)

        eng = None
        tables_fn = None
        if bind_metrics:
            try:
                eng = rt._engine_for(d)
                tables_fn = lambda db_: eng.list_tables()  # noqa: E731
            except Exception:  # noqa: BLE001
                eng = None
        report = _cc(
            db=d, get_text=_text, get_json=_json,
            list_tables=tables_fn, bind_metrics=bool(bind_metrics and eng),
            engine=eng,
        )
        return json.dumps(report, default=str)

    def fabric_health(bind_metrics: bool = False) -> str:
        """Catalog-level health: stub catalog.md, empty DBs, per-DB completeness rollup."""
        from diracdata.mcps.completeness import fabric_health as _fh

        def _text(db_: str, name: str):
            return rt.cs.get_text(rt.catalog, db_, name)

        def _json(db_: str, name: str):
            return rt.cs.get(rt.catalog, db_, name, default=None)

        def _eng(db_: str):
            if not bind_metrics:
                return None
            try:
                return rt._engine_for(db_)
            except Exception:  # noqa: BLE001
                return None

        report = _fh(
            catalog=rt.catalog,
            list_dbs=lambda: rt.cs.list_databases(rt.catalog),
            get_catalog_text=lambda name: rt.cs.get_catalog_text(rt.catalog, name),
            get_text=_text, get_json=_json,
            engine_for=_eng if bind_metrics else None,
        )
        return json.dumps(report, default=str)

    def metric_bind_check(name: str = "", sql: str = "", db: str = "") -> str:
        """Validate metric SQL binds against the live engine (LIMIT 0 probe).
        Pass name= to load SQL from semantic_layer, or sql= directly."""
        from diracdata.mcps.bind_check import metric_bind_check as _mb
        d = rt.resolve_db(db)
        probe_sql = (sql or "").strip()
        if not probe_sql and name:
            import yaml
            sl = yaml.safe_load(rt.cs.get_text(rt.catalog, d, "semantic_layer.yaml") or "") or {}
            metrics = sl.get("metrics") or []
            if isinstance(metrics, dict):
                m = metrics.get(name) or {}
                probe_sql = (m.get("sql") if isinstance(m, dict) else "") or ""
            else:
                for m in metrics:
                    if isinstance(m, dict) and m.get("name") == name:
                        probe_sql = m.get("sql") or ""
                        break
        if not probe_sql:
            return json.dumps({"ok": False, "parse_error": "no sql", "name": name, "database": d})
        eng = rt._engine_for(d)
        out = _mb(eng, probe_sql)
        out["name"] = name
        out["database"] = d
        return json.dumps(out, default=str)

    def get_dialect(db: str = "") -> str:
        """Dialect card + cheat-sheet for this database's engine. Call before first run_sql."""
        from diracdata.mcps.dialect import dialect_card
        d = (db or "").strip() or rt._current_db or ""
        kind = "duckdb+sqlite" if rt._is_sqlite_catalog() else "duckdb"
        card = dialect_card(engine_kind=kind)
        card["catalog"] = rt.catalog
        card["database"] = d
        return json.dumps(card, default=str)

    def clarify(question: str, options: str = "", context: str = "") -> str:
        """Ambiguity handler when metric/period/database is unclear. Returns a structured
        elicitation payload for the host to present to the user — do not guess silently.
        options: comma-separated choices when known."""
        opts = [o.strip() for o in options.split(",") if o.strip()] if options else []
        return json.dumps({
            "needs_elicitation": True,
            "question": question,
            "options": opts,
            "context": context or "",
            "hint": "Present these choices to the user, then continue with their answer.",
        })

    def save_experience(insight: str, evidence: str = "", section: str = "GOTCHAS",
                        db: str = "") -> str:
        """Persist a durable analyst gotcha/binding into experiences.md for this database."""
        from diracdata.mcps.experiences import save_experience as _se
        d = rt.resolve_db(db)
        cur = rt.cs.get_text(rt.catalog, d, "experiences.md") or ""
        new = _se(cur, insight=insight, evidence=evidence, section=section)
        rt.cs.put_text(rt.catalog, d, "experiences.md", new, content_type="text/markdown")
        return json.dumps({"ok": True, "database": d, "section": section, "chars": len(new)})

    def detect_boundary_convention(column: str, sample_values: str = "",
                                   profile_hint: str = "") -> str:
        """Learn-time heuristic for threshold/bucket edge inclusivity.
        sample_values: comma-separated examples."""
        from diracdata.mcps.boundary import detect_boundary_convention as _db
        samples = [s.strip() for s in sample_values.split(",") if s.strip()] if sample_values else []
        return json.dumps(_db(column, samples, profile_hint=profile_hint or None), default=str)

    def upsert_ontology(db: str = "", ontology_yaml: str = "", channel_maps_yaml: str = "") -> str:
        """Write ontology.yaml and/or channel_maps.yaml for a database (YAML text bodies)."""
        d = rt.resolve_db(db)
        written = []
        if ontology_yaml.strip():
            rt.cs.put_text(rt.catalog, d, "ontology.yaml", ontology_yaml, content_type="text/yaml")
            written.append("ontology.yaml")
        if channel_maps_yaml.strip():
            rt.cs.put_text(rt.catalog, d, "channel_maps.yaml", channel_maps_yaml,
                           content_type="text/yaml")
            written.append("channel_maps.yaml")
        if not written:
            from diracdata.mcps.ontology import empty_channel_maps, empty_ontology
            import yaml
            if not rt.cs.get_text(rt.catalog, d, "ontology.yaml"):
                rt.cs.put_text(rt.catalog, d, "ontology.yaml",
                               yaml.safe_dump(empty_ontology()), content_type="text/yaml")
                written.append("ontology.yaml")
            if not rt.cs.get_text(rt.catalog, d, "channel_maps.yaml"):
                rt.cs.put_text(rt.catalog, d, "channel_maps.yaml",
                               yaml.safe_dump(empty_channel_maps()), content_type="text/yaml")
                written.append("channel_maps.yaml")
        return json.dumps({"ok": True, "database": d, "written": written})

    return [
        # navigation
        list_databases, use_database, get_catalog_index, get_database_index, describe_database,
        # observation
        list_tables, describe_table, describe_column, sample_rows, run_sql, find_examples, get_metric,
        # harness
        search_fabric, search_schema, profile, sql_diff, data_check, join_path,
        # authoring
        propose_table_description, propose_column_description, propose_join, propose_metric,
        verify_join, save_semantic_model, refresh_database_md, refresh_catalog_md,
        # gates / dialect / compounding
        completeness_check, fabric_health, metric_bind_check, get_dialect,
        clarify, save_experience, detect_boundary_convention, upsert_ontology,
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

def catalog_mcp(*, catalog: str, env_file: str | None = None, model: Any = None,
                name: str = "dirac-catalog") -> Any:
    """Build the catalog-aware MCP server (tools + resources + prompts)."""
    from diracdata.config import settings_from_env
    from diracdata.mcps.instructions import catalog_instructions
    from diracdata.mcps.prompts_mcp import PROMPTS
    from diracdata.mcps.register import catalog_resource_body
    settings = settings_from_env(env_file)
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("catalog_mcp requires the MCP SDK") from exc

    rt = _CatalogRuntime(catalog=catalog, settings=settings, model=model)
    server = MCPServer(name=name, instructions=catalog_instructions())
    for fn in catalog_tools(rt):
        server.tool()(fn)

    # Resources (noun-shaped). Templates use {param} segments.
    @server.resource("index://{catalog_id}")
    def _res_index_catalog(catalog_id: str) -> str:
        return catalog_resource_body(rt, f"index://{catalog_id}")

    @server.resource("index://{catalog_id}/{db}")
    def _res_index_db(catalog_id: str, db: str) -> str:
        return catalog_resource_body(rt, f"index://{catalog_id}/{db}")

    @server.resource("dialect://{catalog_id}/{db}")
    def _res_dialect(catalog_id: str, db: str) -> str:
        return catalog_resource_body(rt, f"dialect://{catalog_id}/{db}")

    @server.resource("metric://{catalog_id}/{db}/{name}")
    def _res_metric(catalog_id: str, db: str, name: str) -> str:
        return catalog_resource_body(rt, f"metric://{catalog_id}/{db}/{name}")

    @server.resource("table://{catalog_id}/{db}/{table}")
    def _res_table(catalog_id: str, db: str, table: str) -> str:
        return catalog_resource_body(rt, f"table://{catalog_id}/{db}/{table}")

    @server.resource("example://{catalog_id}/{db}/{eid}")
    def _res_example(catalog_id: str, db: str, eid: str) -> str:
        return catalog_resource_body(rt, f"example://{catalog_id}/{db}/{eid}")

    @server.resource("ontology://{catalog_id}/{db}")
    def _res_ontology(catalog_id: str, db: str) -> str:
        return catalog_resource_body(rt, f"ontology://{catalog_id}/{db}")

    @server.resource("experiences://{catalog_id}/{db}")
    def _res_experiences(catalog_id: str, db: str) -> str:
        return catalog_resource_body(rt, f"experiences://{catalog_id}/{db}")

    @server.prompt(name="learn-database", title=PROMPTS["learn-database"]["title"],
                   description=PROMPTS["learn-database"]["description"])
    def _prompt_learn(database: str = "") -> str:
        return PROMPTS["learn-database"]["fn"](database)

    @server.prompt(name="executive-scorecard", title=PROMPTS["executive-scorecard"]["title"],
                   description=PROMPTS["executive-scorecard"]["description"])
    def _prompt_scorecard(year_hint: str = "most recent complete year") -> str:
        from diracdata.mcps.prompts_mcp import prompt_executive_scorecard
        return prompt_executive_scorecard(year_hint, surface="catalog")

    return server


# Back-compat alias if anything imported the old constant
from diracdata.mcps.instructions import catalog_instructions as _ci
_INSTRUCTIONS = _ci()