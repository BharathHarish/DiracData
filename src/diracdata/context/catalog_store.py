"""CatalogStore — storage IO for catalog-level fabric artifacts.

New layout (§3 of CATALOG_DESIGN.md):
    fabric/catalogs/<catalog>/
      catalog.yaml
      catalog.md
      cross_db_joins.yaml
      cross_db_metrics.yaml
      cross_db_examples.jsonl
      databases/<db>/
        database.yaml
        database.md
        semantic_model.yaml
        metadata_descriptions.json
        join_facts.json
        semantic_layer.yaml
        gold_pairs.json
        value_domains.json
        coverage_report.json

Legacy layout (kept indefinitely per Decision #3):
    fabric/<schema>/<name>            — treated as catalog="local", database=<schema>
    state/<schema>/<name>             — mirror path under state/

CatalogStore reads NEW layout first; on miss AND when catalog=='local', falls
back to LEGACY layout automatically. Writes always land in NEW layout.
Nothing in ContextStore changes — the two live side-by-side.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from diracdata.context.catalog import (
    LEGACY_CATALOG, Catalog, Database, Schema, DEFAULT_SCHEMA, wrap_legacy,
)
from diracdata.stores import Store


# ----- path helpers --------------------------------------------------------- #

def _catalog_root(catalog: str) -> str:
    return f"fabric/catalogs/{catalog}"


def _catalog_key(catalog: str, name: str) -> str:
    """Catalog-level artifact key. Example: catalog.yaml, catalog.md."""
    return f"{_catalog_root(catalog)}/{name}"


def _database_root(catalog: str, db: str) -> str:
    return f"{_catalog_root(catalog)}/databases/{db}"


def _database_key(catalog: str, db: str, name: str) -> str:
    """Per-DB fabric artifact key."""
    return f"{_database_root(catalog, db)}/{name}"


def _legacy_fabric_key(db: str, name: str) -> str:
    """Legacy single-schema key (mirrors ContextStore._fabric_key)."""
    return f"fabric/{db}/{name}"


# ----- CatalogStore --------------------------------------------------------- #

class CatalogStore:
    """Read/write catalog-level fabric artifacts, with legacy fallback.

    Read semantics:
      - get(catalog, db, name)      first tries fabric/catalogs/<catalog>/databases/<db>/<name>
                                     then falls back to fabric/<db>/<name> IF catalog=='local'
      - get_catalog(catalog, name)  reads fabric/catalogs/<catalog>/<name> (no legacy fallback —
                                     catalog-level artifacts didn't exist in the old layout)

    Write semantics:
      - all writes land in the NEW layout. We never write back to legacy paths.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    # ---- database-level (per-DB fabric artifacts) --------------------------

    def put(self, catalog: str, db: str, name: str, obj: Any) -> str:
        key = _database_key(catalog, db, name)
        self._store.write_json(key, obj)
        return key

    def put_text(self, catalog: str, db: str, name: str, text: str,
                 content_type: str = "text/plain") -> str:
        key = _database_key(catalog, db, name)
        self._store.write_text(key, text, content_type=content_type)
        return key

    def get(self, catalog: str, db: str, name: str, default: Any = None) -> Any:
        """Read a per-DB JSON artifact. Falls back to legacy path for catalog='local'."""
        key = _database_key(catalog, db, name)
        if self._store.exists(key):
            return self._store.read_json(key)
        if catalog == LEGACY_CATALOG:
            legacy = _legacy_fabric_key(db, name)
            if self._store.exists(legacy):
                return self._store.read_json(legacy)
        return default

    def get_text(self, catalog: str, db: str, name: str, default: Any = None) -> Any:
        """Read a per-DB text artifact (YAML, Markdown, JSONL). Legacy fallback for 'local'."""
        key = _database_key(catalog, db, name)
        if self._store.exists(key):
            return self._store.read_text(key)
        if catalog == LEGACY_CATALOG:
            legacy = _legacy_fabric_key(db, name)
            if self._store.exists(legacy):
                return self._store.read_text(legacy)
        return default

    def has(self, catalog: str, db: str, name: str) -> bool:
        if self._store.exists(_database_key(catalog, db, name)):
            return True
        if catalog == LEGACY_CATALOG:
            return self._store.exists(_legacy_fabric_key(db, name))
        return False

    def list_database_artifacts(self, catalog: str, db: str) -> List[str]:
        """List artifact filenames present in a database's fabric folder (new layout only)."""
        prefix = _database_root(catalog, db) + "/"
        keys = self._store.list_keys(prefix)
        return sorted([k[len(prefix):] for k in keys if k.startswith(prefix)])

    # ---- catalog-level artifacts -------------------------------------------

    def put_catalog(self, catalog: str, name: str, obj: Any) -> str:
        key = _catalog_key(catalog, name)
        self._store.write_json(key, obj)
        return key

    def put_catalog_text(self, catalog: str, name: str, text: str,
                          content_type: str = "text/plain") -> str:
        key = _catalog_key(catalog, name)
        self._store.write_text(key, text, content_type=content_type)
        return key

    def get_catalog(self, catalog: str, name: str, default: Any = None) -> Any:
        key = _catalog_key(catalog, name)
        return self._store.read_json(key) if self._store.exists(key) else default

    def get_catalog_text(self, catalog: str, name: str, default: Any = None) -> Any:
        key = _catalog_key(catalog, name)
        return self._store.read_text(key) if self._store.exists(key) else default

    # ---- discovery ---------------------------------------------------------

    def list_catalogs(self) -> List[str]:
        """All catalogs present under the new layout, plus 'local' if any legacy fabric exists."""
        prefix = "fabric/catalogs/"
        seen = set()
        for k in self._store.list_keys(prefix):
            rest = k[len(prefix):]
            if "/" in rest:
                seen.add(rest.split("/", 1)[0])
        # If any legacy fabric/<schema>/... exists (and it isn't the catalogs/ tree itself),
        # surface 'local' as a virtual catalog.
        for k in self._store.list_keys("fabric/"):
            if k.startswith("fabric/catalogs/"): continue
            rest = k[len("fabric/"):]
            if "/" in rest:
                seen.add(LEGACY_CATALOG)
                break
        return sorted(seen)

    def list_databases(self, catalog: str) -> List[str]:
        """All databases in a catalog. New layout + legacy fallback for 'local'."""
        seen = set()
        # New layout
        prefix = f"{_catalog_root(catalog)}/databases/"
        for k in self._store.list_keys(prefix):
            rest = k[len(prefix):]
            if "/" in rest:
                seen.add(rest.split("/", 1)[0])
        # Legacy fallback: enumerate fabric/<schema>/ that has a semantic_model.yaml
        if catalog == LEGACY_CATALOG:
            for k in self._store.list_keys("fabric/"):
                if k.startswith("fabric/catalogs/"): continue
                rest = k[len("fabric/"):]
                if "/" in rest:
                    schema = rest.split("/", 1)[0]
                    # A directory counts as a database if it holds a semantic_model
                    if self._store.exists(_legacy_fabric_key(schema, "semantic_model.yaml")):
                        seen.add(schema)
        return sorted(seen)

    # ---- high-level: build a Catalog object from stored artifacts ----------

    def load_catalog(self, catalog: str) -> Catalog:
        """Build a Catalog object with all its databases populated (just table_names +
        top-level metadata, NOT full Table objects — those are loaded on demand).

        Prefers new layout; falls back to legacy shim for catalog='local' when
        the new-layout catalog.yaml is absent.
        """
        cat_meta = self.get_catalog(catalog, "catalog.json") or self.get_catalog(catalog, "catalog.yaml")
        cat_md = self.get_catalog_text(catalog, "catalog.md")

        if cat_meta is None and catalog != LEGACY_CATALOG:
            # No such catalog and it's not 'local' → return empty catalog with no DBs
            return Catalog(name=catalog, engine="duckdb", databases={}, catalog_md=cat_md)

        db_names = self.list_databases(catalog)
        cat = Catalog(
            name=catalog,
            engine=(cat_meta or {}).get("engine", "duckdb"),
            connection=(cat_meta or {}).get("connection", {}),
            description=(cat_meta or {}).get("description", ""),
            databases={},
            catalog_md=cat_md,
        )
        for db_name in db_names:
            cat.databases[db_name] = self._load_database(catalog, db_name)
        return cat

    def _load_database(self, catalog: str, db: str) -> Database:
        """Load one Database's metadata + table names (not full table shape)."""
        # Try new-layout database.json → get table names cheap (JSON is the canonical form
        # because CatalogStore.get() reads via read_json; .yaml kept for back-compat only)
        db_meta = self.get(catalog, db, "database.json") or self.get(catalog, db, "database.yaml") or {}
        db_md = self.get_text(catalog, db, "database.md")
        table_names = db_meta.get("table_names") or []
        if not table_names:
            # Fallback: read semantic_model.yaml (works in both new + legacy layouts)
            sm_text = self.get_text(catalog, db, "semantic_model.yaml") or ""
            table_names = _extract_table_names_from_semantic_model(sm_text)
        d = wrap_legacy(db, table_names=table_names).db(db)
        # Correct the catalog name (wrap_legacy always uses 'local')
        d = Database(
            name=db, catalog=catalog,
            schemas={DEFAULT_SCHEMA: Schema(
                name=DEFAULT_SCHEMA, database=db, catalog=catalog, table_names=table_names,
            )},
            description=db_meta.get("description", ""),
            database_md=db_md,
        )
        return d


# ----- helpers -------------------------------------------------------------- #

def _extract_table_names_from_semantic_model(sm_text: str) -> List[str]:
    """Parse table names from a semantic_model.yaml text without importing PyYAML.

    Semantic models follow shape:
        models:
          <table_name>:
            short: ...
            long: ...

    We just extract 2-space-indented keys under the `models:` block. Zero-dep, robust to
    minor YAML variants.
    """
    lines = sm_text.splitlines()
    out: List[str] = []
    in_models = False
    for ln in lines:
        stripped = ln.rstrip()
        if stripped.startswith("models:"):
            in_models = True; continue
        if in_models:
            # 2-space indented top-level model key (e.g. "  clients:")
            if len(ln) > 2 and ln[0] == " " and ln[1] == " " and ln[2] != " ":
                head = ln.strip()
                if head.endswith(":"):
                    out.append(head[:-1])
            # New top-level key ends the models block
            elif ln and not ln.startswith(" ") and not ln.startswith("#"):
                break
    return out


def catalog_store_from_settings(settings: Any) -> CatalogStore:
    """Build a CatalogStore over the configured object store — same pattern as ContextStore."""
    from diracdata.stores import store_from_settings
    return CatalogStore(store_from_settings(settings))


__all__ = ["CatalogStore", "catalog_store_from_settings",
           "_catalog_key", "_database_key", "_legacy_fabric_key"]
