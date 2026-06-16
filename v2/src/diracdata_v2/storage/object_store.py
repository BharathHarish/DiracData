"""Minimal object-store abstraction for v2 learning artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from diracdata_v2.settings import V2Settings


class LocalObjectStore:
    """Filesystem-backed store with object-store-like keys."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def write_bytes(self, key: str, value: bytes, content_type: str | None = None) -> None:
        del content_type
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def write_text(self, key: str, value: str, content_type: str = "text/plain") -> None:
        del content_type
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def read_text(self, key: str) -> str:
        return self._path(key).read_text(encoding="utf-8")

    def write_json(self, key: str, value: object) -> None:
        self.write_text(
            key,
            json.dumps(value, indent=2, sort_keys=True),
            content_type="application/json",
        )

    def read_json(self, key: str) -> object:
        return json.loads(self.read_text(key))

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def list_keys(self, prefix: str = "") -> list[str]:
        base = self._path(prefix)
        if base.is_file():
            return [prefix.strip("/")]
        if not base.exists():
            return []
        return [
            str(path.relative_to(self.root))
            for path in sorted(base.rglob("*"))
            if path.is_file()
        ]

    def _path(self, key: str) -> Path:
        clean_key = key.strip("/")
        path = (self.root / clean_key).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError(f"Object key escapes local root: {key}")
        return path


class S3ObjectStore:
    """S3 or MinIO-compatible store for generated artifacts."""

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region_name: str = "us-east-1",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.client = _boto3_client(
            endpoint_url=endpoint_url,
            region_name=region_name,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )

    def write_bytes(self, key: str, value: bytes, content_type: str | None = None) -> None:
        extra: dict[str, Any] = {}
        if content_type is not None:
            extra["ContentType"] = content_type
        self.client.put_object(Bucket=self.bucket, Key=_clean_key(key), Body=value, **extra)

    def read_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=_clean_key(key))
        return response["Body"].read()

    def write_text(self, key: str, value: str, content_type: str = "text/plain") -> None:
        self.write_bytes(key, value.encode("utf-8"), content_type=content_type)

    def read_text(self, key: str) -> str:
        return self.read_bytes(key).decode("utf-8")

    def write_json(self, key: str, value: object) -> None:
        self.write_text(
            key,
            json.dumps(value, indent=2, sort_keys=True),
            content_type="application/json",
        )

    def read_json(self, key: str) -> object:
        return json.loads(self.read_text(key))

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_clean_key(key))
            return True
        except Exception:
            return False

    def list_keys(self, prefix: str = "") -> list[str]:
        clean_prefix = prefix.strip("/")
        if clean_prefix:
            clean_prefix += "/"
        paginator = self.client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=clean_prefix):
            keys.extend(item["Key"] for item in page.get("Contents", []))
        return sorted(keys)


def object_store_from_settings(settings: V2Settings) -> LocalObjectStore | S3ObjectStore:
    kind = settings.object_store.strip().lower()
    if kind == "local":
        return LocalObjectStore(settings.local_artifact_root)
    if kind in {"s3", "minio"}:
        return S3ObjectStore(
            settings.artifact_bucket,
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
    raise ValueError(f"Unsupported DIRACDATA_OBJECT_STORE: {settings.object_store}")


def _boto3_client(
    *,
    endpoint_url: str | None,
    region_name: str,
    aws_access_key_id: str | None,
    aws_secret_access_key: str | None,
) -> object:
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("S3 object storage requires boto3") from exc
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region_name,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )


def _clean_key(key: str) -> str:
    clean = key.strip("/")
    if not clean:
        raise ValueError("object key cannot be empty")
    return clean
