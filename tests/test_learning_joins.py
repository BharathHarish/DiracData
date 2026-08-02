"""Phase 2 -- verified join discovery. The `verify_join` tool executes the join and reports the
truth (grounding); the join loop is driven by a scripted model that proposes an edge, verifies
it, then emits the graph. Real fintech schema; skipped if absent.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src"))

_FIN = ROOT / "data" / "fintech_schema" / "parquet"


@unittest.skipUnless(_FIN.exists(), "fintech data not present")
class VerifyJoinToolTests(unittest.TestCase):
    """Ground truth: a real FK verifies clean; a shared non-key attribute is rejected/inflated."""

    @classmethod
    def setUpClass(cls) -> None:
        from diracdata.utils.duckdb_engine import DuckDBEngine
        from diracdata.learning.tools import build_learning_tools
        cls.engine = DuckDBEngine(data_root=ROOT / "data", schema_name="fintech_schema")
        cls.vj = next(t for t in build_learning_tools(engine=cls.engine) if t.name == "verify_join")

    def _verify(self, lt, lc, rt, rc):
        return json.loads(str(self.vj.invoke({"left_table": lt, "left_col": lc, "right_table": rt, "right_col": rc})))

    def test_real_fk_verifies_clean_with_right_orientation(self) -> None:
        # payments.rail_ref -> payment_attributes.rail_ref : rail is the dimension (unique key).
        r = self._verify("payments", "rail_ref", "payment_attributes", "rail_ref")
        self.assertEqual(r["verdict"], "accept")
        self.assertEqual(r["dimension"], "payment_attributes")  # oriented fact->dimension
        self.assertEqual(r["fact"], "payments")
        self.assertLessEqual(r["orphan_pct"], 1.0)              # referential integrity holds
        self.assertEqual(r["grain"], "1:1")                    # dim key unique -> no fan-out

    def test_user_ref_join_is_one_to_many_from_payments(self) -> None:
        r = self._verify("payments", "user_ref", "user_attributes", "user_ref")
        self.assertEqual(r["verdict"], "accept")
        self.assertEqual(r["dimension"], "user_attributes")
        self.assertEqual(r["grain"], "1:1")                    # each payment maps to exactly one user row

    def test_shared_nonkey_attribute_is_not_a_clean_key_join(self) -> None:
        # user_attributes.risk_band and payment_attributes.risk_band share a domain but are NOT a
        # key relationship -> the "dimension" side is non-unique, so this explodes fan-out.
        r = self._verify("user_attributes", "risk_band", "payment_attributes", "risk_band")
        self.assertGreater(r["fan_out"], 1.5)                  # massive inflation -> not a real edge
        self.assertEqual(r["grain"], "1:many")


class _ScriptedModel:
    def __init__(self, steps):
        self._steps = list(steps)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        step = self._steps.pop(0) if self._steps else {"content": "{}"}
        return AIMessage(content=step.get("content", ""), tool_calls=step.get("tool_calls", []))


@unittest.skipUnless(_FIN.exists(), "fintech data not present")
class JoinLoopTests(unittest.TestCase):
    def test_loop_verifies_then_emits_edges(self) -> None:
        from diracdata.utils.duckdb_engine import DuckDBEngine
        from diracdata.learning import LearningAgent
        engine = DuckDBEngine(data_root=ROOT / "data", schema_name="fintech_schema")
        final = json.dumps({"joins": [
            {"left_table": "payments", "left_col": "rail_ref", "right_table": "payment_attributes",
             "right_col": "rail_ref", "grain": "1:1", "orphan_pct": 0.0},
            {"bogus": "edge"},  # invalid -> dropped
        ]})
        model = _ScriptedModel([
            {"tool_calls": [{"name": "verify_join", "args": {"left_table": "payments", "left_col": "rail_ref",
                             "right_table": "payment_attributes", "right_col": "rail_ref"}, "id": "j1"}]},
            {"content": final},
        ])
        edges, _ = LearningAgent(engine=engine, model=model).learn_joins()
        self.assertEqual(len(edges), 1)                        # invalid edge dropped
        self.assertEqual(edges[0]["left_table"], "payments")
        self.assertEqual(edges[0]["right_table"], "payment_attributes")


if __name__ == "__main__":
    unittest.main()
