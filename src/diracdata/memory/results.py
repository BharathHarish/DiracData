"""The result store -- large-output handling, so a query result never floods the agent's context.

`run(sql)` materializes the FULL result to a parquet file on the SOURCE engine (persisted to the
object store for durability/audit) and returns only a compact ENVELOPE: schema + a bounded preview +
the row count. `query(result_id, sql)` slices that stored parquet -- referenced as the table
`result` -- and `combine(result_ids, sql)` joins MANY stored results together; both run on the
RECONCILER (a source-independent, locked-down DuckDB that combines result parquets and spills to disk
instead of OOMing), never re-running the base queries and out of the main context.

Faithfulness: every number the agent reports must come from an envelope preview or a query/combine
result, never free-typed -- the finish gate enforces that against this store's `result_id`s.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from threading import Lock
from typing import Any

from diracdata.config import Config
from diracdata.engines.duckdb import Reconciler
from diracdata.execution import InlineExecutor

_DEFAULTS = Config()


class ResultStore:
    def __init__(self, *, engine: Any, store: Any, schema: str,
                 preview_rows: int = _DEFAULTS.preview_rows,
                 preview_all_max: int = _DEFAULTS.preview_all_max,
                 reconciler: Any = None, executor: Any = None, sources: Any = None,
                 reconciler_memory_limit: str = _DEFAULTS.reconciler_memory_limit,
                 reconciler_temp_dir: str | None = _DEFAULTS.reconciler_temp_dir,
                 reconciler_threads: int | None = _DEFAULTS.reconciler_threads) -> None:
        self.engine = engine            # DEFAULT source: materializes its query to parquet
        self._sources = sources         # optional SourceRegistry -> run(sql, source=...) routes here
        self.store = store
        self.schema = schema
        self.preview_rows = preview_rows
        self.preview_all_max = preview_all_max
        self._seq = 0
        self._seq_lock = Lock()   # result_ids stay unique when parallel sub-agents materialize at once
        self._local = Path(tempfile.mkdtemp(prefix="v4results-"))
        self._paths: dict[str, Path] = {}
        # RECONCILER: reads back + combines result parquets, independent of any source.
        self.reconciler = reconciler or Reconciler(
            memory_limit=reconciler_memory_limit,
            temp_dir=reconciler_temp_dir or str(self._local / "spill"),
            threads=reconciler_threads)
        # EXECUTOR: runs the two demanding materialize calls with memory/time bounding (default inline).
        self.executor = executor or InlineExecutor()

    def _key(self, rid: str) -> str:
        return f"results/{self.schema}/{rid}.parquet"

    def _path(self, rid: str) -> Path:
        """Local parquet path for a result_id, fetching from the object store if not cached."""
        p = self._paths.get(rid)
        if p is not None and p.exists():
            return p
        p = self._local / f"{rid}.parquet"
        p.write_bytes(self.store.read_bytes(self._key(rid)))
        self._paths[rid] = p
        return p

    def run(self, sql: str, source: str | None = None) -> dict:
        """Execute a SELECT on a source (the default, or `source` from the registry), persist the full
        result as parquet, return an envelope."""
        eng = self._sources.get(source) if (source and self._sources) else self.engine
        rid = self._next_rid()
        local = self._local / f"{rid}.parquet"
        row_count = self.executor.run(eng, lambda: eng.copy_to_parquet(sql, str(local)))
        return self._persist(rid, local, sql, row_count)

    def query(self, result_id: str, sql: str, max_rows: int = _DEFAULTS.result_query_max_rows) -> dict:
        """Run `sql` over ONE stored result on the reconciler, referenced as the table `result`."""
        self.reconciler.register_view("result", self._path(result_id).as_posix())
        res = self.reconciler.query(sql, max_rows)
        return {"columns": res.columns, "rows": [list(r) for r in res.rows], "row_count": len(res.rows)}

    def combine(self, result_ids: list[str], sql: str) -> dict:
        """Join/aggregate MANY stored results on the reconciler (each referenced by its result_id),
        persist the combined output as a NEW result parquet, and return its envelope."""
        for rid in result_ids:
            self.reconciler.register_view(rid, self._path(rid).as_posix())
        rid = self._next_rid()
        local = self._local / f"{rid}.parquet"
        row_count = self.executor.run(self.reconciler, lambda: self.reconciler.copy_to_parquet(sql, str(local)))
        return self._persist(rid, local, sql, row_count)

    # ---- internals -------------------------------------------------------------------
    def _next_rid(self) -> str:
        with self._seq_lock:
            self._seq += 1
            return f"r{self._seq}"

    def _persist(self, rid: str, local: Path, sql: str, row_count: int) -> dict:
        """Store a materialized parquet and build its envelope (schema + bounded preview) via the
        reconciler (reads parquet regardless of which engine produced it)."""
        self.store.write_bytes(self._key(rid), local.read_bytes(), "application/x-parquet")
        self._paths[rid] = local
        read = f"SELECT * FROM read_parquet('{_s(local.as_posix())}')"
        dtypes = self.reconciler.describe_query(read)
        limit = self.preview_all_max if row_count <= self.preview_all_max else self.preview_rows
        prev = self.reconciler.query(read, limit)
        return {
            "result_id": rid,
            "columns": [d["column_name"] for d in dtypes],
            "dtypes": {d["column_name"]: d["column_type"] for d in dtypes},
            "row_count": row_count,
            "sql": sql,
            "preview_rows": len(prev.rows),
            "truncated": row_count > len(prev.rows),
            "preview": [list(r) for r in prev.rows],
        }


def _s(value: str) -> str:
    return value.replace("'", "''")
