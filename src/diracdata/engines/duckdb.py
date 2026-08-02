"""DuckDB runtimes:

- `DuckDBEngine` -- the reference read-only source over a schema's local parquet (each table = a view).
- `Reconciler`  -- a locked-down, source-independent DuckDB that COMBINES result parquets (the
  cross-source join substrate). memory_limit + temp_directory make it spill to disk instead of OOMing.

Both share `_DuckDBRuntime` (bounded query / DESCRIBE / COPY over one connection).
"""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

from diracdata.engines.base import AbstractEngine, QueryResult


def _lit(value: str) -> str:
    return str(value).replace("'", "''")


class _DuckDBRuntime:
    """Shared execution over `self._con` behind `self._lock`: bounded query, typing, full COPY."""

    _con: Any
    _lock: RLock

    def query(self, sql: str, max_rows: int) -> QueryResult:
        clean = sql.strip().rstrip(";")
        with self._lock:
            cur = self._con.execute(f"SELECT * FROM ({clean}) AS diracdata_query LIMIT {int(max_rows)}")
            cols = [c[0] for c in cur.description or []]
            rows = cur.fetchmany(int(max_rows))
        return QueryResult(columns=cols, rows=rows)

    def describe_query(self, sql: str) -> list[dict[str, str]]:
        clean = sql.strip().rstrip(";")
        with self._lock:
            rows = self._con.execute(f"DESCRIBE ({clean})").fetchall()
        return [{"column_name": str(r[0]), "column_type": str(r[1])} for r in rows]

    def copy_to_parquet(self, sql: str, out_path: str) -> int:
        """Materialize a SELECT's FULL result to parquet (no row cap) and return its row count."""
        clean = sql.strip().rstrip(";")
        with self._lock:
            self._con.execute(f"COPY ({clean}) TO '{_lit(out_path)}' (FORMAT PARQUET)")
            row = self._con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{_lit(out_path)}')").fetchone()
        return int(row[0]) if row else 0

    def interrupt(self) -> None:
        """Cancel the currently-running query (used by the executor's timeout watchdog)."""
        self._con.interrupt()


class DuckDBEngine(_DuckDBRuntime, AbstractEngine):
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
                    f"CREATE OR REPLACE VIEW {self.quote_ident(table)} AS "
                    f"SELECT * FROM read_parquet('{_lit(path.as_posix())}')")
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


class Reconciler(_DuckDBRuntime):
    """A locked-down DuckDB that combines RESULT PARQUETS -- independent of any source (it only ever
    sees reduced result parquets). `memory_limit` + `temp_directory` spill to disk instead of OOMing;
    the HTTP filesystem is disabled so it cannot reach the network. Bind a result with `register_view`,
    then query/COPY referencing that name."""

    dialect = "duckdb"

    def __init__(self, *, memory_limit: str = "2GB", temp_dir: str | None = None,
                 threads: int | None = None) -> None:
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("Reconciler requires duckdb") from exc
        self._con = duckdb.connect(":memory:")
        self._lock = RLock()
        self._con.execute(f"SET memory_limit='{_lit(memory_limit)}'")
        if temp_dir:
            Path(temp_dir).mkdir(parents=True, exist_ok=True)
            self._con.execute(f"SET temp_directory='{_lit(temp_dir)}'")
        if threads:
            self._con.execute(f"SET threads={int(threads)}")
        # Stream large results (sort/aggregate/COPY spill to temp_directory) instead of buffering the
        # whole result in memory -- this is what turns "large combine" into "spills, not OOM".
        self._con.execute("SET preserve_insertion_order=false")
        self._con.execute("SET disabled_filesystems='HTTPFileSystem'")   # no network reach

    def register_view(self, name: str, parquet_path: str) -> None:
        """Bind a stored result parquet as a table named `name` (referenced by combine SQL)."""
        with self._lock:
            self._con.execute(
                f'CREATE OR REPLACE VIEW "{name}" AS SELECT * FROM read_parquet(\'{_lit(parquet_path)}\')')
