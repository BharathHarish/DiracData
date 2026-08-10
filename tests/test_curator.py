"""M2: the Curator folds a finished turn into experiences.md via read/update tools -- agentically. A
scripted curator model drives the tool loop (no network): it can add a section, refine an existing one,
or make NO change when nothing is worth keeping."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from langchain_core.messages import AIMessageChunk  # noqa: E402

from diracdata.config import Config  # noqa: E402
from diracdata.memory.book import ExperienceBook  # noqa: E402
from diracdata.memory.curator import make_curator  # noqa: E402
from diracdata.utils.object_store import LocalObjectStore  # noqa: E402


class _ScriptedCurator:
    """Emits a queued sequence of steps. Each step is either tool calls or a final text.
    No .stream -> collect falls back to .invoke, but we implement .stream for realism."""
    def __init__(self, steps):
        self._steps = list(steps)

    def bind_tools(self, tools):
        return self

    def stream(self, messages):
        step = self._steps.pop(0) if self._steps else {"content": "done"}
        chunk = AIMessageChunk(content=step.get("content", ""),
                               tool_call_chunks=step.get("tool_call_chunks", []))
        yield chunk


def _tc(name, args_json, cid):
    return {"name": name, "args": args_json, "id": cid, "index": 0, "type": "tool_call_chunk"}


def _cfg():
    return Config(curator_max_steps=5)


class CuratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.book = ExperienceBook("retail_analytics", LocalObjectStore(Path(self._tmp.name)))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_learns_a_new_pattern(self) -> None:
        model = _ScriptedCurator([
            {"tool_call_chunks": [_tc("read_experiences", "{}", "c1")]},
            {"tool_call_chunks": [_tc("update_experiences",
                '{"section": "SQL PATTERNS", "body": "- cohort new-vs-returning: MIN(year) per client CTE"}',
                "c2")]},
            {"content": "kept the cohort pattern."},
        ])
        make_curator(model, _cfg())(self.book, "TURN: built a new-vs-returning cohort query")
        self.assertIn("cohort new-vs-returning", self.book.read())
        self.assertIn("SQL PATTERNS", self.book.sections())

    def test_learns_an_rca_lead(self) -> None:
        model = _ScriptedCurator([
            {"tool_call_chunks": [_tc("read_experiences", "{}", "c1")]},
            {"tool_call_chunks": [_tc("update_experiences",
                '{"section": "RCA LEADS", "body": "- online_revenue drop -> check acquisition_channel first"}',
                "c2")]},
            {"content": "done"},
        ])
        make_curator(model, _cfg())(self.book, "TURN: RCA on online_revenue drop, driver=channel")
        self.assertIn("acquisition_channel", self.book.sections()["RCA LEADS"])

    def test_nothing_worth_keeping_leaves_book_untouched(self) -> None:
        self.book.update_section("BINDINGS", "- online_revenue = SUM(net_paid)")
        before = self.book.read()
        model = _ScriptedCurator([
            {"tool_call_chunks": [_tc("read_experiences", "{}", "c1")]},
            {"content": "Trivial count; nothing new worth keeping."},   # no update call
        ])
        make_curator(model, _cfg())(self.book, "TURN: how many clients? -> 100000")
        self.assertEqual(self.book.read(), before)   # unchanged

    def test_refine_replaces_section_body(self) -> None:
        self.book.update_section("GOTCHAS", "- old note")
        model = _ScriptedCurator([
            {"tool_call_chunks": [_tc("read_experiences", "{}", "c1")]},
            {"tool_call_chunks": [_tc("update_experiences",
                '{"section": "GOTCHAS", "body": "- billing_client_ref ~0.02% NULL -> bucket unclassified"}',
                "c2")]},
            {"content": "refined"},
        ])
        make_curator(model, _cfg())(self.book, "TURN: found nulls in billing_client_ref")
        gotchas = self.book.sections()["GOTCHAS"]
        self.assertIn("unclassified", gotchas)
        self.assertNotIn("old note", gotchas)   # replaced, not appended


if __name__ == "__main__":
    unittest.main()
