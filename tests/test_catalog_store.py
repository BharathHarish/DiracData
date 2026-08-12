"""Unit tests for CatalogStore — new-layout writes, legacy-fallback reads,
Catalog assembly from stored artifacts.

Uses an in-memory Store so no MinIO / disk needed. Fully hermetic.
"""
from __future__ import annotations

from typing import Any, Dict, List
import json

from diracdata.context.catalog import LEGACY_CATALOG, DEFAULT_SCHEMA
from diracdata.context.catalog_store import (
    CatalogStore, _catalog_key, _database_key, _legacy_fabric_key,
    _extract_table_names_from_semantic_model,
)


# ---------- an in-memory Store implementing the diracdata.stores.Store protocol ----------

class MemStore:
    def __init__(self) -> None:
        self.blobs: Dict[str, bytes] = {}
        self.types: Dict[str, str] = {}

    def exists(self, key: str) -> bool:                          return key in self.blobs
    def read_json(self, key: str) -> Any:                        return json.loads(self.blobs[key].decode())
    def read_text(self, key: str) -> str:                        return self.blobs[key].decode()
    def write_json(self, key: str, obj: Any) -> None:            self.write_text(key, json.dumps(obj), "application/json")
    def write_text(self, key: str, text: str, content_type: str = "text/plain") -> None:
        self.blobs[key] = text.encode(); self.types[key] = content_type
    def list_keys(self, prefix: str) -> List[str]:               return sorted(k for k in self.blobs if k.startswith(prefix))
    def delete(self, key: str) -> None:                          self.blobs.pop(key, None)


# ---------- path helpers ----------

def test_catalog_key_shape():
    assert _catalog_key("spider2_local", "catalog.yaml") == "fabric/catalogs/spider2_local/catalog.yaml"

def test_database_key_shape():
    assert _database_key("spider2_local", "chinook", "semantic_model.yaml") == \
        "fabric/catalogs/spider2_local/databases/chinook/semantic_model.yaml"

def test_legacy_key_shape():
    assert _legacy_fabric_key("retail_complex", "semantic_model.yaml") == \
        "fabric/retail_complex/semantic_model.yaml"


# ---------- writes go to NEW layout only ----------

def test_put_writes_new_layout():
    st = MemStore(); cs = CatalogStore(st)
    cs.put("local", "retail_complex", "gold_pairs.json", [{"nl": "x", "sql": "SELECT 1"}])
    assert "fabric/catalogs/local/databases/retail_complex/gold_pairs.json" in st.blobs
    # Legacy path NOT written
    assert "fabric/retail_complex/gold_pairs.json" not in st.blobs

def test_put_catalog_writes_at_catalog_root():
    st = MemStore(); cs = CatalogStore(st)
    cs.put_catalog("spider2_local", "catalog.yaml", {"engine": "duckdb", "databases": ["chinook", "f1"]})
    assert "fabric/catalogs/spider2_local/catalog.yaml" in st.blobs


# ---------- reads prefer NEW layout, fall back to legacy for 'local' ----------

def test_get_reads_new_layout_when_present():
    st = MemStore(); cs = CatalogStore(st)
    cs.put("local", "retail_complex", "coverage_report.json", {"metrics": 5})
    # Also add a stale legacy blob at the same logical name — new-layout should win
    st.write_json("fabric/retail_complex/coverage_report.json", {"metrics": 99})
    assert cs.get("local", "retail_complex", "coverage_report.json") == {"metrics": 5}

def test_get_falls_back_to_legacy_when_local_and_new_missing():
    st = MemStore(); cs = CatalogStore(st)
    st.write_json("fabric/retail_complex/coverage_report.json", {"metrics": 42})
    # nothing at new-layout path → falls back
    assert cs.get("local", "retail_complex", "coverage_report.json") == {"metrics": 42}

def test_get_does_not_fall_back_to_legacy_for_non_local_catalogs():
    st = MemStore(); cs = CatalogStore(st)
    st.write_json("fabric/retail_complex/coverage_report.json", {"metrics": 42})
    assert cs.get("spider2_local", "retail_complex", "coverage_report.json") is None

def test_has_reflects_either_layout_for_local():
    st = MemStore(); cs = CatalogStore(st)
    st.write_text("fabric/retail_complex/semantic_model.yaml", "models: {}", "text/yaml")
    assert cs.has("local", "retail_complex", "semantic_model.yaml") is True
    assert cs.has("spider2_local", "retail_complex", "semantic_model.yaml") is False

def test_get_text_reads_yaml_from_new_layout():
    st = MemStore(); cs = CatalogStore(st)
    cs.put_text("spider2_local", "chinook", "semantic_model.yaml",
                "version: 3\nmodels:\n  tracks:\n    short: song table\n", content_type="text/yaml")
    assert "tracks" in cs.get_text("spider2_local", "chinook", "semantic_model.yaml")


# ---------- discovery: list_catalogs / list_databases ----------

def test_list_catalogs_includes_new_layout_catalogs():
    st = MemStore(); cs = CatalogStore(st)
    cs.put_catalog("spider2_local", "catalog.yaml", {})
    cs.put("local", "retail_complex", "semantic_model.yaml", {"models": {}})
    assert set(cs.list_catalogs()) == {"spider2_local", "local"}

def test_list_catalogs_surfaces_local_when_only_legacy_fabric_exists():
    st = MemStore(); cs = CatalogStore(st)
    st.write_json("fabric/retail_complex/semantic_model.yaml", {"models": {"clients": {}}})
    assert "local" in cs.list_catalogs()

def test_list_databases_for_new_layout_catalog():
    st = MemStore(); cs = CatalogStore(st)
    cs.put("spider2_local", "chinook", "semantic_model.yaml", {"models": {}})
    cs.put("spider2_local", "northwind", "semantic_model.yaml", {"models": {}})
    assert cs.list_databases("spider2_local") == ["chinook", "northwind"]

def test_list_databases_for_local_falls_back_to_legacy_layout():
    st = MemStore(); cs = CatalogStore(st)
    st.write_json("fabric/retail_complex/semantic_model.yaml", {"models": {}})
    st.write_json("fabric/fintech_complex/semantic_model.yaml", {"models": {}})
    assert cs.list_databases("local") == ["fintech_complex", "retail_complex"]

def test_list_databases_for_local_merges_new_and_legacy():
    st = MemStore(); cs = CatalogStore(st)
    st.write_json("fabric/retail_complex/semantic_model.yaml", {"models": {}})   # legacy
    cs.put("local", "fintech_complex", "semantic_model.yaml", {"models": {}})    # new
    assert set(cs.list_databases("local")) == {"retail_complex", "fintech_complex"}


# ---------- semantic_model table-name extraction ----------

def test_extract_table_names_from_semantic_model_basic():
    sm = """version: 3
schema: retail_complex
models:
  clients:
    short: shopper record
  online_purchases:
    short: purchases from web
  merchandise:
    short: SKU catalog
"""
    assert _extract_table_names_from_semantic_model(sm) == ["clients", "online_purchases", "merchandise"]

def test_extract_table_names_ignores_deeper_indents():
    sm = """models:
  clients:
    short: shopper
    columns:
      id:
        type: bigint
  online_purchases:
    short: purchases
"""
    assert _extract_table_names_from_semantic_model(sm) == ["clients", "online_purchases"]

def test_extract_table_names_stops_at_next_top_level_key():
    sm = """models:
  clients:
    short: x
joins:
  - left: clients
"""
    assert _extract_table_names_from_semantic_model(sm) == ["clients"]


# ---------- load_catalog: full end-to-end assembly ----------

def test_load_catalog_new_layout_returns_populated_catalog():
    st = MemStore(); cs = CatalogStore(st)
    cs.put_catalog("spider2_local", "catalog.yaml",
                    {"engine": "duckdb", "description": "Spider 2.0-Lite: 30 SQLite DBs"})
    cs.put_catalog_text("spider2_local", "catalog.md", "# Catalog: spider2_local\n\n30 dbs...")
    cs.put_text("spider2_local", "chinook", "semantic_model.yaml",
                 "models:\n  albums:\n    short: x\n  artists:\n    short: y\n", content_type="text/yaml")
    cs.put_text("spider2_local", "chinook", "database.md", "# chinook\n\ndigital music store")
    cs.put_text("spider2_local", "northwind", "semantic_model.yaml",
                 "models:\n  categories:\n    short: x\n", content_type="text/yaml")

    cat = cs.load_catalog("spider2_local")
    assert cat.name == "spider2_local"
    assert cat.engine == "duckdb"
    assert cat.description == "Spider 2.0-Lite: 30 SQLite DBs"
    assert "spider2_local" in cat.catalog_md
    assert set(cat.database_names()) == {"chinook", "northwind"}
    ch = cat.db("chinook")
    assert ch.catalog == "spider2_local"
    assert set(ch.default_schema().table_names) == {"albums", "artists"}
    assert "digital music store" in ch.database_md

def test_load_catalog_legacy_falls_back_and_returns_local_catalog():
    st = MemStore(); cs = CatalogStore(st)
    # Only legacy layout present — no catalog.yaml under fabric/catalogs/local/
    st.write_json("fabric/retail_complex/semantic_model.yaml",
                   {"version": 3, "models": {"clients": {}}})
    st.write_text("fabric/retail_complex/semantic_model.yaml",
                   "models:\n  clients:\n    short: x\n  online_purchases:\n    short: y\n",
                   "text/yaml")
    cat = cs.load_catalog("local")
    assert cat.name == "local"
    assert cat.database_names() == ["retail_complex"]
    rc = cat.db("retail_complex")
    assert rc.catalog == "local"
    assert set(rc.default_schema().table_names) == {"clients", "online_purchases"}

def test_load_catalog_unknown_returns_empty_catalog():
    st = MemStore(); cs = CatalogStore(st)
    cat = cs.load_catalog("nonexistent")
    assert cat.name == "nonexistent"
    assert cat.database_names() == []
