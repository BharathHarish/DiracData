"""The Executor seam + the bounded in-process backend.

`run(runtime, materialize)` executes `materialize` -- a call that writes a parquet and returns its row
count -- with isolation/bounding, and re-raises on failure so the caller (a tool) can turn it into a
clean error the agent re-plans from. `runtime` is the DuckDB runtime whose in-flight query is
interrupted on timeout. A remote `SandboxExecutor` (Phase 1.5b) implements the same interface against
an off-host DuckDB sandbox over the shared object store.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Protocol


class Executor(Protocol):
    def run(self, runtime: Any, materialize: Callable[[], int]) -> int: ...


class InlineExecutor:
    """In-process, bounded. OOM protection comes from the runtime's own `memory_limit` (an over-budget
    query raises a catchable OutOfMemoryException); a hang past `job_timeout_s` is turned into an error
    by interrupting the runtime's in-flight query. No subprocess, no extra dependency."""

    def __init__(self, *, job_timeout_s: float | None = None) -> None:
        self.job_timeout_s = job_timeout_s

    def run(self, runtime: Any, materialize: Callable[[], int]) -> int:
        if not self.job_timeout_s:
            return materialize()
        timer = threading.Timer(self.job_timeout_s, _interrupt, args=(runtime,))
        timer.daemon = True
        timer.start()
        try:
            return materialize()
        finally:
            timer.cancel()


def _interrupt(runtime: Any) -> None:
    try:
        runtime.interrupt()   # best-effort cancel; the materialize call raises on its own
    except Exception:  # noqa: BLE001
        pass


def make_executor(config: Any) -> Executor:
    """Build the executor selected by `config.executor` (default: bounded inline)."""
    kind = getattr(config, "executor", "inline")
    if kind == "inline":
        return InlineExecutor(job_timeout_s=getattr(config, "exec_job_timeout_s", None))
    raise NotImplementedError(f"executor {kind!r} not available yet")   # 'sandbox' -> Phase 1.5b
