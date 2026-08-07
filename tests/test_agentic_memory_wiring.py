"""M2 wiring: with agentic_memory_enabled + an ExperienceBook, the agent's _record enqueues the turn
and kicks the async drain; with the flag off there's no consolidator. (Queue/curator internals are
covered by test_consolidator / test_curator; here we pin the agent hook deterministically with a spy.)
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from langchain_core.messages import AIMessage  # noqa: E402

from diracdata.agent import Agent  # noqa: E402
from diracdata.config import Config  # noqa: E402
from diracdata.experiences.book import ExperienceBook  # noqa: E402
from diracdata.memory.conversation import Conversation  # noqa: E402
from diracdata.utils.object_store import LocalObjectStore  # noqa: E402


class _SummaryModel:
    """Serves the summarizer call (.invoke -> a summary)."""
    def invoke(self, messages):
        return AIMessage(content="## KEY NUMBERS\n- 369")


class _SpyConsolidator:
    def __init__(self):
        self.enqueued = []
        self.drained = 0

    def enqueue(self, md):
        self.enqueued.append(md)

    def drain_async(self, curate):
        self.drained += 1
        return None


def _agent(enabled, book):
    return Agent(model=_SummaryModel(), workspace=None, engine=None, result_store=None,
                   config=Config(agentic_memory_enabled=enabled), experience_book=book,
                   subagents=False, frame=False)


class AgenticMemoryWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = LocalObjectStore(Path(self._tmp.name))
        self.book = ExperienceBook("retail_analytics", self.store)
        self.conv = Conversation("c1", store=self.store)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_enabled_enqueues_and_drains(self) -> None:
        agent = _agent(True, self.book)
        self.assertIsNotNone(agent._consolidator)
        spy = _SpyConsolidator()
        agent._consolidator = spy                      # observe the hook without spawning a thread
        agent._record(self.conv, "count clients", [{"phase": "analyst", "tool": "run_sql",
                                                     "args": {}, "result": "369"}], "There are 369.")
        self.assertEqual(len(spy.enqueued), 1)         # the turn was enqueued
        self.assertIn("count clients", spy.enqueued[0])
        self.assertEqual(spy.drained, 1)               # background drain kicked

    def test_disabled_has_no_consolidator(self) -> None:
        agent = _agent(False, self.book)
        self.assertIsNone(agent._consolidator)
        # _record still works (summary only), no crash
        agent._record(self.conv, "q", [], "a")
        self.assertIn("369", self.conv.summary())

    def test_no_book_means_no_consolidator_even_if_enabled(self) -> None:
        agent = _agent(True, None)
        self.assertIsNone(agent._consolidator)


if __name__ == "__main__":
    unittest.main()
