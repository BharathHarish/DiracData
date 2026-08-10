"""Conversation continuity: a follow-up ("break that down by state") is resolved into a standalone
intent at framing time, using the conversation memory (the running summary) it is handed. Scripted
model + fake tools, no infra.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.harness.framing import frame_intent  # noqa: E402
from diracdata.runtime.working_memory import WorkingMemory  # noqa: E402
from diracdata.tools import build_tools  # noqa: E402


class _FakeWorkspace:
    semantic_layer = None


class _FakeEngine:
    pass


class _ScriptedModel:
    def __init__(self, steps):
        self._steps = list(steps)
        self.saw = []                       # the messages it was shown (to assert the summary reached it)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        self.saw.append("\n".join(getattr(m, "content", "") for m in messages))
        step = self._steps.pop(0) if self._steps else {"content": "{}"}
        return AIMessage(content=step.get("content", ""), tool_calls=step.get("tool_calls", []))


def _tools(memory):
    return build_tools(workspace=_FakeWorkspace(), engine=_FakeEngine(), result_store=None, memory=memory)


class FollowUpFramingTests(unittest.TestCase):
    def test_followup_resolves_against_the_running_summary(self) -> None:
        mem = WorkingMemory(goal="break that down by state")
        summary = ("## KEY NUMBERS\n- 2001 online revenue = $339.5M [r1]\n"
                   "## ESTABLISHED FACTS\n- online_revenue = SUM(online_purchases.net_paid), filter calendar year")
        model = _ScriptedModel([{"content":
            '{"intent": "online revenue in 2001 split by state", '
            '"concepts": [{"phrase": "that", "meaning": "2001 online revenue", '
            '"binds_to": "SUM(net_paid) year=2001"}], "assumptions": []}'}])
        frame_intent(model=model, tools=_tools(mem), memory=mem, sink=lambda *a: None,
                     recent_turns=summary)
        # the conversation summary was actually put in front of the framer
        self.assertTrue(any("2001 online revenue = $339.5M" in s for s in model.saw))
        # and the follow-up was resolved into a standalone, carried-forward intent
        self.assertIn("2001", mem.confirmed_intent["intent"])
        self.assertIn("state", mem.confirmed_intent["intent"])


if __name__ == "__main__":
    unittest.main()
