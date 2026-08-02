"""A per-schema cache of a column's distinct values, in state/<schema>/column_values.json.

`profile_column` uses it to see real values before filtering. Distinct scans are the expensive
part, so we cache them in the object store and reuse across sessions -- computed lazily, only for
the columns an investigation inspects.
"""

from __future__ import annotations

from typing import Any

from diracdata.context.fabric import FabricStore

_FILE = "column_values.json"


class ColumnValueCache:
    def __init__(self, store: FabricStore | None = None, schema: str = "") -> None:
        self.store = store
        self.schema = schema
        self._data: dict[str, list[Any]] = (store.get_state(schema, _FILE, default={}) if store else {}) or {}

    @staticmethod
    def _key(table: str, column: str) -> str:
        return f"{table}.{column}"

    def get(self, table: str, column: str) -> list[Any] | None:
        return self._data.get(self._key(table, column))

    def put(self, table: str, column: str, values: list[Any]) -> None:
        self._data[self._key(table, column)] = values
        if self.store is not None:
            self.store.put_state(self.schema, _FILE, self._data)
