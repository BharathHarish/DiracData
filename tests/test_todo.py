"""T0: the agentic TODO is a first-class part of the loop -- it renders into working memory, and the
agent maintains it across steps (add -> work -> verify) before finishing. The Plan structure + the
plan_update tool + the finish-gate dependency are covered in test_memory / test_verify; this pins the
LOOP-level behaviour (render prominence + cross-step maintenance)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.agents.loop import run_loop  # noqa: E402
from diracdata.agents.verify import FinishGate  # noqa: E402
from diracdata.runtime.working_memory import WorkingMemory  # noqa: E402
from diracdata.tools.control import build_control_tools  # noqa: E402


class _ScriptedModel:
    def __init__(self, steps):
        self._steps = list(steps)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        step = self._steps.pop(0) if self._steps else {"content": ""}
        return AIMessage(content=step.get("content", ""), tool_calls=step.get("tool_calls", []))


class TodoRenderTests(unittest.TestCase):
    def test_render_shows_plan_block_only_when_nonempty(self):
        m = WorkingMemory(goal="g")
        self.assertNotIn("PLAN:", m.render())            # empty TODO -> no block (simple path unchanged)
        m.plan.add("compute total")
        m.plan.add("compute by segment")
        r = m.render()
        self.assertIn("PLAN:", r)
        self.assertIn("compute total", r)
        self.assertIn("compute by segment", r)


class TodoLoopTests(unittest.TestCase):
    def test_agent_maintains_todo_across_steps_then_finishes(self):
        mem = WorkingMemory(goal="two-part question")
        mem.seen_numbers = {10.0}
        gate = FinishGate(memory=mem,
                          verifier=lambda answer, m: ({"ok": True, "reason": "", "ambiguity": False}, 0))
        tools = build_control_tools(memory=mem, gate=gate)
        model = _ScriptedModel([
            {"tool_calls": [{"name": "plan_update", "args": {"action": "add", "goal": "part A"}, "id": "c1"}]},
            {"tool_calls": [{"name": "plan_update",
                             "args": {"action": "set", "id": "t1", "status": "verified", "number": "10"},
                             "id": "c2"}]},
            {"content": "answer: 10 (from r1)"},
        ])
        out = run_loop(model=model, tools=tools, system_prompt="sys", memory=mem,
                       max_steps=8, finish_gate=gate)
        self.assertEqual(len(mem.plan.items), 1)              # the TODO was created + maintained...
        self.assertEqual(mem.plan.items[0].status, "verified")  # ...and worked to `verified`
        self.assertTrue(mem.plan.all_verified())
        self.assertIn("PLAN:", mem.render())                 # and rendered back into working memory
        self.assertIn("10", out["text"])                     # finish accepted (gate passed)


if __name__ == "__main__":
    unittest.main()
