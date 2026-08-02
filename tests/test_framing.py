"""Tooled intent framing + ask_user, driven by a scripted model + scripted asker (no infra).

Covers: the real ask_user tool (records the Q&A into WorkingMemory, headless-safe); framing binds an
unambiguous question with no question; and framing asks ONE question on a material ambiguity and
carries the answer into confirmed_intent.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.agents.framing import frame_intent  # noqa: E402
from diracdata.memory.working_memory import WorkingMemory  # noqa: E402
from diracdata.tools import build_tools  # noqa: E402


class _FakeWorkspace:
    semantic_layer = None  # no semantic layer -> v3 build_tools skips `define`, fine for these tests


class _FakeEngine:
    pass


class _ScriptedModel:
    def __init__(self, steps):
        self._steps = list(steps)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        step = self._steps.pop(0) if self._steps else {"content": "{}"}
        return AIMessage(content=step.get("content", ""), tool_calls=step.get("tool_calls", []))


def _tools(memory, asker=None):
    return build_tools(workspace=_FakeWorkspace(), engine=_FakeEngine(), result_store=None,
                       memory=memory, asker=asker)


class AskUserToolTests(unittest.TestCase):
    def test_records_answer_into_memory(self) -> None:
        mem = WorkingMemory(goal="g")
        ask = {t.name: t for t in _tools(mem, asker=lambda q: "the channel reading")}["ask_user"]
        out = str(ask.invoke({"question": "retail-stores: channel or exclusivity?"}))
        self.assertEqual(out, "the channel reading")
        self.assertEqual(mem.clarifications, [("retail-stores: channel or exclusivity?", "the channel reading")])

    def test_headless_is_safe(self) -> None:
        mem = WorkingMemory(goal="g")
        ask = {t.name: t for t in _tools(mem, asker=None)}["ask_user"]  # no user available
        out = str(ask.invoke({"question": "which reading?"}))
        self.assertIn("proceed", out.lower())
        self.assertEqual(mem.clarifications[0][0], "which reading?")   # still recorded that we asked


class FramingTests(unittest.TestCase):
    def test_unambiguous_binds_without_asking(self) -> None:
        mem = WorkingMemory(goal="total online revenue in 2001")
        model = _ScriptedModel([{"content": """```json
{"intent": "sum net_paid for online purchases in 2001",
 "concepts": [{"phrase": "online revenue", "meaning": "amount paid online",
               "binds_to": "online_purchases.net_paid"}],
 "assumptions": []}
```"""}])
        frame_intent(model=model, tools=_tools(mem), memory=mem, sink=lambda *a: None)
        self.assertEqual(mem.confirmed_intent["intent"], "sum net_paid for online purchases in 2001")
        self.assertEqual(mem.confirmed_intent["concepts"][0]["binds_to"], "online_purchases.net_paid")
        self.assertEqual(mem.clarifications, [])                       # nothing asked

    def test_material_ambiguity_asks_and_binds_the_answer(self) -> None:
        mem = WorkingMemory(goal="customers who shopped electronics in retail stores only")
        # turn 1: framer asks the user; turn 2: emits intent JSON reflecting the answer
        model = _ScriptedModel([
            {"tool_calls": [{"name": "ask_user", "id": "a1",
                             "args": {"question": "'retail stores only': bought in a store, or bought ONLY in stores (never online)?"}}]},
            {"content": """{"intent": "male AZ electronics buyers whose ONLY channel was in-store",
 "concepts": [{"phrase": "retail stores only", "meaning": "exclusively in-store, no online in 2001",
               "binds_to": "in store_purchases AND NOT in online_purchases"}],
 "assumptions": []}"""},
        ])
        frame_intent(model=model, tools=_tools(mem, asker=lambda q: "only in stores, never online"),
                     memory=mem, sink=lambda *a: None)
        self.assertEqual(len(mem.clarifications), 1)                   # asked exactly once
        self.assertIn("only in stores", mem.clarifications[0][1])
        self.assertIn("exclusively in-store", mem.confirmed_intent["concepts"][0]["meaning"])
        # the clarification is rendered as authoritative in working memory for the main loop
        self.assertIn("USER CLARIFICATIONS", mem.render())

    def test_assumptions_become_facts(self) -> None:
        mem = WorkingMemory(goal="revenue")
        model = _ScriptedModel([{"content":
            '{"intent":"x","concepts":[],"assumptions":["revenue = net_paid, not list price"]}'}])
        frame_intent(model=model, tools=_tools(mem), memory=mem, sink=lambda *a: None)
        self.assertTrue(any("net_paid" in f for f in mem.facts))       # assumption surfaced as a fact


if __name__ == "__main__":
    unittest.main()
