"""Redis checkpoint backend -- STUB (open-source later). Install: diracdata[checkpoints-redis].

Keeps each conversation's transcript/summary under a namespaced key for low-latency continuity.
Satisfies CheckpointBackend so `Conversation(store=RedisCheckpointer(...))` works once implemented.
"""

from __future__ import annotations

from typing import Any

_NOT_IMPL = ("RedisCheckpointer is a stub. Install diracdata[checkpoints-redis] and implement it, or "
             "use the object store (MinIO/S3) backend, which works today.")


class RedisCheckpointer:
    def __init__(self, *, url: str, prefix: str = "diracdata:ckpt", **_: Any) -> None:
        self.url = url
        self.prefix = prefix
        raise NotImplementedError(_NOT_IMPL)

    def read_bytes(self, key: str) -> bytes:  # pragma: no cover - stub
        raise NotImplementedError(_NOT_IMPL)

    def write_bytes(self, key: str, value: bytes, content_type: str | None = None) -> None:  # pragma: no cover
        raise NotImplementedError(_NOT_IMPL)

    def exists(self, key: str) -> bool:  # pragma: no cover - stub
        raise NotImplementedError(_NOT_IMPL)
