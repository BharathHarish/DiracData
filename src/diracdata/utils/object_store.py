"""Back-compat shim: object stores now live in `diracdata.stores`.

Import from `diracdata.stores` in new code. This re-export keeps existing imports working.
"""

from diracdata.stores import (
    CachingObjectStore,
    LocalObjectStore,
    S3ObjectStore,
    Store,
    object_store_from_settings,
    store_from_settings,
)

__all__ = ["Store", "LocalObjectStore", "S3ObjectStore", "CachingObjectStore",
           "object_store_from_settings", "store_from_settings"]
