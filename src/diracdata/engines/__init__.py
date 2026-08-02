"""diracdata.engines -- the pluggable data-source layer.

`QueryEngine` is the contract; `AbstractEngine` a convenience base; `DuckDBEngine` the reference
implementation (and the cross-source reconciler); `SourceRegistry`/`EngineSpec` declare the sources
the agent can reach. New data stores are new `QueryEngine`s here and nothing else changes.
"""

from diracdata.engines.base import AbstractEngine, QueryEngine, QueryResult
from diracdata.engines.duckdb import DuckDBEngine, Reconciler
from diracdata.engines.registry import EngineSpec, SourceRegistry

__all__ = ["QueryEngine", "AbstractEngine", "QueryResult", "DuckDBEngine", "Reconciler",
           "EngineSpec", "SourceRegistry"]
