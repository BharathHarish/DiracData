"""The investigative loop: endurance (keeps going until it decides done), and the
deterministic reconciliation test (parts must sum to the whole).

Reflection is driven by a scripted fake model so the loop's control flow is pinned without
spending tokens; the reconciliation math is exercised for real.
"""

import unittest

from diracdata_v3.investigator import Investigator


class _FakeAnswer:
    def __init__(self, rows, route="cold_start"):
        self.result = {"rows": rows} if rows is not None else None
        self.route = route
        self.answered = rows is not None
        self.final_sql = "SELECT ..."
        self.tokens = 10
        self.clarify = ""
        self.stage_tokens = {}


class _ScriptedAgent:
    """Returns a queued answer per sub-question asked; records the confirmed_intent it was given."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.asked = []
        self.confirmed = []

    def answer(self, question, confirmed_intent=""):
        self.asked.append(question)
        self.confirmed.append(confirmed_intent)
        return self._answers.pop(0) if self._answers else _FakeAnswer([[0]])


class _ScriptedModel:
    """Emits queued decide decisions (JSON strings). The intent-framing call (first thing
    investigate() does) is satisfied transparently by `frame` and does NOT consume the queue,
    so decision scripts read as just the decide loop. No .stream -> invoke fallback."""

    def __init__(self, decisions, frame=None):
        self._decisions = list(decisions)
        self._frame = frame or '{"intent":"test","concepts":[],"clarification":""}'

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        system = messages[0].content if messages else ""
        if "FRAME THE INTENT" in system:
            return AIMessage(content=self._frame)
        return AIMessage(content=self._decisions.pop(0) if self._decisions else '{"action":"finish","final_answer":"done"}')


class ReconciliationTests(unittest.TestCase):
    def test_parts_that_sum_to_the_whole_reconcile(self) -> None:
        inv = Investigator(agent=None, model=None)
        findings = [{"kind": "finding", "rows": [[100.0]]},                 # total
                    {"kind": "finding", "rows": [["A", 60.0], ["B", 40.0]]}]  # parts -> 100
        rec = inv._reconcile(findings, 0, 1)
        self.assertTrue(rec["reconciles"])

    def test_missing_part_does_not_reconcile(self) -> None:
        inv = Investigator(agent=None, model=None)
        findings = [{"kind": "finding", "rows": [[100.0]]},
                    {"kind": "finding", "rows": [["A", 60.0], ["B", 25.0]]}]  # sums to 85, residual 15
        rec = inv._reconcile(findings, 0, 1)
        self.assertFalse(rec["reconciles"])
        self.assertEqual(rec["residual"], 15.0)

    def test_explicit_numbers_reconcile_regardless_of_finding_shape(self) -> None:
        # the orchestrator reads the numbers out of the findings and passes them directly;
        # the deterministic check just does the arithmetic (the fix for reconcile "unknown").
        inv = Investigator(agent=None, model=None)
        rec = inv._reconcile([], None, None, total_value=-300, part_values=[-1191, 891])
        self.assertTrue(rec["reconciles"])
        self.assertEqual(rec["residual"], 0.0)

    def test_explicit_numbers_flag_a_missing_part(self) -> None:
        inv = Investigator(agent=None, model=None)
        rec = inv._reconcile([], None, None, total_value=-300, part_values=[-1191])  # returning part omitted
        self.assertFalse(rec["reconciles"])
        self.assertEqual(rec["residual"], -300 - (-1191))  # +891 unexplained

    def test_before_after_total_reconciles_against_part_changes(self) -> None:
        # total given as a 2-row before/after series (11552 -> 11252 = -300); parts are per-cohort
        # CHANGES whose last column sums to -300. This is the real RCA shape the author returns.
        inv = Investigator(agent=None, model=None)
        findings = [{"kind": "finding", "rows": [[2001, 11552], [2002, 11252]]},          # total change -300
                    {"kind": "finding", "rows": [["new", -75], ["returning", -225]]}]      # parts sum -300
        rec = inv._reconcile(findings, 0, 1)
        self.assertTrue(rec["reconciles"])

    def test_before_after_total_flags_a_missing_driver_as_residual(self) -> None:
        # the actual wrinkle: parts (+801) do NOT explain the -300 total -> residual surfaces the gap.
        inv = Investigator(agent=None, model=None)
        findings = [{"kind": "finding", "rows": [[2001, 11552], [2002, 11252]]},          # total change -300
                    {"kind": "finding", "rows": [["new", -75], ["returning", 876]]}]       # parts sum +801
        rec = inv._reconcile(findings, 0, 1)
        self.assertFalse(rec["reconciles"])
        self.assertEqual(rec["residual"], -1101.0)  # -300 - 801; the missing (churned/uncohorted) bucket

    def test_string_numbers_are_reconciled(self) -> None:
        inv = Investigator(agent=None, model=None)
        findings = [{"kind": "finding", "rows": [["150.50"]]},
                    {"kind": "finding", "rows": [["X", "100.50"], ["Y", "50.00"]]}]
        self.assertTrue(inv._reconcile(findings, 0, 1)["reconciles"])


class OrchestratorTests(unittest.TestCase):
    def test_simple_question_authors_then_finishes(self) -> None:
        agent = _ScriptedAgent([_FakeAnswer([[42]])])
        model = _ScriptedModel([
            '{"action":"author","question":"what is the value?"}',
            '{"action":"finish","final_answer":"The value is 42."}',
        ])
        inv = Investigator(agent=agent, model=model).investigate("what is the value?")
        self.assertTrue(inv.converged)
        self.assertIn("42", inv.answer)
        self.assertEqual(len(agent.asked), 1)

    def test_rca_decomposes_reconciles_then_finishes(self) -> None:
        agent = _ScriptedAgent([
            _FakeAnswer([[100.0]]),                              # total change
            _FakeAnswer([["West", 70.0], ["East", 30.0]]),      # breakdown by region
        ])
        model = _ScriptedModel([
            '{"action":"author","question":"what was the total change as one number?"}',
            '{"action":"author","question":"break the change down by region"}',
            '{"action":"reconcile","total_finding":0,"parts_finding":1}',
            '{"action":"finish","final_answer":"Change of 100 driven mostly by West (70)."}',
        ])
        result = Investigator(agent=agent, model=model).investigate("why did it change by 100?")
        self.assertTrue(result.converged)
        self.assertGreaterEqual(result.steps, 3)
        self.assertTrue(any(f.get("kind") == "reconcile" and f.get("reconciles") for f in result.findings))
        self.assertIn("West", result.answer)

    def test_framing_clarifies_before_any_sql(self) -> None:
        # a genuinely ambiguous concept -> the intent-framing gate asks the user BEFORE any SQL,
        # so the analyst is never even called.
        agent = _ScriptedAgent([_FakeAnswer([[1]])])
        model = _ScriptedModel(['{"action":"finish","final_answer":"x"}'],
                               frame='{"intent":"?","concepts":[],"clarification":"Do you mean where they live now, or at purchase time?"}')
        inv = Investigator(agent=agent, model=model).investigate("customers from Arizona")
        self.assertIn("purchase time", inv.needs_clarification)
        self.assertEqual(agent.asked, [])  # never reached SQL

    def test_confirmed_intent_carries_bindings_and_clarification_to_the_agent(self) -> None:
        # the framed bindings AND any user clarification reach the agent as an authoritative field
        # (which the agent hands to BOTH the analyst and the steward).
        agent = _ScriptedAgent([_FakeAnswer([[42]])])
        model = _ScriptedModel(
            ['{"action":"author","question":"count spend"}', '{"action":"finish","final_answer":"42"}'],
            frame='{"intent":"total spend","concepts":[{"phrase":"spend","meaning":"amount paid","binds_to":"online_revenue (net_paid)"}],"clarification":""}')
        Investigator(agent=agent, model=model).investigate(
            "total spend", clarifications=[("By spend do you mean paid or list price?", "amount paid")])
        ci = agent.confirmed[0]
        self.assertIn("net_paid", ci)                 # framed binding
        self.assertIn("amount paid", ci)              # the user's clarification, verbatim
        self.assertEqual(agent.asked[0], "count spend")  # sub-question stays clean; intent rides the field

    def test_two_distinct_reconciles_both_run_then_finish(self) -> None:
        # a multiplicative reconcile AND an additive sub-split are DISTINCT -- both must fire and the
        # loop must still reach finish (the old anti-spin broke on the 2nd, reporting not-converged).
        agent = _ScriptedAgent([_FakeAnswer([[100.0]]), _FakeAnswer([["a", 60.0], ["b", 40.0]])])
        model = _ScriptedModel([
            '{"action":"author","question":"total?"}',
            '{"action":"author","question":"parts?"}',
            '{"action":"reconcile","total":100,"parts":[60,40]}',
            '{"action":"reconcile","total":60,"parts":[35,25]}',
            '{"action":"finish","final_answer":"done, both reconcile"}',
        ])
        inv = Investigator(agent=agent, model=model).investigate("decompose")
        self.assertTrue(inv.converged)
        recs = [f for f in inv.findings if f.get("kind") == "reconcile"]
        self.assertEqual(len(recs), 2)
        self.assertTrue(all(f.get("reconciles") for f in recs))

    def test_author_passes_the_subquestion_verbatim_to_the_analyst(self) -> None:
        # the analyst looks up its own definitions; the orchestrator just hands it the sub-question.
        agent = _ScriptedAgent([_FakeAnswer([[7]])])
        model = _ScriptedModel([
            '{"action":"author","question":"count active buyers"}',
            '{"action":"finish","final_answer":"7 active buyers."}',
        ])
        inv = Investigator(agent=agent, model=model).investigate("how many active buyers?")
        self.assertTrue(inv.converged)
        self.assertEqual(agent.asked, ["count active buyers"])  # verbatim, no injected definitions

    def test_clarify_bubbles_up_from_a_sub_question(self) -> None:
        agent = _ScriptedAgent([_FakeAnswer(None, route="clarify")])
        agent._answers[0].clarify = "did you mean 2002 or 2003?"
        model = _ScriptedModel(['{"action":"author","question":"ambiguous"}'])
        inv = Investigator(agent=agent, model=model).investigate("ambiguous goal")
        self.assertEqual(inv.needs_clarification, "did you mean 2002 or 2003?")


if __name__ == "__main__":
    unittest.main()
