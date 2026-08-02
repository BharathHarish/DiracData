"""FabricStore -- the one place artifacts live: schema-keyed storage over the object store
(MinIO/S3 in prod, a local object store in tests). Both agents use it; no agent touches the
filesystem for artifacts.

Two namespaces:
  fabric/<schema>/...  -- compiled context fabric the learning agent produces (immutable per run):
                          metadata_descriptions.json, value_domains.json, join_graph.json
  state/<schema>/...   -- runtime state the query agent writes as it answers:
                          experiences.jsonl, joins.jsonl, column_values.json
"""

from __future__ import annotations

import json
from typing import Any


class FabricStore:
    def __init__(self, store: Any) -> None:
        self._store = store  # any object store with write_json/read_json/read_text/write_text/exists/list_keys

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


def fabric_store_from_settings(settings: Any) -> FabricStore:
    """Build a FabricStore over the configured object store (MinIO/S3 or local)."""
    from diracdata.utils.object_store import object_store_from_settings
    return FabricStore(object_store_from_settings(settings))
