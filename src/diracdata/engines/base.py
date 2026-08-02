"""The engine contract: one `QueryEngine` protocol every connector satisfies, plus a small
`AbstractEngine` base with the shared bits (identity, read-only flag, dialect-correct quoting).

A connector is a `QueryEngine` over one data source. The agent and `ResultStore` depend on this
protocol, never a concrete engine, so a new data store is a new class here and nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]


@runtime_checkable
class QueryEngine(Protocol):
    name: str        # the source name, e.g. "orders_pg"
    dialect: str     # the SQL dialect this engine speaks; drives dialect-specific authoring rules
    read_only: bool

    def list_tables(self) -> list[str]: ...
    def list_columns(self, table_name: str) -> list[str]: ...
    def describe_columns(self, table_name: str) -> list[dict[str, str]]: ...
    def query(self, sql: str, max_rows: int) -> QueryResult: ...          # bounded preview
    def describe_query(self, sql: str) -> list[dict[str, str]]: ...        # types without a full run
    def copy_to_parquet(self, sql: str, out_path: str) -> int: ...         # FULL result -> parquet


class AbstractEngine:
    """Shared engine behaviour: identity, the read-only flag, and quoting. Concrete connectors
    subclass this (or just satisfy `QueryEngine` structurally)."""

    dialect: str = ""

    def __init__(self, *, name: str, read_only: bool = True) -> None:
        self.name = name
        self.read_only = read_only

    @staticmethod
    def quote_ident(value: str) -> str:
        return '"' + str(value).replace('"', '""') + '"'

    @staticmethod
    def quote_literal(value: str) -> str:
        return str(value).replace("'", "''")
