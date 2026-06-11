"""Query engine adapters."""

from diracdata.query_engines.base import QueryEngine
from diracdata.query_engines.duckdb import DuckDBQueryEngine
from diracdata.query_engines.duckdb_runtime import ColumnSchema, DuckDBRuntime, QueryResult
from diracdata.query_engines.factory import query_engine_from_settings

__all__ = [
    "ColumnSchema",
    "DuckDBQueryEngine",
    "DuckDBRuntime",
    "QueryEngine",
    "QueryResult",
    "query_engine_from_settings",
]
