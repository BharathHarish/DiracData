"""The finish gate: plan-verified + faithfulness + independent verify, with a stub verifier (no
model). Plus the deterministic faithfulness parser, and the loop routing a finish through the gate.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.memory.working_memory import WorkingMemory  # noqa: E402
from diracdata.agents.verify import FinishGate, _unfaithful_figures, build_verify_payload  # noqa: E402
from diracdata.tools import build_control_tools  # noqa: E402


def _stub_verifier(ok=True, reason="ok", ambiguity=False):
    return lambda answer, memory: ({"ok": ok, "reason": reason, "ambiguity": ambiguity}, 0)


class VerifyPayloadTests(unittest.TestCase):
    """The independent verifier must SEE the user clarifications, or it re-litigates the raw
    (possibly corrected) question and the finish loop never converges."""

    def test_payload_carries_user_clarifications(self) -> None:
        m = WorkingMemory(goal="... shopped nothing in 2001 ...")
        m.record_clarification("did you mean nothing in 2001 or 2002?", "2002, not 2001")
        p = build_verify_payload("answer analysing 2002", m)
        self.assertEqual(p["user_clarifications"][0]["answer"], "2002, not 2001")
        self.assertEqual(p["user_clarifications"][0]["asked"], "did you mean nothing in 2001 or 2002?")

    def test_payload_marks_none_when_no_clarifications(self) -> None:
        p = build_verify_payload("a", WorkingMemory(goal="g"))
        self.assertEqual(p["user_clarifications"], "(none)")

    def test_payload_grounds_in_defined_terms_and_precedents(self) -> None:
        from types import SimpleNamespace

        class _WS:
            def definitions_index(self):
                return "DEFINED METRICS:\n  - online_revenue: SUM(online_purchases.net_paid)"

            def find_examples(self, query, limit=2):
                return [SimpleNamespace(question="online revenue 2001?", sql="SELECT SUM(net_paid) ...")]

        p = build_verify_payload("x", WorkingMemory(goal="online revenue in 2001"), workspace=_WS())
        self.assertIn("online_revenue", p["defined_terms"])
        self.assertEqual(p["reference_precedents"][0]["sql"], "SELECT SUM(net_paid) ...")


class FaithfulnessTests(unittest.TestCase):
    def test_flags_a_fabricated_aggregate(self) -> None:
        m = WorkingMemory(goal="how many online purchases in 2001?")
        m.seen_numbers = {49.0, 52.0}
        self.assertEqual(_unfaithful_figures("There were 147,024 online purchases.", m), ["147,024"])

    def test_passes_a_number_a_query_returned(self) -> None:
        m = WorkingMemory(goal="q")
        m.seen_numbers = {147024.0}
        self.assertEqual(_unfaithful_figures("There were 147,024 online purchases.", m), [])

    def test_ignores_years_small_counts_and_question_numbers(self) -> None:
        m = WorkingMemory(goal="top 3 categories in 2001")
        m.seen_numbers = {15287.0}
        # 2001 = year (skip), 3 = small count (skip), 15,287 = in seen -> nothing flagged
        self.assertEqual(_unfaithful_figures("In 2001 the top 3 led with 15,287 purchases.", m), [])

    def test_tolerates_rounding_and_magnitude_suffix(self) -> None:
        m = WorkingMemory(goal="revenue?")
        m.seen_numbers = {29547953.78}
        self.assertEqual(_unfaithful_figures("Revenue was $29,547,954 (about $29.5M).", m), [])


class FinishGateTests(unittest.TestCase):
    def test_rejects_when_plan_items_unverified(self) -> None:
        m = WorkingMemory(goal="g")
        m.plan.add("total")                         # pending
        gate = FinishGate(memory=m, verifier=_stub_verifier())
        out = gate.submit("done", [])
        self.assertIn("not verified", out)
        self.assertIsNone(gate.result)

    def test_rejects_unknown_cited_result(self) -> None:
        m = WorkingMemory(goal="g")
        gate = FinishGate(memory=m, verifier=_stub_verifier())
        self.assertIn("not found", gate.submit("answer", ["r9"]))

    def test_rejects_unfaithful_number(self) -> None:
        m = WorkingMemory(goal="g")
        m.seen_numbers = {52.0}
        gate = FinishGate(memory=m, verifier=_stub_verifier())
        out = gate.submit("The total is 999,999.", [])
        self.assertIn("don't match", out)

    def test_ambiguity_routes_to_ask_user(self) -> None:
        m = WorkingMemory(goal="g")
        gate = FinishGate(memory=m, verifier=_stub_verifier(ok=False, reason="channel or exclusivity?", ambiguity=True))
        out = gate.submit("52 customers", [])
        self.assertIn("ask_user", out)
        self.assertIsNone(gate.result)

    def test_accepts_when_all_gates_pass(self) -> None:
        m = WorkingMemory(goal="g")
        m.plan.add("total")
        m.plan.update("t1", status="verified", evidence={"result_id": "r1", "number": 52})
        m.results["r1"] = {"columns": ["n"], "row_count": 1, "sql": "..."}
        m.seen_numbers = {52.0}
        gate = FinishGate(memory=m, verifier=_stub_verifier(ok=True))
        self.assertEqual(gate.submit("The total is 52 customers.", ["r1"]), "ACCEPTED")
        self.assertEqual(gate.result["answer"], "The total is 52 customers.")


class ControlToolsTests(unittest.TestCase):
    def test_plan_update_add_and_verify_flow(self) -> None:
        m = WorkingMemory(goal="g")
        gate = FinishGate(memory=m, verifier=_stub_verifier())
        tools = {t.name: t for t in build_control_tools(memory=m, gate=gate)}
        tools["plan_update"].invoke({"action": "add", "goal": "count buyers"})
        self.assertEqual(m.plan.items[0].goal, "count buyers")
        tools["plan_update"].invoke({"action": "set", "id": "t1", "status": "verified",
                                     "result_id": "r1", "number": "52"})
        self.assertEqual(m.plan.items[0].status, "verified")
        self.assertEqual(m.plan.items[0].evidence["number"], "52")

    def test_finish_tool_routes_through_gate(self) -> None:
        m = WorkingMemory(goal="g")
        m.seen_numbers = {52.0}
        m.results["r1"] = {"columns": ["n"], "row_count": 1, "sql": "..."}
        gate = FinishGate(memory=m, verifier=_stub_verifier(ok=True))
        finish = {t.name: t for t in build_control_tools(memory=m, gate=gate)}["finish"]
        out = str(finish.invoke({"answer": "52 customers", "result_ids": ["r1"]}))
        self.assertEqual(out, "ACCEPTED")
        self.assertIsNotNone(gate.result)


if __name__ == "__main__":
    unittest.main()
