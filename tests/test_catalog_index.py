"""Unit tests for the catalog_index module (database.md + catalog.md authoring).

Uses an in-memory Store + a fake LLM so no MinIO / no Fireworks needed.
"""
from __future__ import annotations
import json
from typing import Any, Dict, List

from diracdata.context.catalog_store import CatalogStore
from diracdata.learning.catalog_index import (
    build_database_md, build_catalog_md, build_all_databases_md,
    prompt_database_md, prompt_catalog_md,
)


# ---------- reuse the MemStore fixture pattern ----------

class MemStore:
    def __init__(self) -> None:
        self.blobs: Dict[str, bytes] = {}
    def exists(self, key: str) -> bool: return key in self.blobs
    def read_json(self, key: str) -> Any: return json.loads(self.blobs[key].decode())
    def read_text(self, key: str) -> str: return self.blobs[key].decode()
    def write_json(self, key: str, obj: Any) -> None: self.write_text(key, json.dumps(obj), "application/json")
    def write_text(self, key: str, text: str, content_type: str = "text/plain") -> None: self.blobs[key] = text.encode()
    def list_keys(self, prefix: str) -> List[str]: return sorted(k for k in self.blobs if k.startswith(prefix))
    def delete(self, key: str) -> None: self.blobs.pop(key, None)


# ---------- prompt shape ----------

def test_prompt_database_md_includes_target_path_and_semantic_model():
    p = prompt_database_md(
        catalog="spider2_local", database="chinook",
        semantic_model_yaml="models:\n  tracks: {}\n",
        metadata_descriptions_json='{"tables": {}}',
    )
    assert "fabric/catalogs/spider2_local/databases/chinook/database.md" in p
    assert "tracks" in p
    assert "Return ONLY the markdown body" in p

def test_prompt_database_md_conditionally_includes_optional_hints():
    p1 = prompt_database_md(catalog="c", database="d",
                             semantic_model_yaml="", metadata_descriptions_json="{}")
    assert "Join facts" not in p1
    assert "Blessed metrics" not in p1
    assert "Gold NL-SQL seeds" not in p1

    p2 = prompt_database_md(catalog="c", database="d",
                             semantic_model_yaml="", metadata_descriptions_json="{}",
                             join_facts_json='[{"left":"a"}]',
                             semantic_layer_yaml="metrics: []",
                             gold_pairs_json='[{"nl":"q"}]')
    assert "Join facts" in p2
    assert "Blessed metrics" in p2
    assert "Gold NL-SQL seeds" in p2

def test_prompt_catalog_md_lists_databases():
    p = prompt_catalog_md(
        catalog="spider2_local", engine="duckdb",
        catalog_description="Spider 2.0-Lite",
        databases=[
            {"name": "chinook", "table_count": 13, "size_mb": 0.8, "database_md": "# chinook body"},
            {"name": "f1",      "table_count": 29, "size_mb": 71.5, "database_md": "# f1 body"},
        ],
    )
    assert "chinook" in p and "f1" in p
    assert "Spider 2.0-Lite" in p
    assert "13 tables" in p and "29 tables" in p


# ---------- build_database_md end-to-end (with fake LLM) ----------

def test_build_database_md_writes_to_new_layout_and_returns_content():
    st = MemStore(); cs = CatalogStore(st)
    cs.put_text("spider2_local", "chinook", "semantic_model.yaml",
                 "models:\n  tracks:\n    short: song\n", content_type="text/yaml")
    cs.put("spider2_local", "chinook", "metadata_descriptions.json",
            {"tables": {"tracks": {"description": "songs"}}})

    def fake_llm(prompt: str) -> str:
        assert "chinook" in prompt and "tracks" in prompt
        return "# Database: chinook (catalog: spider2_local)\n\nMusic store."

    md = build_database_md(cs, catalog="spider2_local", database="chinook", llm=fake_llm)
    assert "Music store" in md
    # written to new layout at the right key
    stored = cs.get_text("spider2_local", "chinook", "database.md")
    assert stored == md

def test_build_database_md_strips_accidental_code_fences_from_llm():
    st = MemStore(); cs = CatalogStore(st)
    cs.put_text("c", "d", "semantic_model.yaml", "models: {}", content_type="text/yaml")
    cs.put("c", "d", "metadata_descriptions.json", {})

    def fake_llm(prompt: str) -> str:
        return "```markdown\n# Database: d\n\nText\n```"

    md = build_database_md(cs, catalog="c", database="d", llm=fake_llm)
    assert md.startswith("# Database: d")
    assert "```" not in md

def test_build_database_md_handles_missing_optional_artifacts():
    st = MemStore(); cs = CatalogStore(st)
    cs.put_text("c", "d", "semantic_model.yaml", "models: {}", content_type="text/yaml")
    # metadata_descriptions.json, join_facts.json, semantic_layer.yaml, gold_pairs.json all absent
    seen = {"n": 0}
    def fake_llm(prompt: str) -> str:
        seen["n"] += 1
        # No Optional sections should appear
        assert "Join facts" not in prompt
        assert "Blessed metrics" not in prompt
        assert "Gold NL-SQL seeds" not in prompt
        return "# ok"

    md = build_database_md(cs, catalog="c", database="d", llm=fake_llm)
    assert seen["n"] == 1
    assert md == "# ok"


# ---------- build_catalog_md rollup ----------

def test_build_catalog_md_rolls_up_all_database_md_files():
    st = MemStore(); cs = CatalogStore(st)
    cs.put_catalog("spider2_local", "catalog.yaml", {"engine": "duckdb", "description": "Spider!"})
    # Two DBs with database.md already authored
    cs.put_text("spider2_local", "chinook",   "semantic_model.yaml", "models: {}", content_type="text/yaml")
    cs.put_text("spider2_local", "chinook",   "database.md", "# chinook body")
    cs.put_text("spider2_local", "northwind", "semantic_model.yaml", "models: {}", content_type="text/yaml")
    cs.put_text("spider2_local", "northwind", "database.md", "# northwind body")

    def fake_llm(prompt: str) -> str:
        assert "chinook body" in prompt and "northwind body" in prompt
        assert "Spider!" in prompt
        return "# Catalog: spider2_local\n\n2 dbs"

    md = build_catalog_md(cs, catalog="spider2_local", llm=fake_llm)
    assert "2 dbs" in md
    # written at catalog root
    stored = cs.get_catalog_text("spider2_local", "catalog.md")
    assert stored == md

def test_build_catalog_md_uses_database_yaml_hints_when_present():
    st = MemStore(); cs = CatalogStore(st)
    cs.put_catalog("c", "catalog.yaml", {})
    cs.put_text("c", "d", "semantic_model.yaml", "models: {}", content_type="text/yaml")
    cs.put("c", "d", "database.yaml", {"table_count": 13, "size_mb": 0.8})
    cs.put_text("c", "d", "database.md", "# d body")

    seen_prompt = {"p": None}
    def fake_llm(prompt: str) -> str:
        seen_prompt["p"] = prompt
        return "# c"

    build_catalog_md(cs, catalog="c", llm=fake_llm)
    p = seen_prompt["p"]
    assert "13 tables" in p and "0.8 MB" in p


# ---------- build_all_databases_md ----------

def test_build_all_databases_md_covers_every_database_in_catalog():
    st = MemStore(); cs = CatalogStore(st)
    for db in ("chinook", "northwind", "f1"):
        cs.put_text("spider2_local", db, "semantic_model.yaml", "models: {}", content_type="text/yaml")

    called_for = []
    def fake_llm(prompt: str) -> str:
        for db in ("chinook", "northwind", "f1"):
            if f"databases/{db}/database.md" in prompt:
                called_for.append(db)
                return f"# {db}"
        return "# ?"

    out = build_all_databases_md(cs, catalog="spider2_local", llm=fake_llm)
    assert set(out.keys()) == {"chinook", "northwind", "f1"}
    assert set(called_for) == {"chinook", "northwind", "f1"}

def test_build_all_databases_md_progress_callback():
    st = MemStore(); cs = CatalogStore(st)
    for db in ("a", "b"):
        cs.put_text("c", db, "semantic_model.yaml", "models: {}", content_type="text/yaml")
    ticks = []
    build_all_databases_md(
        cs, catalog="c", llm=lambda p: "# ok",
        on_progress=ticks.append,
    )
    assert len(ticks) == 2
    assert all("c/" in t for t in ticks)
