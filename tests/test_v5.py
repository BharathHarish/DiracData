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
        self.last_messages = None

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        self.last_messages = messages
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

    def test_recall_includes_learned_experience(self):
        # TO-V5-15: the experience book (learned SQL PATTERNS) is a FIRST-CLASS recall source in triage,
        # so a learned pattern -- not just gold -- can drive the fast lane. Prior bug: it was invisible.
        class _WS:
            def definitions_index(self): return ""
            def find_examples(self, q, limit=3): return []

        model = _ScriptedModel('{"task_type":"analytics","lane":"fast","precedent_question":"learned pattern",'
                               '"precedent_sql":"SELECT ... GROUP BY income_range","reasoning":"matches a learned pattern"}')
        tri = make_triage(model)("revenue decline by income band", _WS(),
                                 learned="## SQL PATTERNS\n- Revenue decline by segment: SELECT ... GROUP BY income_range")
        payload = str(model.last_messages[-1].content)
        self.assertIn("learned_experience", payload)              # learned recall reaches the triage model
        self.assertIn("Revenue decline by segment", payload)
        self.assertEqual(tri["lane"], "fast")                     # a learned pattern can seed the fast lane
        self.assertIn("income_range", tri["precedent_sql"])


class V5WiringTests(unittest.TestCase):
    def test_v5_is_v4_subclass_and_prompts_load(self):
        from diracdata.agent import V4Agent
        from diracdata.agent_v5 import V5Agent, _CORE, _RCA_SKILL
        self.assertTrue(issubclass(V5Agent, V4Agent))            # reuses all of v4, overrides run()
        self.assertIn("REPORT NUMBERS FAITHFULLY", _CORE)         # lean core carries the invariants
        self.assertIn("DATA SANITY FIRST", _RCA_SKILL)            # the skill opens with DQ
        self.assertIn("ATTRIBUTE THE CHANGE", _RCA_SKILL)         # ...and owns attribution
        self.assertNotIn("METRIC-RCA SKILL", _CORE)               # the skill is NOT in the core (progressive)


class DQGateWiringTests(unittest.TestCase):
    """TO-V5-06: data-sanity is its OWN focused agentic gate (sanity_gate.md), NOT overloaded into the
    derivation reviewer -- so a small model gives each the full attention. verify.md is de-loaded and
    defers sanity; the payload both gates judge carries the DQ ledger + cited queries. The LLM's actual
    gating is shown live in scripts/e2e_v5.py (probabilistic)."""

    def test_sanity_gate_is_separate_and_verify_is_deloaded(self):
        from diracdata.prompts import load_prompt
        sanity, verify = load_prompt("sanity_gate"), load_prompt("verify")
        self.assertIn("DATA-SANITY reviewer", sanity)                 # the focused gate owns it now
        self.assertIn("NEVER PROBED", sanity)                        # absence of a probe is a defect
        self.assertNotIn("DATA-SANITY IS A GATE", verify)            # de-loaded: verify no longer carries it
        self.assertIn("SEPARATE focused gate", verify)               # verify explicitly defers sanity

    def test_payload_carries_dq_evidence_and_queries(self):
        from diracdata.agents.verify import build_verify_payload
        from diracdata.memory.working_memory import WorkingMemory
        m = WorkingMemory(goal="why did online revenue fall?")
        m.facts = "data_health(online_purchases): billing_household_profile_ref 8% NULL -> INNER join drops rows"
        m.results["r1"] = {"sql": "SELECT ...", "row_count": 20}
        p = build_verify_payload("It fell 4%.", m)
        self.assertIn("data_health", p["authoring_notes"])           # DQ finding reaches the reviewer
        self.assertEqual(p["queries"][0]["result_id"], "r1")         # cited queries reach the reviewer

    def test_gate_chain_runs_sanity_first_and_fails_fast(self):
        """The chain runs sanity BEFORE derivation, first reject wins, later gates are skipped."""
        from diracdata.agents.verify import FinishGate
        from diracdata.memory.working_memory import WorkingMemory
        calls = []

        def stub(name, ok, reason=""):
            def v(_answer, _memory):
                calls.append(name)
                return ({"ok": ok, "reason": reason, "ambiguity": False}, 1)
            return v

        m = WorkingMemory(goal="q")
        gate = FinishGate(memory=m, verifier=stub("derivation", True),
                          sanity_verifier=stub("sanity", False, reason="probe online_purchases join"))
        out = gate.submit("answer", [])
        self.assertTrue(out.startswith("REJECTED [sanity]"))         # sanity gate names itself
        self.assertEqual(calls, ["sanity"])                          # fail-fast: derivation never ran

    def test_gate_chain_both_pass_runs_both_in_order(self):
        from diracdata.agents.verify import FinishGate
        from diracdata.memory.working_memory import WorkingMemory
        calls = []

        def stub(name):
            def v(_a, _m):
                calls.append(name)
                return ({"ok": True, "reason": "", "ambiguity": False}, 1)
            return v

        m = WorkingMemory(goal="q")
        gate = FinishGate(memory=m, verifier=stub("derivation"), sanity_verifier=stub("sanity"))
        self.assertEqual(gate.submit("answer", []), "ACCEPTED")
        self.assertEqual(calls, ["sanity", "derivation"])            # sanity precedes derivation


class ExperiencesDefaultTests(unittest.TestCase):
    """TO-V5-07: agentic memory (schema experiences.md) is ON by default; ENV can still disable it."""

    def test_agentic_memory_on_by_default(self):
        from diracdata.config import Config
        self.assertTrue(Config().agentic_memory_enabled)


class ModelGardenTests(unittest.TestCase):
    """TO-V5-10: the garden is exactly 3 tiers and the router (which picks among them) is ON by default,
    so V5 auto-routes Haiku=complex / Mini=medium / Nano=simple. Routing behaviour itself is covered by
    test_router / test_router_wiring; V5.run reuses those same helpers (_route/_run_analyst)."""

    def test_garden_is_exactly_three_tiers(self):
        from diracdata.utils.model_factory import BUILT_IN_MODEL_PROFILES as P
        self.assertEqual(set(P), {"anthropic_haiku_45", "openai_gpt_5_4_mini", "openai_gpt_5_4_nano"})
        self.assertEqual(P["anthropic_haiku_45"].capability, "strong")    # top of THIS garden -> complex
        self.assertEqual(P["openai_gpt_5_4_mini"].capability, "standard")  # medium
        self.assertEqual(P["openai_gpt_5_4_nano"].capability, "basic")     # simple

    def test_router_on_by_default(self):
        from diracdata.config import Config
        self.assertTrue(Config().router_enabled)

    def test_v5_run_uses_the_router(self):
        import inspect
        from diracdata.agent_v5 import V5Agent
        src = inspect.getsource(V5Agent.run)
        self.assertIn("self._route(", src)          # V5 routes the model
        self.assertIn("escalations", src)           # ...and escalates on non-convergence

    def test_escalation_continues_not_restarts(self):
        # TO-V5-16: on escalation the stronger model is told to BUILD ON prior results (reuse result_ids),
        # not re-explore from scratch -- the re-derivation that burned ~half the tokens on the RCA run.
        import inspect
        from diracdata.agent_v5 import V5Agent
        src = inspect.getsource(V5Agent.run)
        self.assertIn("CONTINUE", src)
        self.assertIn("REUSE those result_ids", src)

    def test_router_sees_triage_verdict(self):
        # TO-V5-18: the router must SEE triage's verdict -- task_type AND a fast-lane precedent -- else it
        # re-labels a precedented RCA "cold" and sends it to the top model (the bug the user caught).
        import inspect
        from diracdata.agent_v5 import V5Agent
        from diracdata.routing.router import RouteSignals
        self.assertIn("task_type", RouteSignals.__dataclass_fields__)   # signals carry triage's task_type
        src = inspect.getsource(V5Agent.run)
        self.assertIn('task_type=tri["task_type"]', src)               # V5 threads triage into the signals
        self.assertIn('exact_match=base_signals.exact_match or tri["lane"] == "fast"', src)

    def test_router_payload_carries_task_type_and_precedent(self):
        from diracdata.routing.router import make_router, RouteSignals
        from diracdata.config import Config
        cap = _ScriptedModel('{"authoring_profile":"openai_gpt_5_4_mini","max_tokens":4000,'
                             '"temperature":0.0,"max_steps":16,"allow_shortcut":true}')
        route = make_router(cap, Config(router_enabled=True))
        route("why did revenue fall", RouteSignals(exact_match=True, task_type="rca"))
        payload = str(cap.last_messages[-1].content)
        self.assertIn('"task_type": "rca"', payload)                   # router SEES triage's task_type
        self.assertIn('"precedent_exists": true', payload)             # ...and that a precedent exists


if __name__ == "__main__":
    unittest.main()
