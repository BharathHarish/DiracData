"""WorkingMemory + Plan -- the durable spine (pure logic, no engine/model)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.memory.working_memory import WorkingMemory  # noqa: E402
from diracdata.memory.plan import Plan  # noqa: E402


class MemoryTests(unittest.TestCase):
    def test_render_shows_goal_facts_and_results(self) -> None:
        m = WorkingMemory(goal="count AZ buyers")
        m.add_fact("net_paid = online revenue")
        m.note_result({"result_id": "r1", "columns": ["band", "n"], "row_count": 18,
                       "sql": "SELECT band, COUNT(*) n FROM x GROUP BY band"})
        out = m.render()
        self.assertIn("GOAL: count AZ buyers", out)
        self.assertIn("net_paid = online revenue", out)
        self.assertIn("r1: 18 rows", out)
        self.assertIn("query_result", out)  # tells the agent how to slice it

    def test_full_sql_kept_for_verify_but_display_truncates(self) -> None:
        # regression: truncating the stored SQL made the verifier reject correct answers as
        # "SQL incomplete". Store the full query; truncate only in the rendered display.
        m = WorkingMemory(goal="g")
        long_sql = "SELECT " + ", ".join(f"col{i}" for i in range(80)) + " FROM t WHERE x = 1"
        m.note_result({"result_id": "r1", "columns": ["c"], "row_count": 1, "sql": long_sql})
        self.assertEqual(m.results["r1"]["sql"], long_sql)          # full -> the verifier sees it all
        self.assertGreater(len(long_sql), 200)
        self.assertNotIn("col79", m.render())                       # display is truncated (stays lean)

    def test_add_fact_dedupes_and_normalizes(self) -> None:
        m = WorkingMemory(goal="g")
        m.add_fact("a  b")
        m.add_fact("a b")           # same after whitespace-normalization
        self.assertEqual(m.facts, ["a b"])

    def test_confirmed_intent_renders_bindings(self) -> None:
        m = WorkingMemory(goal="g", confirmed_intent={
            "intent": "spend = amount paid",
            "concepts": [{"phrase": "spend", "meaning": "amount paid", "binds_to": "net_paid"}]})
        out = m.render()
        self.assertIn("CONFIRMED INTENT: spend = amount paid", out)
        self.assertIn("spend -> net_paid", out)


class PlanTests(unittest.TestCase):
    def test_add_update_and_all_verified(self) -> None:
        p = Plan()
        a = p.add("total buyers")
        b = p.add("by band")
        self.assertEqual([i.id for i in p.items], ["t1", "t2"])
        self.assertFalse(p.all_verified())
        p.update(a.id, status="verified", evidence={"result_id": "r1", "number": 57})
        p.update(b.id, status="done")
        self.assertFalse(p.all_verified())           # b is only done, not verified
        p.update(b.id, status="verified")
        self.assertTrue(p.all_verified())

    def test_render_marks_status_and_evidence(self) -> None:
        p = Plan()
        i = p.add("total buyers")
        p.update(i.id, status="verified", evidence={"result_id": "r1", "number": 57})
        out = p.render()
        self.assertIn("[verified] t1: total buyers", out)
        self.assertIn("= 57", out)
        self.assertIn("r1", out)

    def test_blocked_items_surface(self) -> None:
        p = Plan()
        i = p.add("ambiguous cohort")
        p.update(i.id, status="blocked", note="channel vs exclusivity")
        self.assertEqual([x.id for x in p.blocked()], [i.id])


if __name__ == "__main__":
    unittest.main()
