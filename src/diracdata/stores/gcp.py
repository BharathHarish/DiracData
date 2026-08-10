"""Google Cloud Storage Store -- STUB (open-source later). Install: diracdata[gcp].

Satisfies the Store protocol so `object_store=gcs` works unchanged once implemented (bucket +
google-cloud-storage client, keys mapped to blob names).
"""

from __future__ import annotations

from typing import Any

_NOT_IMPL = ("GCSStore is a stub. Install diracdata[gcp] and implement it, or use the s3/minio or "
             "local store, which work today.")


class GCSStore:
    def __init__(self, bucket: str, *, project: str | None = None, credentials: Any = None,
                 **_: Any) -> None:
        self.bucket = bucket
        raise NotImplementedError(_NOT_IMPL)

    def write_bytes(self, key: str, value: bytes, content_type: str | None = None) -> None:  # pragma: no cover
        raise NotImplementedError(_NOT_IMPL)

    def read_bytes(self, key: str) -> bytes:  # pragma: no cover
        raise NotImplementedError(_NOT_IMPL)

    def write_text(self, key: str, value: str, content_type: str = "text/plain") -> None:  # pragma: no cover
        raise NotImplementedError(_NOT_IMPL)

    def read_text(self, key: str) -> str:  # pragma: no cover
        raise NotImplementedError(_NOT_IMPL)

    def write_json(self, key: str, value: object) -> None:  # pragma: no cover
        raise NotImplementedError(_NOT_IMPL)

    def read_json(self, key: str) -> object:  # pragma: no cover
        raise NotImplementedError(_NOT_IMPL)

    def exists(self, key: str) -> bool:  # pragma: no cover
        raise NotImplementedError(_NOT_IMPL)

    def delete(self, key: str) -> None:  # pragma: no cover
        raise NotImplementedError(_NOT_IMPL)

    def list_keys(self, prefix: str = "") -> list[str]:  # pragma: no cover
        raise NotImplementedError(_NOT_IMPL)
