"""The finish gate: plan-verified + cited-exist + independent verify (which now OWNS faithfulness,
judging the authoring artifacts -- no regex), a loop-breaker after N verifier rejections, and the
loop routing a finish through the gate. Stub verifier (no model).
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.config import Config  # noqa: E402
from diracdata.memory.working_memory import WorkingMemory  # noqa: E402
from diracdata.agents.verify import FinishGate, build_verify_payload, estate_dialects_note  # noqa: E402
from diracdata.tools import build_control_tools  # noqa: E402


class _FakeSources:
    def __init__(self, dialects: dict):
        self._d = dialects

    def names(self):
        return list(self._d)

    def get(self, name):
        from types import SimpleNamespace
        return SimpleNamespace(dialect=self._d[name])


class EstateDialectsNoteTests(unittest.TestCase):
    """The verifier must be told each source's dialect + the reconciler contract, or it flags valid
    cross-source SQL as 'won't execute / fabricated' (the multi-engine RCA spiral)."""

    def test_multi_source_lists_per_source_dialects(self) -> None:
        note = estate_dialects_note(_FakeSources({"fintech_lake": "duckdb", "orders_pg": "postgres"}), "BASE")
        self.assertTrue(note.startswith("BASE"))
        self.assertIn("orders_pg", note)
        self.assertIn("postgres", note)
        self.assertIn("duckdb", note)
        self.assertIn("reconciler", note)              # explains combine_results result_id views

    def test_single_source_and_none_are_unchanged(self) -> None:
        self.assertEqual(estate_dialects_note(_FakeSources({"only": "duckdb"}), "BASE"), "BASE")
        self.assertEqual(estate_dialects_note(None, "BASE"), "BASE")


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


class VerifyArtifactsTests(unittest.TestCase):
    """The verifier now judges the AUTHORING ARTIFACTS -- plan trail, facts/DQ ledger, queries, and a
    sample of returned values -- instead of a regex over prose. The payload must carry them."""

    def test_payload_carries_authoring_artifacts(self) -> None:
        m = WorkingMemory(goal="rca")
        m.plan.add("quantify revenue")
        m.add_fact("orders: 5000 rows, 0% nulls, no drift (data_health)")
        m.results["r1"] = {"columns": ["revenue"], "row_count": 1, "sql": "SELECT SUM(amount) ..."}
        m.seen_numbers = {-16054.0, 478848.0}
        p = build_verify_payload("Revenue declined by $16,054.", m)
        self.assertIn("quantify revenue", p["plan"])
        self.assertTrue(any("data_health" in f for f in p["authoring_notes"]))
        self.assertEqual(p["queries"][0]["result_id"], "r1")
        self.assertIn(478848.0, p["values_returned_by_queries"])   # returned values passed as evidence

    def test_values_sample_is_bounded(self) -> None:
        m = WorkingMemory(goal="g")
        m.seen_numbers = {float(i) for i in range(500)}
        p = build_verify_payload("a", m, config=Config())
        self.assertLessEqual(len(p["values_returned_by_queries"]), Config().verify_evidence_values)


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

    def test_no_regex_faithfulness_gate(self) -> None:
        # a big number the verifier is happy with is ACCEPTED -- provenance is the reviewer's call now,
        # not a deterministic string match (which used to dead-loop on false positives).
        m = WorkingMemory(goal="g")
        m.seen_numbers = {52.0}
        gate = FinishGate(memory=m, verifier=_stub_verifier(ok=True))
        self.assertEqual(gate.submit("The total is 999,999.", []), "ACCEPTED")

    def test_loop_breaker_accepts_with_caveat_after_max_rejects(self) -> None:
        m = WorkingMemory(goal="g")
        gate = FinishGate(memory=m, verifier=_stub_verifier(ok=False, reason="MECE concern"),
                          config=Config(verify_max_rejects=3))
        self.assertIn("REJECTED", gate.submit("ans", []))          # 1
        self.assertIn("REJECTED", gate.submit("ans", []))          # 2
        out = gate.submit("ans", [])                               # 3 -> loop-breaker
        self.assertEqual(out, "ACCEPTED")
        self.assertTrue(gate.result["verdict"].get("accepted_with_caveat"))
        self.assertIn("Reviewer note", gate.result["answer"])      # concern surfaced honestly
        self.assertIn("MECE concern", gate.result["answer"])

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
