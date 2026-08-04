"""T4 metric tree + RCA scaffolding: the structured metric accessor + recursive depends_on walk on
the Workspace, and the agent-facing metric_tree tool (gated on a real tree). Structure is
authored/measured here; quantifying + ranking the drivers stays the agent's job (prompt-driven), so
these tests cover the STRUCTURE the RCA walk consumes, not a deterministic ranker.

The demo tree mirrors docs/examples/semantic_layer.fintech.json but is inline (no fabric/services).
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.context.workspace import Workspace  # noqa: E402


def _fintech_layer() -> dict:
    return {
        "metrics": {
            "revenue": {"description": "total payments", "sql": "SELECT SUM(amount) FROM payments",
                        "decomposition": "multiplicative", "depends_on": ["order_volume", "aov"]},
            "order_volume": {"description": "distinct orders", "sql": "SELECT COUNT(DISTINCT order_id) FROM orders",
                             "decomposition": "additive",
                             "depends_on": ["new_customer_volume", "returning_customer_volume"]},
            "aov": {"description": "avg order value", "formula": "revenue / order_volume",
                    "decomposition": "multiplicative", "depends_on": ["avg_unit_price", "segment_mix"]},
            "new_customer_volume": {"sql": "SELECT ... new"},
            "returning_customer_volume": {"sql": "SELECT ... returning"},
            "avg_unit_price": {"sql": "SELECT AVG(amount) FROM payments"},
            # segment_mix intentionally has NO metric def -> it's a leaf driver (still nameable in a fan-out)
        },
    }


def _ws(layer: dict | None) -> Workspace:
    return Workspace.load(metadata={"columns": {}}, semantic_layer=layer)


class MetricAccessorTests(unittest.TestCase):
    """TO-T4-01."""

    def test_metric_returns_raw_dict_or_none(self):
        ws = _ws(_fintech_layer())
        rev = ws.metric("revenue")
        self.assertEqual(rev["depends_on"], ["order_volume", "aov"])
        self.assertEqual(rev["decomposition"], "multiplicative")
        self.assertIsNone(ws.metric("not_a_metric"))

    def test_metric_normalizes_name(self):
        ws = _ws(_fintech_layer())
        self.assertIsNotNone(ws.metric("Order Volume"))          # 'Order Volume' -> 'order_volume'


class MetricTreeTests(unittest.TestCase):
    """TO-T4-02 / TO-T4-03."""

    def test_recursive_expansion_carries_sql_and_decomposition(self):
        tree = _ws(_fintech_layer()).metric_tree("revenue")
        self.assertTrue(tree["defined"])
        self.assertEqual(tree["decomposition"], "multiplicative")
        drivers = {d["name"]: d for d in tree["drivers"]}
        self.assertEqual(set(drivers), {"order_volume", "aov"})
        # one level down: order_volume expands to its two customer drivers
        ov = {d["name"] for d in drivers["order_volume"]["drivers"]}
        self.assertEqual(ov, {"new_customer_volume", "returning_customer_volume"})
        # an undefined driver (segment_mix) is a leaf, still nameable
        seg = [d for d in drivers["aov"]["drivers"] if d["name"] == "segment_mix"][0]
        self.assertFalse(seg["defined"])
        # a defined leaf carries its SQL
        aup = [d for d in drivers["aov"]["drivers"] if d["name"] == "avg_unit_price"][0]
        self.assertIn("AVG(amount)", aup["sql"])

    def test_depth_bound_truncates_without_recursing(self):
        tree = _ws(_fintech_layer()).metric_tree("revenue", max_depth=1)
        ov = tree["drivers"][0]
        self.assertEqual(ov["name"], "order_volume")
        self.assertNotIn("drivers", ov)                          # depth 1 -> children not expanded
        self.assertIn("new_customer_volume", ov["drivers_truncated"])

    def test_cycle_is_safe(self):
        layer = {"metrics": {"a": {"depends_on": ["b"]}, "b": {"depends_on": ["a"]}}}
        tree = _ws(layer).metric_tree("a")                       # a -> b -> a(on path) -> truncated
        a_again = tree["drivers"][0]["drivers"][0]
        self.assertEqual(a_again["name"], "a")
        self.assertIn("b", a_again["drivers_truncated"])         # re-hit an ancestor -> truncated, no infinite loop

    def test_unknown_metric_is_leaf(self):
        tree = _ws(_fintech_layer()).metric_tree("nope")
        self.assertFalse(tree["defined"])


class MetricTreeToolTests(unittest.TestCase):
    """TO-T4-04 / TO-T4-05: the agent-facing tool, gated on a real tree."""

    def _tools(self, layer):
        from diracdata.tools.navigation import build_navigation_tools

        class _Eng:
            dialect = "duckdb"
            name = "x"
            def list_tables(self): return []
            def describe_columns(self, t): return []

        return {t.name: t for t in build_navigation_tools(workspace=_ws(layer), engine=_Eng())}

    def test_tool_returns_structured_tree(self):
        tools = self._tools(_fintech_layer())
        self.assertIn("metric_tree", tools)
        out = json.loads(str(tools["metric_tree"].invoke({"metric_name": "revenue"})))
        self.assertEqual(out["name"], "revenue")
        self.assertEqual({d["name"] for d in out["drivers"]}, {"order_volume", "aov"})

    def test_tool_unknown_metric_lists_defined(self):
        out = str(self._tools(_fintech_layer())["metric_tree"].invoke({"metric_name": "ebitda"}))
        self.assertIn("not a defined metric", out)
        self.assertIn("revenue", out)                            # points the agent at what IS defined

    def test_tool_absent_without_metrics(self):
        # a semantic layer with terms but no metrics -> no metric_tree tool (earn-its-keep gating)
        self.assertNotIn("metric_tree", self._tools({"business_terms": {"x": {"description": "y"}}}))
        self.assertNotIn("metric_tree", self._tools(None))       # no semantic layer at all


class RcaFanoutShapeTests(unittest.TestCase):
    """TO-T4-06: the tree's top-level drivers are exactly the tasks an RCA fans out with
    spawn_subagents (one driver per branch). We assert the mapping shape, not a live model run."""

    def test_drivers_map_to_fanout_tasks(self):
        tree = _ws(_fintech_layer()).metric_tree("revenue")
        tasks = [{"task": f"Quantify driver '{d['name']}' contribution to revenue over the two periods.",
                  "context": d.get("sql") or d.get("formula") or ""} for d in tree["drivers"]]
        self.assertEqual(len(tasks), 2)                          # one branch per top-level driver
        self.assertTrue(all(t["task"] and "driver" in t["task"] for t in tasks))
        self.assertTrue(any("SUM(amount)" not in t["context"] for t in tasks))  # aov carries a formula, not that sql


if __name__ == "__main__":
    unittest.main()
