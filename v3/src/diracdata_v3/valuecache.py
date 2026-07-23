"""A persistent cache of a column's distinct values.

`profile_column` is the analyst's way to see the real values in a column before it filters on
them (so it maps 'jewellry' -> 'Jewelry', or learns a status column's domain). Distinct-value
scans are the expensive part, so we cache them per (table, column) on disk and reuse across
sessions -- computed lazily, only for the columns an investigation actually inspects.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ColumnValueCache:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._data: dict[str, list[Any]] = {}
        if self.path and self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    @staticmethod
    def _key(table: str, column: str) -> str:
        return f"{table}.{column}"

    def get(self, table: str, column: str) -> list[Any] | None:
        return self._data.get(self._key(table, column))

    def put(self, table: str, column: str, values: list[Any]) -> None:
        self._data[self._key(table, column)] = values
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self._data, default=str))
            except OSError:
                pass
