"""DQ snapshot history -- a per-table time-series in the object store, so drift is measured over time.

One JSONL object per probe at `dq/<schema>/<source>/<table>.jsonl`, oldest to newest. Data is
ingested frequently, so there is NO reuse-cache: every table touch probes fresh and appends here. The
last `keep` snapshots are retained (the drift window); older ones are trimmed. The agent can read this
series directly (read_dq_history) to inspect the trend and decide.
"""

from __future__ import annotations

import json
from typing import Any


class DQHistory:
    def __init__(self, store: Any, *, schema: str, keep: int) -> None:
        self._store = store
        self._schema = schema
        self._keep = max(1, int(keep))

    def read(self, source: str, table: str) -> list[dict]:
        """The stored snapshots for a table, oldest to newest ([] if none yet)."""
        key = self._key(source, table)
        if not self._store.exists(key):
            return []
        out: list[dict] = []
        for line in self._store.read_text(key).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:              # tolerate a partial/corrupt tail line
                continue
        return out

    def append(self, source: str, table: str, snapshot: dict) -> list[dict]:
        """Append a fresh snapshot and trim to the last `keep`; return the retained series."""
        series = (self.read(source, table) + [snapshot])[-self._keep:]
        text = "".join(json.dumps(s, default=str) + "\n" for s in series)
        self._store.write_text(self._key(source, table), text, content_type="application/x-ndjson")
        return series

    def _key(self, source: str, table: str) -> str:
        return f"dq/{self._schema}/{_seg(source)}/{_seg(table)}.jsonl"


def _seg(value: str) -> str:
    """Keep a path segment safe for an object key (no separators / traversal)."""
    return str(value).replace("/", "_").replace("\\", "_").replace("..", "_") or "_"
