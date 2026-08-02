"""Read-only DuckDB runtime over a schema's local parquet files (each table = a view).

The reference `QueryEngine`: DuckDB also backs the cross-source RECONCILER (combining result
parquets), so this surface is the contract every other connector implements.
"""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from diracdata.engines.base import AbstractEngine, QueryResult


class DuckDBEngine(AbstractEngine):
    dialect = "duckdb"

    def __init__(self, *, data_root: Path, schema_name: str = "default_schema",
                 name: str | None = None, read_only: bool = True) -> None:
        super().__init__(name=name or schema_name, read_only=read_only)
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
                    f"CREATE OR REPLACE VIEW {table} AS "
                    f"SELECT * FROM read_parquet('{self.quote_literal(path.as_posix())}')")
            self._tables.add(table)

    def list_tables(self) -> list[str]:
        return sorted(self._tables)

    def list_columns(self, table_name: str) -> list[str]:
        if table_name not in self._tables:
            return []
        with self._lock:
            rows = self._con.execute(f"DESCRIBE {self.quote_ident(table_name)}").fetchall()
        return [str(row[0]) for row in rows]

    def describe_columns(self, table_name: str) -> list[dict[str, str]]:
        if table_name not in self._tables:
            return []
        with self._lock:
            rows = self._con.execute(f"DESCRIBE {self.quote_ident(table_name)}").fetchall()
        return [{"column_name": str(row[0]), "column_type": str(row[1])} for row in rows]

    def query(self, sql: str, max_rows: int) -> QueryResult:
        clean_sql = sql.strip().rstrip(";")
        limited_sql = f"SELECT * FROM ({clean_sql}) AS diracdata_query LIMIT {int(max_rows)}"
        with self._lock:
            cursor = self._con.execute(limited_sql)
            columns = [column[0] for column in cursor.description or []]
            rows = cursor.fetchmany(int(max_rows))
        return QueryResult(columns=columns, rows=rows)

    def copy_to_parquet(self, sql: str, out_path: str) -> int:
        """Materialize a SELECT's FULL result to parquet (no row cap) and return its row count, so
        large outputs live on disk/object store, not in the agent's context."""
        clean_sql = sql.strip().rstrip(";")
        with self._lock:
            self._con.execute(
                f"COPY ({clean_sql}) TO '{self.quote_literal(out_path)}' (FORMAT PARQUET)")
            row = self._con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{self.quote_literal(out_path)}')").fetchone()
        return int(row[0]) if row else 0

    def describe_query(self, sql: str) -> list[dict[str, str]]:
        """Column names + types for an arbitrary SELECT, without running it for rows."""
        clean_sql = sql.strip().rstrip(";")
        with self._lock:
            rows = self._con.execute(f"DESCRIBE ({clean_sql})").fetchall()
        return [{"column_name": str(r[0]), "column_type": str(r[1])} for r in rows]
