"""diracdata.stores -- one Store protocol, pluggable backends (local, s3/minio, caching; azure/gcp stubs).

`store_from_settings(config)` builds the backend from DIRACDATA_OBJECT_STORE. Azure/GCS are lazy so
their optional deps are only needed when actually selected.
"""

from __future__ import annotations

from typing import Any

from diracdata.config import Config
from diracdata.stores.base import Store
from diracdata.stores.caching import CachingObjectStore
from diracdata.stores.local import LocalObjectStore
from diracdata.stores.s3 import S3ObjectStore

__all__ = ["Store", "LocalObjectStore", "S3ObjectStore", "CachingObjectStore",
           "AzureBlobStore", "GCSStore", "store_from_settings", "object_store_from_settings"]


def store_from_settings(settings: Config) -> Any:
    """Pick the object-store backend from config. Remote backends are wrapped in a read-through cache."""
    kind = settings.object_store.strip().lower()
    if kind == "local":
        return LocalObjectStore(settings.local_artifact_root)
    if kind in {"s3", "minio"}:
        return CachingObjectStore(
            S3ObjectStore(settings.artifact_bucket, endpoint_url=settings.s3_endpoint_url,
                          region_name=settings.aws_region, aws_access_key_id=settings.aws_access_key_id,
                          aws_secret_access_key=settings.aws_secret_access_key),
            max_entries=settings.cache_max_entries)
    if kind in {"azure", "azure_blob"}:
        from diracdata.stores.azure import AzureBlobStore
        return AzureBlobStore(settings.artifact_bucket)
    if kind in {"gcs", "gcp"}:
        from diracdata.stores.gcp import GCSStore
        return GCSStore(settings.artifact_bucket)
    raise ValueError(f"Unsupported DIRACDATA_OBJECT_STORE: {settings.object_store}")


# canonical name is store_from_settings; keep the historical alias so existing callers are unaffected
object_store_from_settings = store_from_settings


def __getattr__(name: str):
    if name == "AzureBlobStore":
        from diracdata.stores.azure import AzureBlobStore
        return AzureBlobStore
    if name == "GCSStore":
        from diracdata.stores.gcp import GCSStore
        return GCSStore
    raise AttributeError(name)
