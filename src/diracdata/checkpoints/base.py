"""Conversation CONTINUITY -- durable transcript + running summary per conversation id.

A `Conversation` persists two documents (transcript.md, summary.md) through a CheckpointBackend: any
key/value blob store with read_bytes / write_bytes / exists. The object store (MinIO/S3, via
diracdata.stores) already satisfies it -- so continuity is object-store-native out of the box. Other
backends (Postgres, Redis) are stubs here and land as optional extras (diracdata[checkpoints-postgres],
[checkpoints-redis]) when someone wants transactional or low-latency continuity instead of blob storage.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CheckpointBackend(Protocol):
    """The minimal blob interface a Conversation persists through -- satisfied by any diracdata store."""

    def read_bytes(self, key: str) -> bytes: ...
    def write_bytes(self, key: str, value: bytes, content_type: str | None = None) -> None: ...
    def exists(self, key: str) -> bool: ...
