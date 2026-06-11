"""Object store construction from settings."""

from __future__ import annotations

from diracdata.config.settings import DiracDataSettings
from diracdata.storage.local import LocalObjectStore
from diracdata.storage.object_store import ObjectStore
from diracdata.storage.s3 import S3ObjectStore


def object_store_from_settings(
    settings: DiracDataSettings,
    *,
    create_bucket_if_missing: bool = False,
) -> ObjectStore:
    kind = settings.object_store.lower()
    if kind == "local":
        return LocalObjectStore(settings.local_artifact_root)
    if kind in {"s3", "minio"}:
        return S3ObjectStore(
            settings.artifact_bucket,
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            create_bucket_if_missing=create_bucket_if_missing,
        )
    raise ValueError(f"Unsupported DIRACDATA_OBJECT_STORE: {settings.object_store}")

