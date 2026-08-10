"""M1: the async candidate queue + background drain. Enqueue is instant + durable; drain runs the
(injected) curator and removes each candidate; the daemon thread doesn't block; failures don't clog."""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.memory.book import ExperienceBook  # noqa: E402
from diracdata.memory.consolidator import MemoryConsolidator  # noqa: E402
from diracdata.utils.object_store import LocalObjectStore  # noqa: E402


class ConsolidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = LocalObjectStore(Path(self._tmp.name))
        self.book = ExperienceBook("retail_analytics", self.store)
        self.con = MemoryConsolidator(self.book)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_enqueue_is_durable_and_listed(self) -> None:
        self.con.enqueue("## Turn\nrun_sql -> 369")
        self.assertEqual(len(self.con.pending()), 1)
        # a fresh consolidator on the same store sees it (survives process restart)
        again = MemoryConsolidator(ExperienceBook("retail_analytics", self.store))
        self.assertEqual(len(again.pending()), 1)

    def test_drain_runs_curator_and_clears_queue(self) -> None:
        seen = []
        self.con.enqueue("cand-1")
        self.con.enqueue("cand-2")
        n = self.con.drain(lambda book, md: seen.append(md))
        self.assertEqual(n, 2)
        self.assertEqual(sorted(seen), ["cand-1", "cand-2"])
        self.assertEqual(self.con.pending(), [])          # queue emptied

    def test_failing_curator_does_not_clog_queue(self) -> None:
        self.con.enqueue("poison")
        def boom(book, md):
            raise RuntimeError("curator error")
        n = self.con.drain(boom)
        self.assertEqual(n, 1)
        self.assertEqual(self.con.pending(), [])          # candidate removed despite failure

    def test_drain_async_is_non_blocking_and_completes(self) -> None:
        gate = threading.Event()
        done = []
        def slow(book, md):
            gate.wait(2.0)
            done.append(md)
        self.con.enqueue("c")
        t = self.con.drain_async(slow)
        self.assertIsNotNone(t)
        self.assertTrue(t.is_alive())                     # returned immediately, work still running
        gate.set()
        t.join(3.0)
        self.assertEqual(done, ["c"])
        self.assertEqual(self.con.pending(), [])

    def test_second_drain_skips_while_one_is_running(self) -> None:
        gate = threading.Event()
        def slow(book, md):
            gate.wait(2.0)
        self.con.enqueue("c1")
        t1 = self.con.drain_async(slow)
        time.sleep(0.05)
        t2 = self.con.drain_async(slow)                   # lock held -> no second thread
        self.assertIsNone(t2)
        gate.set()
        t1.join(3.0)


if __name__ == "__main__":
    unittest.main()
