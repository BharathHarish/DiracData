"""Durable conversation memory: the transcript is the lossless record (appended every turn with the
full tool trace), the summary is the compact running memory (regenerated agentically every turn and
fed into the next turn). Here we pin the persistence + rendering + the summarizer, with a scripted
model (no tokens); the cross-turn resolution is exercised live in the e2e run.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.config import Config  # noqa: E402
from diracdata.memory.conversation import Conversation  # noqa: E402
from diracdata.agents.summarizer import make_summarizer  # noqa: E402
from diracdata.utils.object_store import LocalObjectStore  # noqa: E402


def _events():
    return [
        {"phase": "framing", "tool": "define", "args": {"term": "online revenue"},
         "result": "online_revenue = SUM(online_purchases.net_paid)"},
        {"phase": "analyst", "tool": "run_sql", "args": {"sql": "SELECT SUM(net_paid) FROM online_purchases"},
         "result": "r1: 1 row -> 49712345.67"},
    ]


class ConversationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.conv = Conversation("c1", root=Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_append_turn_writes_full_trace_and_counts_turns(self) -> None:
        self.assertEqual(self.conv.turns, 0)
        self.conv.append_turn(question="2001 online revenue?", events=_events(), answer="$49.71M")
        t = self.conv.read_transcript()
        self.assertIn("## Turn 1", t)
        self.assertIn("**Question:** 2001 online revenue?", t)
        self.assertIn("### Framing", t)
        self.assertIn("### Analyst", t)
        self.assertIn("define(", t)            # a framing tool call is recorded
        self.assertIn("run_sql(", t)           # an analyst tool call is recorded
        self.assertIn("49712345.67", t)        # the raw result is recorded (minute detail)
        self.assertIn("**Answer:** $49.71M", t)
        self.assertEqual(self.conv.turns, 1)

    def test_transcript_grows_every_turn(self) -> None:
        self.conv.append_turn(question="q1", events=_events(), answer="a1")
        first = len(self.conv.read_transcript())
        self.conv.append_turn(question="q2", events=_events(), answer="a2")
        self.assertGreater(len(self.conv.read_transcript()), first)
        self.assertEqual(self.conv.turns, 2)
        self.assertIn("## Turn 2", self.conv.read_transcript())

    def test_read_transcript_tail(self) -> None:
        self.conv.append_turn(question="q1", events=_events(), answer="a1")
        self.conv.append_turn(question="q2", events=_events(), answer="a2")
        tail = self.conv.read_transcript(tail_chars=40)
        self.assertLessEqual(len(tail), 40)
        self.assertTrue(self.conv.read_transcript().endswith(tail))

    def test_long_result_is_clipped_in_transcript(self) -> None:
        cfg = Config()  # transcript_result_cap default
        big = "X" * (cfg.transcript_result_cap + 500)
        self.conv.append_turn(question="q", answer="a",
                              events=[{"phase": "analyst", "tool": "run_sql", "args": {}, "result": big}])
        t = self.conv.read_transcript()
        self.assertIn("chars]", t)             # a clip marker
        self.assertNotIn("X" * (cfg.transcript_result_cap + 1), t)

    def test_summary_roundtrip_and_persists_to_disk(self) -> None:
        self.assertEqual(self.conv.summary(), "")
        self.conv.set_summary("online revenue bound to SUM(net_paid); 2001 = $49.71M [r1]")
        self.assertIn("49.71M", self.conv.summary())
        # a fresh handle to the same id reads it back (cross-session continuity)
        again = Conversation("c1", root=Path(self._tmp.name))
        self.assertIn("49.71M", again.summary())
        self.assertEqual(again.turns, 0)


class ObjectStoreBackedTests(unittest.TestCase):
    """The durable path: transcript + summary live in the object store (under conversations/<id>/),
    right next to query results and the fabric -- portable across machines/sessions."""

    def test_persists_through_the_object_store_and_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalObjectStore(Path(tmp))
            conv = Conversation("c9", store=store)
            conv.append_turn(question="2001 online revenue?", events=_events(), answer="$49.71M")
            conv.set_summary("2001 online revenue = $49.71M [r1]")
            # written under the shared conventions prefix, not to a local conversations dir
            self.assertTrue(store.exists("conversations/c9/transcript.md"))
            self.assertTrue(store.exists("conversations/c9/summary.md"))
            self.assertIn("(object store)", conv.location)
            # a fresh handle backed by the SAME store reads it back (cross-session continuity)
            again = Conversation("c9", store=store)
            self.assertEqual(again.turns, 1)
            self.assertIn("49.71M", again.summary())
            self.assertIn("run_sql(", again.read_transcript())

    def test_transcript_appends_across_turns_in_the_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conv = Conversation("c10", store=LocalObjectStore(Path(tmp)))
            conv.append_turn(question="q1", events=_events(), answer="a1")
            first = len(conv.read_transcript())
            conv.append_turn(question="q2", events=_events(), answer="a2")
            self.assertGreater(len(conv.read_transcript()), first)   # object stores have no append -> read+write
            self.assertEqual(conv.turns, 2)


class _ScriptedModel:
    """No .stream -> stream_and_collect falls back to .invoke."""

    def __init__(self, text):
        self._text = text

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        # echo the human content so the test can assert the turn was actually fed in
        self._last = messages
        return AIMessage(content=self._text)


class SummarizerTests(unittest.TestCase):
    def test_folds_turn_into_a_new_summary(self) -> None:
        model = _ScriptedModel("online_revenue = SUM(net_paid); 2001 = $49.71M [r1]")
        summarize = make_summarizer(model)
        new_summary, _tok = summarize("", "## Turn 1\nrun_sql -> 49712345.67")
        self.assertIn("49.71M", new_summary)

    def test_empty_model_output_keeps_previous_summary(self) -> None:
        summarize = make_summarizer(_ScriptedModel(""))
        prev = "established: online_revenue = SUM(net_paid)"
        new_summary, _tok = summarize(prev, "## Turn 2\n(nothing new)")
        self.assertEqual(new_summary, prev)   # never lose memory on an empty regeneration


class AgentRecordTests(unittest.TestCase):
    """The Agent's post-turn step: append the trace to transcript.md AND regenerate summary.md."""

    def test_record_writes_both_files_from_one_turn(self) -> None:
        from diracdata.agent import Agent

        with tempfile.TemporaryDirectory() as tmp:
            conv = Conversation("c1", root=Path(tmp))
            agent = Agent(model=_ScriptedModel("running: 2001 online revenue = $49.71M [r1]"),
                            workspace=None, engine=None, result_store=None, frame=False, subagents=False)
            agent._record(conv, "2001 online revenue?", _events(), "$49.71M")
            self.assertIn("## Turn 1", conv.read_transcript())   # lossless trace
            self.assertIn("run_sql(", conv.read_transcript())
            self.assertIn("49.71M", conv.summary())              # regenerated running memory
            self.assertEqual(conv.turns, 1)


if __name__ == "__main__":
    unittest.main()
