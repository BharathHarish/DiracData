"""V5: triage (recall + one-bit classify) parsing + validation, and V5Agent wiring (subclass of v4,
progressive prompt = lean core, RCA skill body only for a metric-RCA). The live v4-vs-v5 A/B is
scripts/ab_v4_v5.py (UAT TO-V5-05)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.agents.triage import _parse, make_triage  # noqa: E402


class TriageParseTests(unittest.TestCase):
    """TO-V5-01: routing is agentic but validated -- bad/edge replies degrade to (analytics, cold)."""

    def test_rca_and_fast_with_precedent(self):
        t = _parse('{"task_type":"rca","lane":"fast","precedent_question":"why did rev drop",'
                   '"precedent_sql":"SELECT ...","reasoning":"metric decomposition"}')
        self.assertEqual(t["task_type"], "rca")
        self.assertEqual(t["lane"], "fast")
        self.assertEqual(t["precedent_sql"], "SELECT ...")

    def test_analytics_cold_default_on_junk(self):
        t = _parse("not json at all")
        self.assertEqual((t["task_type"], t["lane"]), ("analytics", "cold"))

    def test_fast_without_sql_downgrades_to_cold(self):
        t = _parse('{"task_type":"analytics","lane":"fast","precedent_sql":""}')
        self.assertEqual(t["lane"], "cold")               # a fast lane with no precedent is meaningless
        self.assertIsNone(t["precedent_sql"])

    def test_unknown_task_type_is_analytics(self):
        self.assertEqual(_parse('{"task_type":"cohort"}')["task_type"], "analytics")


class _ScriptedModel:
    def __init__(self, text):
        self._text = text

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        return AIMessage(content=self._text)


class TriageCallTests(unittest.TestCase):
    def test_make_triage_returns_validated_dict(self):
        from types import SimpleNamespace

        class _WS:
            def definitions_index(self): return "DEFINED METRICS:\n  - revenue"
            def find_examples(self, q, limit=3):
                return [SimpleNamespace(question="why did revenue fall", sql="SELECT SUM(amount) ...")]

        model = _ScriptedModel('{"task_type":"rca","lane":"cold","reasoning":"why a metric moved"}')
        tri = make_triage(model)("why did revenue fall in Q2?", _WS())
        self.assertEqual(tri["task_type"], "rca")
        self.assertIn("tokens", tri)


class V5WiringTests(unittest.TestCase):
    def test_v5_is_v4_subclass_and_prompts_load(self):
        from diracdata.agent import V4Agent
        from diracdata.agent_v5 import V5Agent, _CORE, _RCA_SKILL
        self.assertTrue(issubclass(V5Agent, V4Agent))            # reuses all of v4, overrides run()
        self.assertIn("REPORT NUMBERS FAITHFULLY", _CORE)         # lean core carries the invariants
        self.assertIn("DATA SANITY FIRST", _RCA_SKILL)            # the skill opens with DQ
        self.assertIn("ATTRIBUTE THE CHANGE", _RCA_SKILL)         # ...and owns attribution
        self.assertNotIn("METRIC-RCA SKILL", _CORE)               # the skill is NOT in the core (progressive)


if __name__ == "__main__":
    unittest.main()
