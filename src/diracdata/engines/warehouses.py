"""Warehouse engine STUBS -- Databricks, Trino, Spark, Snowflake, Redshift.

Each satisfies the QueryEngine contract (via AbstractEngine) and sets its SQL `dialect` (which already
drives dialect-specific authoring rules in the harness), but raises on construction with an install
hint. They graduate to their own modules with a real driver when implemented, behind optional extras
(diracdata[databricks|trino|snowflake] etc.). Until then, use DuckDBEngine or PostgresEngine.
"""

from __future__ import annotations

from typing import Any

from diracdata.engines.base import AbstractEngine, QueryResult


class _WarehouseStub(AbstractEngine):
    dialect = ""
    _extra = ""  # the pip extra that will carry the real driver

    def __init__(self, *, name: str | None = None, read_only: bool = True, **_: Any) -> None:
        super().__init__(name=name or self.dialect, read_only=read_only)
        raise NotImplementedError(
            f"{type(self).__name__} is a stub. Install diracdata[{self._extra}] and implement it, or "
            f"use DuckDBEngine / PostgresEngine, which work today.")

    def list_tables(self) -> list[str]:  # pragma: no cover - unreachable (ctor raises)
        raise NotImplementedError
    def list_columns(self, table_name: str) -> list[str]:  # pragma: no cover
        raise NotImplementedError
    def describe_columns(self, table_name: str) -> list[dict[str, str]]:  # pragma: no cover
        raise NotImplementedError
    def query(self, sql: str, max_rows: int) -> QueryResult:  # pragma: no cover
        raise NotImplementedError
    def describe_query(self, sql: str) -> list[dict[str, str]]:  # pragma: no cover
        raise NotImplementedError
    def copy_to_parquet(self, sql: str, out_path: str) -> int:  # pragma: no cover
        raise NotImplementedError


class DatabricksEngine(_WarehouseStub):
    dialect = "databricks"
    _extra = "databricks"


class TrinoEngine(_WarehouseStub):
    dialect = "trino"
    _extra = "trino"


class SparkEngine(_WarehouseStub):
    dialect = "spark"
    _extra = "spark"


class SnowflakeEngine(_WarehouseStub):
    dialect = "snowflake"
    _extra = "snowflake"


class RedshiftEngine(_WarehouseStub):
    dialect = "redshift"
    _extra = "redshift"
