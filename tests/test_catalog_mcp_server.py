"""Unit tests for dirac-catalog-mcp — the catalog-aware MCP server.

Uses an in-memory Store + a fake SQLite (via a temp file) — no MinIO, no LLM.
Verifies the runtime + tool wiring: navigation reads catalog/database stubs,
authoring proposals accumulate + flush to the fabric layout, and the tool set
exposes exactly the documented tool families (nav + observation + harness + authoring).
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import pytest

from diracdata.mcps.catalog_server import _CatalogRuntime, catalog_tools


class _MemStore:
    def __init__(self) -> None:
        self.blobs: Dict[str, bytes] = {}
    def exists(self, key: str) -> bool: return key in self.blobs
    def read_json(self, key: str) -> Any: return json.loads(self.blobs[key].decode())
    def read_text(self, key: str) -> str: return self.blobs[key].decode()
    def write_json(self, key: str, obj: Any) -> None:
        self.blobs[key] = json.dumps(obj).encode()
    def write_text(self, key: str, text: str, content_type: str = "text/plain") -> None:
        self.blobs[key] = text.encode()
    def list_keys(self, prefix: str) -> List[str]:
        return sorted(k for k in self.blobs if k.startswith(prefix))
    def delete(self, key: str) -> None: self.blobs.pop(key, None)


@dataclass
class _FakeSettings:
    query_max_rows: int = 100
    preview_rows: int = 100
    preview_all_max: int = 200
    reconciler_memory_limit: str = "1GB"
    reconciler_temp_dir: str = "/tmp"
    reconciler_threads: int = 2


def _make_runtime(tmp_path: Path, catalog: str = "test_cat") -> _CatalogRuntime:
    """Build a _CatalogRuntime whose underlying store is an in-memory MemStore.
    Monkeypatched via the same _CatalogRuntime __init__ path (we replace its .store)."""
    st = _MemStore()
    # Seed a minimal fabric layout the runtime + tools can navigate
    st.write_json(f"fabric/catalogs/{catalog}/catalog.json",
                   {"name": catalog, "engine": "duckdb", "description": "test catalog"})
    for db in ("db_a", "db_b"):
        st.write_json(f"fabric/catalogs/{catalog}/databases/{db}/database.json",
                       {"name": db, "catalog": catalog, "engine": "sqlite",
                        "size_mb": 1, "description": f"desc {db}"})

    rt = _CatalogRuntime.__new__(_CatalogRuntime)
    rt.catalog = catalog
    rt.settings = _FakeSettings()
    rt.model = None
    rt.store = st
    from diracdata.context.catalog_store import CatalogStore
    rt.cs = CatalogStore(st)
    rt._current_db = None
    rt._engines = {}
    rt._proposals = {}
    import threading
    rt._lock = threading.Lock()
    rt._sqlite_cache = tmp_path / "cache"
    rt._sqlite_cache.mkdir(parents=True, exist_ok=True)
    return rt


def test_catalog_tools_exposes_expected_families(tmp_path):
    rt = _make_runtime(tmp_path)
    tools = catalog_tools(rt)
    names = {t.__name__ for t in tools}
    # nav(5) + obs(7) + harness(6) + authoring(8) + gates(8) = 34
    assert len(tools) == 34
    # Navigation
    for n in ("list_databases", "use_database", "get_catalog_index",
              "get_database_index", "describe_database"):
        assert n in names
    # Observation
    for n in ("list_tables", "describe_table", "describe_column",
              "sample_rows", "run_sql", "find_examples", "get_metric"):
        assert n in names
    # Harness
    for n in ("search_fabric", "search_schema", "profile", "sql_diff",
              "data_check", "join_path"):
        assert n in names
    # Authoring
    for n in ("propose_table_description", "propose_column_description",
              "propose_join", "propose_metric", "verify_join", "save_semantic_model",
              "refresh_database_md", "refresh_catalog_md"):
        assert n in names
    # Gates / dialect / compounding
    for n in ("completeness_check", "fabric_health", "metric_bind_check", "get_dialect",
              "clarify", "save_experience", "detect_boundary_convention", "upsert_ontology"):
        assert n in names


def test_completeness_and_dialect_tools(tmp_path):
    rt = _make_runtime(tmp_path)
    # seed a stub database.md so completeness flags stub
    rt.cs.put_text(rt.catalog, "db_a", "database.md", "# db_a\n", content_type="text/markdown")
    tools = {t.__name__: t for t in catalog_tools(rt)}
    rt._current_db = "db_a"  # avoid engine attach in unit tests
    dial = json.loads(tools["get_dialect"]("db_a"))
    assert dial["dialect"] == "duckdb"
    assert "cheat_sheet" in dial
    report = json.loads(tools["completeness_check"]("db_a", False))
    assert report["ok"] is False
    assert report["indexes_stub"] is True
    clarify = json.loads(tools["clarify"]("gross or net?", "gross,net"))
    assert clarify["needs_elicitation"] is True
    assert "gross" in clarify["options"]


def test_save_experience_and_ontology_tools(tmp_path):
    rt = _make_runtime(tmp_path)
    tools = {t.__name__: t for t in catalog_tools(rt)}
    rt._current_db = "db_a"
    out = json.loads(tools["save_experience"]("null client_ref drops ~4% of store sales",
                                              "data_check", "GOTCHAS", "db_a"))
    assert out["ok"] is True
    md = rt.cs.get_text(rt.catalog, "db_a", "experiences.md")
    assert "null client_ref" in md
    up = json.loads(tools["upsert_ontology"]("db_a", "", ""))
    assert up["ok"] is True
    assert "channel_maps.yaml" in up["written"] or "ontology.yaml" in up["written"]


def test_catalog_resource_body(tmp_path):
    from diracdata.mcps.register import catalog_resource_body
    rt = _make_runtime(tmp_path)
    rt.cs.put_text(rt.catalog, "db_a", "database.md",
                   "# db_a\n\n" + ("Retail narrative paragraph. " * 20),
                   content_type="text/markdown")
    body = catalog_resource_body(rt, f"index://{rt.catalog}/db_a")
    assert "db_a" in body
    dial = json.loads(catalog_resource_body(rt, f"dialect://{rt.catalog}/db_a"))
    assert dial["dialect"] == "duckdb"


def test_list_databases_reads_stubs(tmp_path):
    rt = _make_runtime(tmp_path)
    tools = {t.__name__: t for t in catalog_tools(rt)}
    r = json.loads(tools["list_databases"]())
    assert r["catalog"] == "test_cat"
    assert r["count"] == 2
    got = {d["database"]: d["description"] for d in r["databases"]}
    assert got == {"db_a": "desc db_a", "db_b": "desc db_b"}


def test_use_database_rejects_unknown(tmp_path):
    rt = _make_runtime(tmp_path)
    tools = {t.__name__: t for t in catalog_tools(rt)}
    with pytest.raises(ValueError):
        # Not calling the tool wrapper — the underlying runtime raises directly
        rt.use_database("db_missing")


def test_get_catalog_index_falls_back_when_missing(tmp_path):
    rt = _make_runtime(tmp_path)
    tools = {t.__name__: t for t in catalog_tools(rt)}
    out = tools["get_catalog_index"]()
    assert "no catalog.md" in out
    # Now write one and try again
    rt.store.write_text("fabric/catalogs/test_cat/catalog.md", "# hello", "text/markdown")
    assert tools["get_catalog_index"]() == "# hello"


def test_get_database_index_needs_current_db(tmp_path):
    rt = _make_runtime(tmp_path)
    tools = {t.__name__: t for t in catalog_tools(rt)}
    with pytest.raises(RuntimeError):
        tools["get_database_index"]()  # no db in session


def test_authoring_proposals_flush_to_fabric(tmp_path):
    sqlite_path = tmp_path / "db_a.sqlite"
    con = sqlite3.connect(str(sqlite_path))
    con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE orders (order_ref INTEGER, customer_id INTEGER, amount DOUBLE)")
    con.executemany("INSERT INTO customers VALUES (?)", [(1,), (2,)])
    con.executemany("INSERT INTO orders VALUES (?, ?, ?)",
                    [(1, 1, 10.0), (2, 1, 20.0), (3, 2, 5.0)])
    con.commit(); con.close()

    rt = _make_runtime(tmp_path)
    rt.store.write_json("fabric/catalogs/test_cat/catalog.json",
                         {"name": "test_cat", "engine": "duckdb+sqlite"})
    rt._download_sqlite = lambda db: sqlite_path
    tools = {t.__name__: t for t in catalog_tools(rt)}
    tools["use_database"]("db_a")
    tools["propose_table_description"](table="orders", description="one row per order",
                                        db="db_a")
    tools["propose_column_description"](table="orders", column="amount",
                                         description="order total in USD", db="db_a")
    pj = json.loads(tools["propose_join"](
        left_table="orders", left_col="customer_id",
        right_table="customers", right_col="id", db="db_a",
    ))
    assert pj["ok"] is True
    assert pj["measurement"]["verdict"] == "accept"
    assert pj["edge"].get("match_rate") == 1.0
    tools["propose_metric"](name="total_orders", sql="SELECT COUNT(*) FROM orders",
                             description="total orders", db="db_a")
    r = json.loads(tools["save_semantic_model"](db="db_a"))
    assert r["ok"] is True
    assert r["tables_updated"] == 1 and r["columns_updated"] == 1
    assert r["joins_added"] == 1 and r["metrics_added"] == 1
    # Artifacts written to the fabric under new layout
    md = json.loads(rt.store.blobs[
        "fabric/catalogs/test_cat/databases/db_a/metadata_descriptions.json"].decode())
    assert md["tables"]["orders"]["description"] == "one row per order"
    assert md["tables"]["orders"]["columns"]["amount"]["description"] == "order total in USD"
    joins = json.loads(rt.store.blobs[
        "fabric/catalogs/test_cat/databases/db_a/join_facts.json"].decode())
    assert joins[0]["cardinality"] in ("N-1", "1-1", "many_to_one")
    assert joins[0].get("verified_by")
    sl = rt.store.blobs[
        "fabric/catalogs/test_cat/databases/db_a/semantic_layer.yaml"].decode()
    assert "total_orders" in sl
    # Proposals cleared after save
    assert "db_a" not in rt._proposals


def test_run_sql_over_sqlite_engine(tmp_path):
    """Prove _SqliteBackedDuckDB works end-to-end via the tool surface, with a real
    SQLite file (no MinIO). We monkeypatch _download_sqlite to point at our temp file."""
    # Build a tiny SQLite database with one table + rows
    sqlite_path = tmp_path / "db_a.sqlite"
    con = sqlite3.connect(str(sqlite_path))
    con.execute("CREATE TABLE widgets (id INTEGER, color TEXT)")
    con.executemany("INSERT INTO widgets VALUES (?, ?)",
                    [(1, "red"), (2, "blue"), (3, "red")])
    con.commit(); con.close()

    rt = _make_runtime(tmp_path)
    # Make the catalog SQLite-typed so _engine_for uses _SqliteBackedDuckDB
    rt.store.write_json(f"fabric/catalogs/test_cat/catalog.json",
                         {"name": "test_cat", "engine": "duckdb+sqlite"})
    # Short-circuit the S3 download to return our local file
    rt._download_sqlite = lambda db: sqlite_path

    tools = {t.__name__: t for t in catalog_tools(rt)}
    tools["use_database"](db="db_a")

    # list_tables
    tables_out = json.loads(tools["list_tables"]())
    assert "widgets" in tables_out["tables"]

    # run_sql
    r = json.loads(tools["run_sql"](sql="SELECT color, COUNT(*) AS n FROM widgets GROUP BY 1 ORDER BY 2 DESC"))
    assert r["columns"] == ["color", "n"]
    assert r["rows"][0] == ["red", 2]

    # harness: search_schema / profile / sql_diff
    sch = json.loads(tools["search_schema"](pattern="col*"))
    assert any(h.get("column") == "color" for h in sch["hits"])
    prof = json.loads(tools["profile"](target="widgets.color"))
    assert prof["ok"] is True and prof.get("distinct") == 2
    diff = json.loads(tools["sql_diff"](
        sql_a="SELECT COUNT(*) AS n FROM widgets",
        sql_b="SELECT COUNT(*) AS n FROM widgets WHERE color = 'red'",
    ))
    assert diff["a"]["ok"] and diff["b"]["ok"]
    assert diff["delta"]["row_count_delta"] == 0  # both 1-row aggregates
    assert diff["delta"]["numeric_sum_deltas"]["n"]["delta"] == -1  # 3 vs 2? wait COUNT returns 1 row each
    # Actually both return one row; n values 3 vs 2
    assert diff["a"]["rows"][0][0] == 3 and diff["b"]["rows"][0][0] == 2


def test_search_fabric_greps_seeded_artifacts(tmp_path):
    rt = _make_runtime(tmp_path)
    rt.store.write_text(
        "fabric/catalogs/test_cat/databases/db_a/database.md",
        "# db_a\n\ncustomer spend band thresholds for wholesale orders.\n",
        "text/markdown",
    )
    rt.store.write_json(
        "fabric/catalogs/test_cat/databases/db_a/metadata_descriptions.json",
        {"tables": {"customergroupthreshold": {"description": "spend group bands"}}},
    )
    tools = {t.__name__: t for t in catalog_tools(rt)}
    out = json.loads(tools["search_fabric"](query="spend band threshold", db="db_a"))
    assert out["ok"] is True
    arts = {h["artifact"] for h in out["hits"]}
    assert "database.md" in arts or "metadata_descriptions.json" in arts


def test_search_fabric_hits_experiences_ontology_and_channel_maps(tmp_path):
    rt = _make_runtime(tmp_path)
    rt.store.write_text(
        "fabric/catalogs/test_cat/databases/db_a/experiences.md",
        "# GOTCHAS\n\npayment_status is uppercase SUCCESS; UNNEST(fraud_signals.rules).\n",
        "text/markdown",
    )
    rt.store.write_text(
        "fabric/catalogs/test_cat/databases/db_a/ontology.yaml",
        "entities:\n  merchant:\n    synonyms: [seller, vendor]\n",
        "text/yaml",
    )
    rt.store.write_text(
        "fabric/catalogs/test_cat/databases/db_a/channel_maps.yaml",
        "utm_source_to_campaign_channel:\n  google: search\n  meta: social\n",
        "text/yaml",
    )
    rt.store.write_text(
        "fabric/catalogs/test_cat/databases/db_a/semantic_model.yaml",
        "models:\n  orders:\n    grain: one row per order\n",
        "text/yaml",
    )
    tools = {t.__name__: t for t in catalog_tools(rt)}
    gotchas = json.loads(tools["search_fabric"](query="SUCCESS UNNEST fraud", db="db_a"))
    arts = {h["artifact"] for h in gotchas["hits"]}
    assert "experiences.md" in arts
    channel = json.loads(tools["search_fabric"](query="utm channel mapping google", db="db_a"))
    assert "channel_maps.yaml" in {h["artifact"] for h in channel["hits"]}
    ont = json.loads(tools["search_fabric"](query="merchant vendor synonym", db="db_a"))
    assert "ontology.yaml" in {h["artifact"] for h in ont["hits"]}
    grain = json.loads(tools["search_fabric"](query="one row per order grain", db="db_a"))
    assert "semantic_model.yaml" in {h["artifact"] for h in grain["hits"]}


def test_catalog_instructions_omit_schema_only_tools(tmp_path):
    from diracdata.mcps.instructions import SCHEMA_ONLY_QUERY_TOOLS, catalog_instructions
    cat = catalog_instructions()
    for name in SCHEMA_ONLY_QUERY_TOOLS:
        assert name not in cat
    names = {t.__name__ for t in catalog_tools(_make_runtime(tmp_path))}
    for name in SCHEMA_ONLY_QUERY_TOOLS:
        assert name not in names


def test_describe_table_soft_fails_partial(tmp_path):
    """When sample SELECT fails, describe_table still returns columns/count if possible."""
    sqlite_path = tmp_path / "db_a.sqlite"
    con = sqlite3.connect(str(sqlite_path))
    con.execute("CREATE TABLE widgets (id INTEGER, color TEXT)")
    con.executemany("INSERT INTO widgets VALUES (?, ?)", [(1, "red"), (2, "blue")])
    con.commit(); con.close()

    rt = _make_runtime(tmp_path)
    rt.store.write_json("fabric/catalogs/test_cat/catalog.json",
                         {"name": "test_cat", "engine": "duckdb+sqlite"})
    rt._download_sqlite = lambda db: sqlite_path
    tools = {t.__name__: t for t in catalog_tools(rt)}
    tools["use_database"](db="db_a")
    out = json.loads(tools["describe_table"](table="widgets"))
    assert "columns" in out and out.get("row_count") == 2
    assert isinstance(out.get("warnings"), list)


def test_describe_tools_against_duckdb_engine(tmp_path):
    """Regression: DuckDBEngine.query requires max_rows and wraps SQL as a subquery.
    describe_table / describe_column / search_schema / sample_rows / profile must work."""
    import duckdb
    from diracdata.engines.duckdb import DuckDBEngine

    pq = tmp_path / "db_a" / "parquet"
    pq.mkdir(parents=True)
    db = duckdb.connect(":memory:")
    db.execute(
        "CREATE TABLE orders (order_ref INTEGER, "
        "fraud_signals STRUCT(score DOUBLE, rules VARCHAR[]))"
    )
    db.execute("INSERT INTO orders VALUES (1, {'score': 0.5, 'rules': ['geo']})")
    dest = (pq / "orders.parquet").as_posix().replace("'", "''")
    db.execute(f"COPY orders TO '{dest}' (FORMAT PARQUET)")
    db.close()

    rt = _make_runtime(tmp_path)
    rt._current_db = "db_a"
    rt._engines["db_a"] = DuckDBEngine(data_root=tmp_path, schema_name="db_a")
    tools = {t.__name__: t for t in catalog_tools(rt)}

    def _no_max_rows_err(blob) -> None:
        text = json.dumps(blob, default=str)
        assert "max_rows" not in text
        assert not str(blob).startswith("error describing")
        assert not str(blob).startswith("error sampling")

    dt = json.loads(tools["describe_table"]("orders"))
    _no_max_rows_err(dt)
    col_names = {c.get("column_name") for c in dt["columns"]}
    assert "fraud_signals" in col_names
    assert dt.get("row_count") == 1
    assert dt.get("sample_rows")

    dc = json.loads(tools["describe_column"]("orders", "fraud_signals"))
    _no_max_rows_err(dc)
    assert "STRUCT" in str(dc.get("type") or "")
    assert dc.get("row_count") == 1
    # Phase 4: derived access recipe + verified runnable example even without fabric
    assert dc.get("access_recipe")
    assert "UNNEST" in str(dc.get("access_recipe")).upper() or "rules" in str(dc.get("access_recipe"))
    assert dc.get("runnable_example") and "SELECT" in dc["runnable_example"]
    assert dc.get("runnable_example_source") == "verified"

    # Fabric fields merge onto live payload (not fabric-only early return)
    rt.store.write_json(
        "fabric/catalogs/test_cat/databases/db_a/metadata_descriptions.json",
        {"tables": {"orders": {"columns": {
            "fraud_signals": {
                "description": "fraud model payload",
                "access_recipe": "UNNEST(fraud_signals.rules)",
                "runnable_example": "SELECT UNNEST(fraud_signals.rules) FROM orders LIMIT 5",
            }
        }}}},
    )
    dc2 = json.loads(tools["describe_column"]("orders", "fraud_signals"))
    assert dc2.get("description") == "fraud model payload"
    assert dc2.get("access_recipe") == "UNNEST(fraud_signals.rules)"
    assert dc2.get("access_recipe_source") == "fabric"
    assert "STRUCT" in str(dc2.get("type") or "")
    assert dc2.get("row_count") == 1

    sch = json.loads(tools["search_schema"]("fraud*"))
    _no_max_rows_err(sch)
    assert sch.get("ok") is True
    assert not sch.get("warnings")
    assert any(h.get("column") == "fraud_signals" for h in sch["hits"])

    samp = json.loads(tools["sample_rows"]("orders", "", 2))
    _no_max_rows_err(samp)
    assert samp.get("rows")

    prof = json.loads(tools["profile"]("orders"))
    _no_max_rows_err(prof)
    assert prof.get("ok") is True
    assert prof.get("row_count") == 1
    assert any(c.get("name") == "fraud_signals" for c in (prof.get("columns") or []))
