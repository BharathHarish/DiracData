"""Query engine protocol."""

from __future__ import annotations

from typing import Protocol

from diracdata.query_engines.duckdb_runtime import ColumnSchema, QueryResult


class QueryEngine(Protocol):
    """Minimal query engine API used by learning and answering."""

    def list_tables(self) -> list[str]: ...
    def describe_table(self, table_name: str) -> list[ColumnSchema]: ...
    def row_count(self, table_name: str) -> int: ...
    def query(self, sql: str, max_rows: int | None = None) -> QueryResult: ...
    def close(self) -> None: ...
