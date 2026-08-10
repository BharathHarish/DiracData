"""diracdata.context -- the READ side of the semantic context (loaded from the object store).

`Context.load(schema)` is the read facade (tables/describe/column+recipe/search/joins/metrics/examples)
the analyst and the future MCP server use. `ContextStore` is the object-store-ONLY home for the
compiled artifacts (see fabric.py for why it isn't pluggable like checkpoints).
"""

from diracdata.context.fabric import (
    ContextStore,
    FabricStore,                    # back-compat alias
    context_store_from_settings,
    fabric_store_from_settings,     # back-compat alias
)
from diracdata.context.reader import Context

__all__ = ["Context", "ContextStore", "context_store_from_settings",
           "FabricStore", "fabric_store_from_settings"]
