"""MemoryConsolidator -- the async plumbing behind agentic memory. On a finished turn the agent
ENQUEUES the turn trace as a durable candidate (instant, non-blocking); a background daemon thread
DRAINS the queue and hands each candidate to the curator, which folds salient knowledge into the
ExperienceBook. Nothing here decides WHAT to keep -- that is the curator's LLM judgement (agents/curator).

Durability: candidates live under `experiences/<schema>/candidates/` in the object store, so a
single-shot process that exits before the thread finishes loses nothing -- the leftover is drained next
run (or by scripts/consolidate.py). Best-effort: a candidate is removed after one processing attempt, so
the queue never clogs (the full record still lives in the conversation transcript).
"""

from __future__ import annotations

import threading
import uuid
from typing import Callable

from diracdata.memory.book import ExperienceBook
from diracdata.utils.streaming import Sink, null_sink

# curate(book, candidate_md) -> None  (the agentic fold; injected so this stays testable without a model)
Curate = Callable[[ExperienceBook, str], None]


class MemoryConsolidator:
    def __init__(self, book: ExperienceBook, *, sink: Sink = null_sink) -> None:
        self.book = book
        self._store = book._store
        self._prefix = f"experiences/{book.schema}/candidates"
        self._sink = sink
        self._lock = threading.Lock()

    # ---- enqueue (instant, durable, non-blocking) -----------------------------------------
    def enqueue(self, candidate_md: str) -> str:
        key = f"{self._prefix}/{uuid.uuid4().hex[:12]}.md"
        self._store.write_bytes(key, (candidate_md or "").encode("utf-8"), "text/markdown")
        return key

    def pending(self) -> list[str]:
        return self._store.list_keys(self._prefix)

    # ---- drain (runs the curator; deletes each candidate after one attempt) ----------------
    def drain(self, curate: Curate) -> int:
        processed = 0
        while True:
            keys = self.pending()
            if not keys:
                return processed
            for key in keys:
                try:
                    md = self._store.read_bytes(key).decode("utf-8")
                    curate(self.book, md)
                except Exception as exc:  # noqa: BLE001 -- best-effort; never crash the background thread
                    self._sink("curate", "info", f"candidate failed: {type(exc).__name__}: {exc}")
                finally:
                    try:
                        self._store.delete(key)
                    except Exception:  # noqa: BLE001
                        pass
                processed += 1

    def drain_async(self, curate: Curate) -> threading.Thread | None:
        """Drain in a daemon thread. If a drain is already running, do nothing -- it will pick up any
        candidate enqueued in the meantime (drain loops until the queue is empty)."""
        if not self._lock.acquire(blocking=False):
            return None

        def _run() -> None:
            try:
                self.drain(curate)
            finally:
                self._lock.release()

        thread = threading.Thread(target=_run, name="diracdata-curator", daemon=True)
        thread.start()
        return thread
