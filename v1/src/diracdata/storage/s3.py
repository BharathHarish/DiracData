"""S3-compatible object store. Works with AWS S3 and MinIO."""

from __future__ import annotations

import json
from typing import Any


class S3ObjectStore:
    """Object store backed by AWS S3 or a MinIO-compatible endpoint."""

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region_name: str = "us-east-1",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        create_bucket_if_missing: bool = False,
    ) -> None:
        self.bucket = bucket
        self.client = _boto3_client(
            endpoint_url=endpoint_url,
            region_name=region_name,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )
        if create_bucket_if_missing:
            self.ensure_bucket()

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

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
        raise RuntimeError("S3ObjectStore requires boto3. Install project dependencies first.") from exc

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

