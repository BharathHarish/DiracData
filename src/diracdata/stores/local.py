"""Filesystem-backed Store with object-store-like keys (dev / offline / tests)."""

from __future__ import annotations

import json
from pathlib import Path


class LocalObjectStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def write_bytes(self, key: str, value: bytes, content_type: str | None = None) -> None:
        del content_type
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def write_text(self, key: str, value: str, content_type: str = "text/plain") -> None:
        del content_type
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def read_text(self, key: str) -> str:
        return self._path(key).read_text(encoding="utf-8")

    def write_json(self, key: str, value: object) -> None:
        self.write_text(key, json.dumps(value, indent=2, sort_keys=True), content_type="application/json")

    def read_json(self, key: str) -> object:
        return json.loads(self.read_text(key))

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def list_keys(self, prefix: str = "") -> list[str]:
        base = self._path(prefix)
        if base.is_file():
            return [prefix.strip("/")]
        if not base.exists():
            return []
        return [str(path.relative_to(self.root)) for path in sorted(base.rglob("*")) if path.is_file()]

    def _path(self, key: str) -> Path:
        clean_key = key.strip("/")
        path = (self.root / clean_key).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError(f"Object key escapes local root: {key}")
        return path
