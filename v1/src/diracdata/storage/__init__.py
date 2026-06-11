"""Object storage interfaces and implementations."""

from diracdata.storage.factory import object_store_from_settings
from diracdata.storage.local import LocalObjectStore
from diracdata.storage.object_store import ObjectStore
from diracdata.storage.paths import artifact_key
from diracdata.storage.s3 import S3ObjectStore

__all__ = [
    "LocalObjectStore",
    "ObjectStore",
    "S3ObjectStore",
    "artifact_key",
    "object_store_from_settings",
]

