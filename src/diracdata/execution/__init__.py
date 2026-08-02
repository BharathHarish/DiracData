"""diracdata.execution -- the pluggable execution seam.

The ResultStore runs its two demanding operations (materialize a source query to parquet; combine
result parquets in the reconciler) THROUGH an `Executor`, so they can be bounded (memory + time) and,
later, moved OFF-HOST to a separately-provisioned sandbox behind the SAME interface -- because bulk
data lives in the object store, not the agent's memory.

Backends (selected by `Config.executor`):
- `inline`  (default) -- in-process, bounded: the reconciler's memory_limit makes an over-budget
  combine raise a catchable OutOfMemoryException; an interrupt watchdog turns a hang into an error.
- `sandbox` (Phase 1.5b) -- a remote DuckDB runtime with its own CPU/RAM over the shared object store.
"""

from diracdata.execution.executor import Executor, InlineExecutor, make_executor

__all__ = ["Executor", "InlineExecutor", "make_executor"]
