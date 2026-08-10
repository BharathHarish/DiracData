"""M4: the curated experiences.md is READ back and injected into the turn (framing + analyst system
prompt) so learned knowledge is reused -- gated on agentic_memory_enabled + a book. Deterministic:
we inspect what gets injected, no model call."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.agent import Agent  # noqa: E402
from diracdata.config import Config  # noqa: E402
from diracdata.memory.book import ExperienceBook  # noqa: E402
from diracdata.utils.object_store import LocalObjectStore  # noqa: E402


class _M:
    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        return AIMessage(content="")


def _agent(enabled, book):
    return Agent(model=_M(), workspace=None, engine=None, result_store=None,
                   config=Config(agentic_memory_enabled=enabled), experience_book=book,
                   subagents=False, frame=False)


class ReadbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.book = ExperienceBook("retail_analytics", LocalObjectStore(Path(self._tmp.name)))
        self.book.update_section("SQL PATTERNS", "- cohort new-vs-returning: MIN(year) per client CTE")
        self.book.update_section("GOTCHAS", "- billing_client_ref has NULLs -> COALESCE")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_enabled_reads_the_book(self) -> None:
        learned = _agent(True, self.book)._learned_context()
        self.assertIn("cohort new-vs-returning", learned)
        self.assertIn("billing_client_ref", learned)

    def test_disabled_injects_nothing(self) -> None:
        self.assertEqual(_agent(False, self.book)._learned_context(), "")

    def test_no_book_injects_nothing(self) -> None:
        self.assertEqual(_agent(True, None)._learned_context(), "")

    def test_framing_task_carries_learned_knowledge(self) -> None:
        # frame_intent injects `learned` into the task shown to the framing model
        from diracdata.agents.framing import frame_intent
        from diracdata.runtime.working_memory import WorkingMemory

        seen = []

        class _CaptureModel:
            def bind_tools(self, tools):
                return self

            def invoke(self, messages):
                from langchain_core.messages import AIMessage
                seen.append("\n".join(getattr(m, "content", "") for m in messages))
                return AIMessage(content='{"intent": "x", "concepts": []}')

        frame_intent(model=_CaptureModel(), tools=[], memory=WorkingMemory(goal="q"),
                     sink=lambda *a: None, learned="## SQL PATTERNS\n- reuse me")
        self.assertTrue(any("reuse me" in s for s in seen))   # the book reached the framer


if __name__ == "__main__":
    unittest.main()
