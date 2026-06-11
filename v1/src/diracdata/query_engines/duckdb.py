"""DuckDB query engine backed by a generic catalog resolver."""

from __future__ import annotations

from dataclasses import dataclass

from diracdata.backends.catalog import CatalogResolver, TableLocation
from diracdata.config.settings import DiracDataSettings
from diracdata.query_engines.duckdb_runtime import ColumnSchema, DuckDBRuntime, QueryResult


@dataclass
class DuckDBQueryEngine:
    """DuckDB query engine that registers catalog tables as views."""

    runtime: DuckDBRuntime
    catalog: CatalogResolver

    @classmethod
    def from_catalog(
        cls,
        settings: DiracDataSettings,
        catalog: CatalogResolver,
    ) -> "DuckDBQueryEngine":
        runtime = DuckDBRuntime(settings.duckdb_database)
        if _requires_s3(catalog.table_locations()):
            runtime.configure_s3(settings)

        for table in catalog.table_locations():
            if table.format != "parquet":
                raise ValueError(f"DuckDBQueryEngine only supports parquet tables today: {table}")
            runtime.register_parquet_view(table.name, table.path)

        return cls(runtime=runtime, catalog=catalog)

    def list_tables(self) -> list[str]:
        return self.runtime.list_tables()

    def describe_table(self, table_name: str) -> list[ColumnSchema]:
        return self.runtime.describe_table(table_name)

    def row_count(self, table_name: str) -> int:
        return self.runtime.row_count(table_name)

    def query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        return self.runtime.query(sql, max_rows=max_rows)

    def close(self) -> None:
        self.runtime.close()


def _requires_s3(tables: list[TableLocation]) -> bool:
    return any(table.path.startswith("s3://") for table in tables)
