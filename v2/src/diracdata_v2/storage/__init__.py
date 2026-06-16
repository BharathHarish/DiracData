"""Small v2 object-store helpers for local and S3-compatible artifacts."""

from diracdata_v2.storage.object_store import LocalObjectStore, S3ObjectStore, object_store_from_settings

__all__ = ["LocalObjectStore", "S3ObjectStore", "object_store_from_settings"]
