"""The result store -- large-output handling, so a query result never floods the agent's context.

`run(sql)` materializes the FULL result to a parquet file (persisted to the object store for
durability/audit) and returns only a compact ENVELOPE: schema + a bounded preview + the row count.
`query(result_id, sql)` slices that stored parquet -- referenced as the table `result` -- WITHOUT
re-running the (possibly expensive) base query, and out of the main context. Deeper profiling
(null %, distinct, stats) is stats-on-demand: the agent just queries `result`.

Faithfulness: every number the agent reports must come from an envelope preview or a query_result,
never free-typed -- the Phase 3 finish gate enforces that against this store's `result_id`s.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from diracdata.config import Config

_DEFAULTS = Config()


class ResultStore:
    def __init__(self, *, engine: Any, store: Any, schema: str,
                 preview_rows: int = _DEFAULTS.preview_rows,
                 preview_all_max: int = _DEFAULTS.preview_all_max) -> None:
        self.engine = engine
        self.store = store
        self.schema = schema
        self.preview_rows = preview_rows
        self.preview_all_max = preview_all_max
        self._seq = 0
        self._local = Path(tempfile.mkdtemp(prefix="v4results-"))
        self._paths: dict[str, Path] = {}

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

    def run(self, sql: str) -> dict:
        """Execute a SELECT, persist the full result as parquet, and return a compact envelope."""
        self._seq += 1
        rid = f"r{self._seq}"
        local = self._local / f"{rid}.parquet"
        row_count = self.engine.copy_to_parquet(sql, str(local))
        self.store.write_bytes(self._key(rid), local.read_bytes(), "application/x-parquet")
        self._paths[rid] = local
        dtypes = self.engine.describe_query(f"SELECT * FROM read_parquet('{_s(local.as_posix())}')")
        limit = self.preview_all_max if row_count <= self.preview_all_max else self.preview_rows
        prev = self.engine.query(f"SELECT * FROM read_parquet('{_s(local.as_posix())}')", limit)
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

    def query(self, result_id: str, sql: str, max_rows: int = _DEFAULTS.result_query_max_rows) -> dict:
        """Run `sql` over a stored result, referenced as the table `result`."""
        path = self._path(result_id)
        wrapped = f"WITH result AS (SELECT * FROM read_parquet('{_s(path.as_posix())}')) {sql}"
        res = self.engine.query(wrapped, max_rows)
        return {"columns": res.columns, "rows": [list(r) for r in res.rows], "row_count": len(res.rows)}


def _s(value: str) -> str:
    return value.replace("'", "''")
