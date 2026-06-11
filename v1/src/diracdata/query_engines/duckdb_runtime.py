"""Reusable DuckDB runtime helpers for local parquet-backed datasets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from diracdata.config.settings import DiracDataSettings
from diracdata.core.sql import quote_identifier, sql_string


@dataclass(frozen=True)
class ColumnSchema:
    """Column metadata returned by DuckDB table introspection."""

    name: str
    data_type: str
    nullable: bool | None = None


@dataclass(frozen=True)
class QueryResult:
    """Small structured query result for harness and future learning utilities."""

    columns: list[str]
    rows: list[tuple[Any, ...]]


class DuckDBRuntime:
    """DuckDB connection wrapper for registering parquet files as queryable views."""

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.database = database
        self.connection = duckdb.connect(str(database))

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "DuckDBRuntime":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def register_parquet_views(self, data_dir: str | Path) -> list[str]:
        """Create one DuckDB view per parquet file in a directory."""
        data_path = Path(data_dir)
        parquet_files = sorted(data_path.glob("*.parquet"))
        if not parquet_files:
            raise FileNotFoundError(f"No parquet files found in {data_path}")

        tables = []
        for parquet_file in parquet_files:
            table = parquet_file.stem
            tables.append(table)
            self.connection.execute(
                f"CREATE OR REPLACE VIEW {quote_identifier(table)} AS "
                f"SELECT * FROM read_parquet({sql_string(parquet_file)})"
            )
        return tables

    def register_parquet_view(self, table_name: str, path: str | Path) -> None:
        """Create a DuckDB view for one parquet table path or URI."""
        self.connection.execute(
            f"CREATE OR REPLACE VIEW {quote_identifier(table_name)} AS "
            f"SELECT * FROM read_parquet({sql_string(path)})"
        )

    def configure_s3(self, settings: DiracDataSettings) -> None:
        """Configure DuckDB httpfs for AWS S3 or local MinIO."""
        try:
            self.connection.execute("LOAD httpfs")
        except duckdb.IOException:
            self.connection.execute("INSTALL httpfs")
            self.connection.execute("LOAD httpfs")
        self.connection.execute(f"SET s3_region={sql_string(settings.aws_region)}")
        if settings.s3_endpoint_url:
            endpoint = settings.s3_endpoint_url
            endpoint = endpoint.removeprefix("http://").removeprefix("https://")
            self.connection.execute(f"SET s3_endpoint={sql_string(endpoint)}")
            self.connection.execute("SET s3_url_style='path'")
            self.connection.execute(
                f"SET s3_use_ssl={'true' if settings.s3_endpoint_url.startswith('https://') else 'false'}"
            )
        if settings.aws_access_key_id:
            self.connection.execute(f"SET s3_access_key_id={sql_string(settings.aws_access_key_id)}")
        if settings.aws_secret_access_key:
            self.connection.execute(
                f"SET s3_secret_access_key={sql_string(settings.aws_secret_access_key)}"
            )

    def register_s3_parquet_views(
        self,
        *,
        bucket: str,
        prefix: str,
        tables: list[str],
    ) -> list[str]:
        """Create one DuckDB view per S3 parquet table."""
        clean_prefix = prefix.strip("/")
        for table in tables:
            object_uri = f"s3://{bucket}/{clean_prefix}/{table}.parquet"
            self.connection.execute(
                f"CREATE OR REPLACE VIEW {quote_identifier(table)} AS "
                f"SELECT * FROM read_parquet({sql_string(object_uri)})"
            )
        return tables

    def list_tables(self) -> list[str]:
        return [row[0] for row in self.connection.execute("SHOW TABLES").fetchall()]

    def describe_table(self, table_name: str) -> list[ColumnSchema]:
        rows = self.connection.execute(f"DESCRIBE {quote_identifier(table_name)}").fetchall()
        return [
            ColumnSchema(
                name=row[0],
                data_type=row[1],
                nullable=_parse_nullable(row[2]) if len(row) > 2 else None,
            )
            for row in rows
        ]

    def row_count(self, table_name: str) -> int:
        return self.connection.execute(
            f"SELECT count(*) FROM {quote_identifier(table_name)}"
        ).fetchone()[0]

    def query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        limited_sql = sql
        if max_rows is not None:
            limited_sql = f"SELECT * FROM ({sql}) AS diracdata_limited_query LIMIT {int(max_rows)}"

        cursor = self.connection.execute(limited_sql)
        columns = [description[0] for description in cursor.description or []]
        rows = cursor.fetchall()
        return QueryResult(columns=columns, rows=rows)

def _parse_nullable(value: object) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text == "YES":
        return True
    if text == "NO":
        return False
    return None
