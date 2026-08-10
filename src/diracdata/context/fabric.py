"""ContextStore -- the ONE home for a schema's compiled semantic context, over an object store ONLY.

The learned context (metadata_descriptions.json, semantic_model.yaml, value_domains.json,
semantic_layer.yaml, join_graph.json, coverage_report.json) is compiled write-once-per-learn and
read-many, shared across users, and file-shaped -- so it lives in the object store and nowhere else.
This is DELIBERATELY not pluggable the way diracdata.checkpoints is (conversation state is mutable,
per-user, write-heavy, so it earns a DB backend; compiled context does not). Object store keeps the
context portable: a schema's context is just a folder of blobs you can copy, version, or ship.

Two namespaces:
  fabric/<schema>/...  -- the compiled context the learning agent produces (immutable per run).
  state/<schema>/...   -- small runtime state the query agent writes back as it answers.
"""

from __future__ import annotations

import json
from typing import Any

from diracdata.stores import Store


class ContextStore:
    def __init__(self, store: Store) -> None:
        self._store = store  # a diracdata.stores.Store (object store) -- NOT a checkpoint DB backend

    # ---- compiled fabric (learning agent output) --------------------------------------
    def put(self, schema: str, name: str, obj: Any) -> str:
        key = self._fabric_key(schema, name)
        self._store.write_json(key, obj)
        return key

    def get(self, schema: str, name: str, default: Any = None) -> Any:
        key = self._fabric_key(schema, name)
        return self._store.read_json(key) if self._store.exists(key) else default

    def has(self, schema: str, name: str) -> bool:
        return self._store.exists(self._fabric_key(schema, name))

    def read_text(self, schema: str, name: str, default: Any = None) -> Any:
        """Raw text of a fabric artifact (e.g. a YAML semantic layer authored by hand). `default` if
        absent."""
        key = self._fabric_key(schema, name)
        return self._store.read_text(key) if self._store.exists(key) else default

    def list(self, schema: str) -> list[str]:
        return self._store.list_keys(f"fabric/{schema}/")

    # ---- runtime state (query agent write-back) ---------------------------------------
    def append_record(self, schema: str, name: str, record: dict) -> None:
        """Append one JSONL record (object stores have no append -> read-modify-write; fine for
        the small per-schema state files)."""
        key = self._state_key(schema, name)
        text = self._store.read_text(key) if self._store.exists(key) else ""
        self._store.write_text(key, text + json.dumps(record, default=str) + "\n",
                               content_type="application/x-ndjson")

    def read_records(self, schema: str, name: str) -> list[dict]:
        key = self._state_key(schema, name)
        if not self._store.exists(key):
            return []
        return [json.loads(line) for line in self._store.read_text(key).splitlines() if line.strip()]

    def put_state(self, schema: str, name: str, obj: Any) -> None:
        self._store.write_json(self._state_key(schema, name), obj)

    def get_state(self, schema: str, name: str, default: Any = None) -> Any:
        key = self._state_key(schema, name)
        return self._store.read_json(key) if self._store.exists(key) else default

    @staticmethod
    def _fabric_key(schema: str, name: str) -> str:
        return f"fabric/{schema}/{name}"

    @staticmethod
    def _state_key(schema: str, name: str) -> str:
        return f"state/{schema}/{name}"


def context_store_from_settings(settings: Any) -> ContextStore:
    """Build a ContextStore over the configured object store (MinIO/S3 or local) -- object store only."""
    from diracdata.stores import store_from_settings
    return ContextStore(store_from_settings(settings))


# Back-compat aliases (the store used to be called "fabric"). Prefer ContextStore in new code.
FabricStore = ContextStore
fabric_store_from_settings = context_store_from_settings
