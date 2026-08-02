"""Small read-only DuckDB runtime over the schema's local parquet files (each table = a view)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]


class DuckDBEngine:
    def __init__(self, *, data_root: Path, schema_name: str = "default_schema") -> None:
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("DuckDBEngine requires duckdb") from exc
        self._con = duckdb.connect(":memory:")
        self._lock = RLock()
        self._tables: set[str] = set()
        parquet_root = data_root / schema_name / "parquet"
        for path in sorted(parquet_root.rglob("*.parquet")):
            table = path.stem
            with self._lock:
                self._con.execute(
                    f"CREATE OR REPLACE VIEW {table} AS SELECT * FROM read_parquet('{_sql_string(path.as_posix())}')",
                )
            self._tables.add(table)

    dialect = "duckdb"  # the SQL dialect this engine speaks; drives dialect-specific authoring rules

    def list_tables(self) -> list[str]:
        return sorted(self._tables)

    def list_columns(self, table_name: str) -> list[str]:
        if table_name not in self._tables:
            return []
        with self._lock:
            rows = self._con.execute(f"DESCRIBE {_identifier(table_name)}").fetchall()
        return [str(row[0]) for row in rows]

    def describe_columns(self, table_name: str) -> list[dict[str, str]]:
        if table_name not in self._tables:
            return []
        with self._lock:
            rows = self._con.execute(f"DESCRIBE {_identifier(table_name)}").fetchall()
        return [
            {
                "column_name": str(row[0]),
                "column_type": str(row[1]),
            }
            for row in rows
        ]

    def query(self, sql: str, max_rows: int) -> QueryResult:
        clean_sql = sql.strip().rstrip(";")
        limited_sql = f"SELECT * FROM ({clean_sql}) AS diracdata_query LIMIT {int(max_rows)}"
        with self._lock:
            cursor = self._con.execute(limited_sql)
            columns = [column[0] for column in cursor.description or []]
            rows = cursor.fetchmany(int(max_rows))
        return QueryResult(columns=columns, rows=rows)

    def copy_to_parquet(self, sql: str, out_path: str) -> int:
        """Materialize a SELECT's FULL result to a parquet file (no row cap) and return its row
        count. Used by the result store so large outputs live on disk/object store, not in context."""
        clean_sql = sql.strip().rstrip(";")
        with self._lock:
            self._con.execute(
                f"COPY ({clean_sql}) TO '{_sql_string(out_path)}' (FORMAT PARQUET)")
            row = self._con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{_sql_string(out_path)}')").fetchone()
        return int(row[0]) if row else 0

    def describe_query(self, sql: str) -> list[dict[str, str]]:
        """Column names + types for an arbitrary SELECT, without running it for rows."""
        clean_sql = sql.strip().rstrip(";")
        with self._lock:
            rows = self._con.execute(f"DESCRIBE ({clean_sql})").fetchall()
        return [{"column_name": str(r[0]), "column_type": str(r[1])} for r in rows]


def _sql_string(value: str) -> str:
    return value.replace("'", "''")


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
