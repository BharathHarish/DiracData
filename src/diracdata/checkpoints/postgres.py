"""Postgres checkpoint backend -- STUB (open-source later). Install: diracdata[checkpoints-postgres].

Stores each conversation's transcript/summary as rows keyed by (conversation_id, name) so continuity is
transactional and queryable. Satisfies CheckpointBackend so a `Conversation(store=PostgresCheckpointer(...))`
works unchanged once implemented.
"""

from __future__ import annotations

from typing import Any

_NOT_IMPL = ("PostgresCheckpointer is a stub. Install diracdata[checkpoints-postgres] and implement it, "
             "or use the object store (MinIO/S3) backend, which works today.")


class PostgresCheckpointer:
    def __init__(self, *, dsn: str, table: str = "diracdata_checkpoints", **_: Any) -> None:
        self.dsn = dsn
        self.table = table
        raise NotImplementedError(_NOT_IMPL)

    def read_bytes(self, key: str) -> bytes:  # pragma: no cover - stub
        raise NotImplementedError(_NOT_IMPL)

    def write_bytes(self, key: str, value: bytes, content_type: str | None = None) -> None:  # pragma: no cover
        raise NotImplementedError(_NOT_IMPL)

    def exists(self, key: str) -> bool:  # pragma: no cover - stub
        raise NotImplementedError(_NOT_IMPL)
