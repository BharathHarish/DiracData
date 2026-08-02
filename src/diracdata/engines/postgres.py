"""PostgresEngine -- a QueryEngine over a Postgres database via ADBC (Arrow-native).

ADBC returns results as Arrow, so complex Postgres types map cleanly: `jsonb` -> canonical JSON text,
arrays -> Arrow list, `timestamptz` -> UTC timestamp (see `engines/arrow.canonicalize`). The connection
is read-only by default (a source is never written) with an optional statement timeout. The driver is
an optional extra: `pip install diracdata[postgres]`.
"""

from __future__ import annotations

from threading import RLock
from typing import Any
from urllib.parse import quote

from diracdata.engines.arrow import canonicalize
from diracdata.engines.base import AbstractEngine, QueryResult


def _connect(dsn: str):
    try:
        import adbc_driver_postgresql.dbapi as pg
    except ImportError as exc:
        raise RuntimeError(
            "PostgresEngine needs the postgres extra: pip install diracdata[postgres] "
            "(adbc-driver-postgresql, adbc-driver-manager)") from exc
    return pg.connect(dsn)


def _with_options(dsn: str, options: list[str]) -> str:
    """Fold libpq per-connection GUCs into the DSN (read-only + statement_timeout enforced by the
    server, the reliable way with ADBC)."""
    if not options:
        return dsn
    optstr = quote(" ".join(f"-c {o}" for o in options))
    return f"{dsn}{'&' if '?' in dsn else '?'}options={optstr}"


class PostgresEngine(AbstractEngine):
    dialect = "postgres"

    def __init__(self, *, dsn: str, name: str = "postgres", read_only: bool = True,
                 timeout_s: float | None = None, schema: str = "public") -> None:
        super().__init__(name=name, read_only=read_only)
        self._schema = schema
        self._lock = RLock()
        opts = (["default_transaction_read_only=on"] if read_only else []) + \
               ([f"statement_timeout={int(timeout_s * 1000)}"] if timeout_s else [])
        self._con = _connect(_with_options(dsn, opts))

    # ---- Arrow helpers ----------------------------------------------------------------
    def _arrow(self, sql: str) -> Any:
        with self._lock:
            try:
                with self._con.cursor() as cur:
                    cur.execute(sql)
                    table = cur.fetch_arrow_table()
                self._con.commit()   # end the read txn so its locks don't block DDL/writers
                return canonicalize(table)
            except Exception:
                try:
                    self._con.rollback()   # keep the connection usable after a bad query
                except Exception:  # noqa: BLE001
                    pass
                raise

    @staticmethod
    def _rows(table: Any) -> list[tuple]:
        cols = [c.to_pylist() for c in table.columns]
        return list(zip(*cols)) if cols and table.num_rows else []

    # ---- QueryEngine surface ----------------------------------------------------------
    def list_tables(self) -> list[str]:
        t = self._arrow("SELECT table_name FROM information_schema.tables "
                        f"WHERE table_schema = '{self._schema}' AND table_type = 'BASE TABLE' "
                        "ORDER BY table_name")
        return [r[0] for r in self._rows(t)]

    def describe_columns(self, table_name: str) -> list[dict[str, str]]:
        t = self._arrow("SELECT column_name, data_type FROM information_schema.columns "
                        f"WHERE table_schema = '{self._schema}' AND table_name = '{table_name}' "
                        "ORDER BY ordinal_position")
        return [{"column_name": r[0], "column_type": r[1]} for r in self._rows(t)]

    def list_columns(self, table_name: str) -> list[str]:
        return [c["column_name"] for c in self.describe_columns(table_name)]

    def query(self, sql: str, max_rows: int) -> QueryResult:
        clean = sql.strip().rstrip(";")
        t = self._arrow(f"SELECT * FROM ({clean}) AS diracdata_q LIMIT {int(max_rows)}")
        return QueryResult(columns=list(t.column_names), rows=self._rows(t))

    def describe_query(self, sql: str) -> list[dict[str, str]]:
        clean = sql.strip().rstrip(";")
        t = self._arrow(f"SELECT * FROM ({clean}) AS diracdata_q LIMIT 0")
        return [{"column_name": f.name, "column_type": str(f.type)} for f in t.schema]

    def copy_to_parquet(self, sql: str, out_path: str) -> int:
        """Materialize the full result (expected reduced -- aggregate at source) to parquet via Arrow."""
        import pyarrow.parquet as pqt
        table = self._arrow(sql.strip().rstrip(";"))
        pqt.write_table(table, out_path)
        return table.num_rows
