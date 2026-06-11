"""Catalog resolver interfaces and table metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TableLocation:
    """Physical table location resolved from a customer-facing catalog reference."""

    name: str
    path: str
    format: str = "parquet"
    description: str | None = None


class CatalogResolver(Protocol):
    """Resolve catalog/database/schema table references into queryable locations."""

    catalog: str
    database: str
    schema: str

    def list_tables(self) -> list[str]: ...
    def get_table(self, table_name: str) -> TableLocation: ...
    def table_locations(self) -> list[TableLocation]: ...

