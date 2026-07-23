"""v2 query execution helpers."""

from typing import Any, Protocol, runtime_checkable

from diracdata_v2.query.duckdb_engine import DuckDBEngine, QueryResult


@runtime_checkable
class QueryEngine(Protocol):
    """Minimal read-only warehouse interface the agent + tools depend on.

    DuckDB is the only shipped implementation; a Snowflake/BigQuery engine is a
    drop-in behind this protocol, selected by ``settings.sql_engine``.
    """

    def query(self, sql: str, max_rows: int) -> QueryResult: ...
    def list_tables(self) -> list[str]: ...
    def list_columns(self, table: str) -> list[str]: ...


def create_query_engine(settings: Any) -> QueryEngine:
    """Build the configured warehouse engine (``DIRACDATA_SQL_ENGINE``)."""

    engine = str(getattr(settings, "sql_engine", "duckdb")).strip().lower()
    if engine in {"", "duckdb"}:
        return DuckDBEngine(data_root=settings.data_root, schema_name=settings.schema)
    raise NotImplementedError(
        f"SQL engine '{engine}' is not implemented yet; only 'duckdb' ships today."
    )


__all__ = ["DuckDBEngine", "QueryEngine", "QueryResult", "create_query_engine"]
