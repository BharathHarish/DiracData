"""Small DuckDB runtime for v2 local parquet data."""

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
    def __init__(self, *, data_root: Path, schema_name: str = "fintech_schema") -> None:
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

    def list_tables(self) -> list[str]:
        return sorted(self._tables)

    def list_columns(self, table_name: str) -> list[str]:
        if table_name not in self._tables:
            return []
        with self._lock:
            rows = self._con.execute(f"DESCRIBE {_identifier(table_name)}").fetchall()
        return [str(row[0]) for row in rows]

    def query(self, sql: str, max_rows: int) -> QueryResult:
        clean_sql = sql.strip().rstrip(";")
        if clean_sql.lower().startswith("explain"):
            limited_sql = clean_sql
        else:
            limited_sql = f"SELECT * FROM ({clean_sql}) AS diracdata_v2_query LIMIT {int(max_rows)}"
        with self._lock:
            cursor = self._con.execute(limited_sql)
            columns = [column[0] for column in cursor.description or []]
            rows = cursor.fetchmany(int(max_rows))
        return QueryResult(columns=columns, rows=rows)


def _sql_string(value: str) -> str:
    return value.replace("'", "''")


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
