"""Back-compat shim: `DuckDBEngine`/`QueryResult` now live in `diracdata.engines`.

Import from `diracdata.engines` in new code. This re-export keeps existing imports working.
"""

from diracdata.engines.base import QueryResult
from diracdata.engines.duckdb import DuckDBEngine

__all__ = ["DuckDBEngine", "QueryResult"]
