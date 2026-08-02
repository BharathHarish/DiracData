"""The learning agent as an agentic loop, driven by a scripted FAKE model (no tokens):
it should call tools, then emit a dictionary, which the harness assembles into the fabric.
Also pins the harness-side assembly/fallback (pure logic, no engine/model).
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src"))

from diracdata.learning.fabric_agent import _assemble  # noqa: E402

_FIN = ROOT / "data" / "fintech_schema" / "parquet"


class AssemblyTests(unittest.TestCase):
    """Harness-side assembly: use what the agent emitted; fill only a safe fallback for anything
    it omitted; NEVER emit a column that isn't real, and always cover every real column."""

    def test_uses_emitted_and_fills_missing(self) -> None:
        parsed = {
            "table": {"short_description": "orders table", "long_description": "one row per order"},
            "columns": {
                "order_ref": {"short_description": "order id", "long_description": "unique order id",
                              "value_domain": {"complete": False, "values": [1, 2], "distinct_at_least": 15000}},
                # 'user_ref' deliberately omitted by the model
            },
        }
        table_doc, col_docs, col_domains = _assemble(parsed, ["order_ref", "user_ref"], "orders")
        self.assertEqual(table_doc["short_description"], "orders table")
        self.assertEqual(set(col_docs), {"order_ref", "user_ref"})          # exactly the real columns
        self.assertEqual(col_docs["order_ref"]["short_description"], "order id")
        self.assertTrue(col_docs["user_ref"]["short_description"])          # omitted -> fallback filled
        self.assertEqual(col_domains["order_ref"]["distinct_at_least"], 15000)
        self.assertEqual(col_domains["user_ref"], {})                       # no domain emitted -> empty

    def test_ignores_hallucinated_columns(self) -> None:
        parsed = {"columns": {"not_a_real_col": {"short_description": "x", "long_description": "y"}}}
        _, col_docs, _ = _assemble(parsed, ["real_col"], "t")
        self.assertEqual(set(col_docs), {"real_col"})                       # hallucinated col dropped


class _ScriptedModel:
    """Emits queued messages. First (optional) a tool call, then the final JSON. No .stream."""

    def __init__(self, steps):
        self._steps = list(steps)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        step = self._steps.pop(0) if self._steps else {"content": "{}"}
        return AIMessage(content=step.get("content", ""), tool_calls=step.get("tool_calls", []))


@unittest.skipUnless(_FIN.exists(), "fintech data not present")
class AgenticLoopTests(unittest.TestCase):
    def test_loop_calls_tool_then_emits_and_assembles(self) -> None:
        from diracdata.utils.duckdb_engine import DuckDBEngine
        from diracdata.learning import LearningAgent
        engine = DuckDBEngine(data_root=ROOT / "data", schema_name="fintech_schema")
        final = json.dumps({
            "table": {"short_description": "payment rails", "long_description": "one row per rail"},
            "columns": {"rail_type": {"short_description": "rail kind",
                                      "long_description": "UPI/CC/...",
                                      "value_domain": {"complete": True, "values": ["UPI", "CC"], "distinct_at_least": 7}}},
        })
        model = _ScriptedModel([
            {"tool_calls": [{"name": "profile_column", "args": {"table": "payment_attributes", "column": "rail_type"}, "id": "t1"}]},
            {"content": final},
        ])
        agent = LearningAgent(engine=engine, model=model)
        r = agent.compile(tables=["payment_attributes"], with_joins=False)
        self.assertEqual(r.metadata["tables"]["payment_attributes"]["short_description"], "payment rails")
        # every REAL column present (even those the model didn't describe), none hallucinated
        real = set(engine.list_columns("payment_attributes"))
        self.assertEqual(set(r.metadata["columns"]["payment_attributes"]), real)
        self.assertEqual(r.metadata["columns"]["payment_attributes"]["rail_type"]["short_description"], "rail kind")


if __name__ == "__main__":
    unittest.main()
