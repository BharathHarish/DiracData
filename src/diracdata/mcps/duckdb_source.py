"""A local DuckDB source for the MCP: attach a `.duckdb` file, or glob a directory / single file of
parquet + csv. Reuses the engine's query runtime (`_DuckDBRuntime`) so nothing in the core engine is
touched -- this is a thin, MCP-local source so a desktop user's own data actually loads.
"""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from diracdata.engines.base import AbstractEngine
from diracdata.engines.duckdb import _DuckDBRuntime, _lit


class DuckDBFileEngine(_DuckDBRuntime, AbstractEngine):
    """QueryEngine over a local .duckdb file, or a dir / single file of .parquet/.csv."""

    dialect = "duckdb"

    def __init__(self, *, path: str, name: str = "local", read_only: bool = True) -> None:
        super().__init__(name=name, read_only=read_only)
        import duckdb
        self._con = duckdb.connect(":memory:")
        self._lock = RLock()
        self._tables: set[str] = set()
        p = Path(path).expanduser()
        if p.is_dir():
            self._register_glob(p)
        elif p.suffix == ".duckdb":
            self._register_attached(p)
        elif p.suffix in (".parquet", ".csv"):
            self._register_file(p.stem, p)
        else:
            raise ValueError(f"unsupported source: {path} (want a .duckdb file, a dir, or a .parquet/.csv)")

    def _view(self, table: str, source_sql: str) -> None:
        with self._lock:
            self._con.execute(f"CREATE OR REPLACE VIEW {self.quote_ident(table)} AS SELECT * FROM {source_sql}")
        self._tables.add(table)

    def _register_file(self, table: str, p: Path) -> None:
        reader = "read_csv_auto" if p.suffix == ".csv" else "read_parquet"
        self._view(table, f"{reader}('{_lit(p.as_posix())}')")

    def _register_glob(self, root: Path) -> None:
        for p in sorted(root.rglob("*.parquet")) + sorted(root.rglob("*.csv")):
            if p.stem not in self._tables:
                self._register_file(p.stem, p)

    def _register_attached(self, p: Path) -> None:
        with self._lock:
            self._con.execute(f"ATTACH '{_lit(p.as_posix())}' AS src (READ_ONLY)")
            rows = self._con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_catalog = 'src'").fetchall()
        for (t,) in rows:
            self._view(str(t), f'src."{t}"')

    def list_tables(self) -> list[str]:
        return sorted(self._tables)

    def list_columns(self, table_name: str) -> list[str]:
        if table_name not in self._tables:
            return []
        with self._lock:
            rows = self._con.execute(f"DESCRIBE {self.quote_ident(table_name)}").fetchall()
        return [str(r[0]) for r in rows]

    def describe_columns(self, table_name: str) -> list[dict[str, str]]:
        if table_name not in self._tables:
            return []
        with self._lock:
            rows = self._con.execute(f"DESCRIBE {self.quote_ident(table_name)}").fetchall()
        return [{"column_name": str(r[0]), "column_type": str(r[1])} for r in rows]
