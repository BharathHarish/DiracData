"""Read-through cache over any Store.

Object reads are cached; a write to a key evicts it, so the cache stays consistent within a process
(all writes go through this wrapper). Listings and existence are not cached because they change as
artifacts are written. Best for immutable artifacts on a remote backend (S3/MinIO), where it removes
repeated network reads from the hot path.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from diracdata.config import Config


class CachingObjectStore:
    def __init__(self, inner: Any, *, max_entries: int = Config().cache_max_entries) -> None:
        self._inner = inner
        self._max_entries = max(1, max_entries)
        self._cache: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def write_bytes(self, key: str, value: bytes, content_type: str | None = None) -> None:
        self._inner.write_bytes(key, value, content_type=content_type)
        self._evict(key)

    def write_text(self, key: str, value: str, content_type: str = "text/plain") -> None:
        self._inner.write_text(key, value, content_type=content_type)
        self._evict(key)

    def write_json(self, key: str, value: object) -> None:
        self._inner.write_json(key, value)
        self._evict(key)

    def read_bytes(self, key: str) -> bytes:
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None:
            return hit
        value = self._inner.read_bytes(key)
        with self._lock:
            if len(self._cache) >= self._max_entries:
                self._cache.clear()
            self._cache[key] = value
        return value

    def read_text(self, key: str) -> str:
        return self.read_bytes(key).decode("utf-8")

    def read_json(self, key: str) -> object:
        return json.loads(self.read_text(key))

    def exists(self, key: str) -> bool:
        return self._inner.exists(key)

    def delete(self, key: str) -> None:
        self._inner.delete(key)
        self._evict(key)

    def list_keys(self, prefix: str = "") -> list[str]:
        return self._inner.list_keys(prefix)

    def _evict(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)
