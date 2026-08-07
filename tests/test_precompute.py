"""Deterministic metric-RCA pre-compute (TO-PRE-*): the harness runs the whole attribution up-front and
injects a reconciled, CITED brief so the analyst narrates instead of re-deriving (kills the coin-flip).

Unit tests cover the pure pieces (adtributor on a negative metric, triage's RCA-target parse, the brief
render). The integration test drives the real pre-compute on the retail parquet: every assembled query
lands as a citable result_id in working memory and the reconciled tree + per-dimension movers come back.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.config import Config  # noqa: E402
from diracdata.rca.kernels import adtributor  # noqa: E402
from diracdata.rca.precompute import _fmt, _render_tree, _sliceable_metric, precompute_rca  # noqa: E402
from diracdata.agents.triage import _parse  # noqa: E402

_HAS_RETAIL = (ROOT / "data" / "retail_analytics").exists()


class AdtributorNegativeMetricTests(unittest.TestCase):
    """TO-PRE-03: a metric that is NEGATIVE in every slice (loss-making margin/profit) must still rank --
    the abs() share basis fixes the old max(v,0) clamp that zeroed impact for negatives."""

    def test_negative_metric_still_ranks_the_mover(self):
        # a negative-valued metric (e.g. web gross profit) worsening; the big mover must surface (impact>0)
        slices = [("Boomer", -9.0, -15.0), ("GenX", -5.0, -5.2), ("GenY", -3.0, -3.1)]
        ranked = adtributor(slices, top_k=3)
        self.assertEqual(ranked[0]["slice"], "Boomer")
        self.assertGreater(ranked[0]["impact"], 0.0)                 # was 0.0 before the fix

    def test_positive_metric_ranking_unchanged(self):
        # abs() is the identity for non-negative values -> same order as before the fix
        slices = [("Electronics", 150, 95), ("Music", 100, 100), ("Books", 50, 45)]
        self.assertEqual(adtributor(slices, top_k=3)[0]["slice"], "Electronics")


class TriageRcaTargetParseTests(unittest.TestCase):
    """TO-PRE-04: triage extracts (metric, period_a, period_b) for an rca question; nullable -> no target."""

    def test_parses_rca_target(self):
        v = _parse(json.dumps({"task_type": "rca", "lane": "cold", "rca_metric": "web_net_profit",
                               "period_a": "2001", "period_b": 2002}))
        self.assertEqual(v["task_type"], "rca")
        self.assertEqual(v["rca_target"], {"metric": "web_net_profit", "period_a": 2001, "period_b": 2002})

    def test_missing_period_yields_no_target(self):
        v = _parse(json.dumps({"task_type": "rca", "lane": "cold", "rca_metric": "web_net_profit",
                               "period_a": "", "period_b": "unknown"}))
        self.assertIsNone(v["rca_target"])                           # -> agentic fallback, no bad guess

    def test_analytics_never_carries_a_target(self):
        v = _parse(json.dumps({"task_type": "analytics", "lane": "cold", "rca_metric": "x",
                               "period_a": "2001", "period_b": "2002"}))
        self.assertIsNone(v["rca_target"])


class SliceableMetricTests(unittest.TestCase):
    """A formula-only top (net_revenue = revenue - refunds) can't be GROUP BY'd -> rank_movers must
    attribute its top measured arm, so demographic movers are ALWAYS computed (not left to the agent)."""

    _WS = SimpleNamespace(semantic_layer={"metrics": {
        "net_revenue": {"formula": "revenue - refunds", "depends_on": ["revenue", "refunds"]},
        "revenue": {"sql": "SUM(t.paid)"},
        "refunds": {"sql": "SUM(r.amt)"}}})

    def test_formula_only_metric_falls_through_to_measured_arm(self):
        self.assertEqual(_sliceable_metric(self._WS, "net_revenue"), "revenue")  # top sql-bearing driver

    def test_sql_bearing_metric_is_its_own_slice(self):
        self.assertEqual(_sliceable_metric(self._WS, "revenue"), "revenue")


class BriefRenderTests(unittest.TestCase):
    def test_render_tree_shows_contribution_and_share(self):
        node = {"name": "m", "v0": 100.0, "v1": 80.0, "delta": -20.0,
                "drivers": [{"name": "a", "v0": 60.0, "v1": 50.0, "delta": -10.0,
                             "contribution": -12.0, "pct": 0.6}]}
        lines: list[str] = []
        _render_tree(node, lines)
        joined = "\n".join(lines)
        self.assertIn("- m:", joined)
        self.assertIn("- a:", joined)
        self.assertIn("60% of parent", joined)                      # share surfaced for narration

    def test_fmt_large_and_small(self):
        self.assertEqual(_fmt(1234567), "1,234,567")
        self.assertIn(".", _fmt(0.1234))


@unittest.skipUnless(_HAS_RETAIL, "retail parquet not present")
class PrecomputeIntegrationTests(unittest.TestCase):
    """TO-PRE-01/02: real pre-compute on retail -- reconciled tree + all-dimension movers, every query a
    citable result_id in working memory (so the derivation reviewer has provenance)."""

    _LAYER = {
        "metrics": {
            "online_revenue": {"sql": "SUM(online_purchases.net_paid)", "decomposition": "multiplicative",
                               "depends_on": ["online_active_buyers", "revenue_per_buyer"]},
            "online_active_buyers": {"sql": "COUNT(DISTINCT online_purchases.billing_client_ref)"},
            "revenue_per_buyer": {"sql": "SUM(online_purchases.net_paid)::DOUBLE / "
                                         "NULLIF(COUNT(DISTINCT online_purchases.billing_client_ref), 0)"},
        },
        "time": {"online_purchases": {
            "join": "JOIN calendar_days ON online_purchases.sale_calendar_day_ref = calendar_days.calendar_day_record",
            "period_column": "calendar_days.year"}},
        "dimensions": {
            "gender": {"sql": "client_profiles.gender",
                       "join": "JOIN client_profiles ON online_purchases.billing_client_profile_ref = client_profiles.client_profile_record"},
            "product_category": {"sql": "merchandise.category",
                                 "join": "JOIN merchandise ON online_purchases.merchandise_ref = merchandise.merchandise_record"}},
    }

    def _ws(self):
        from diracdata.context.workspace import Workspace
        ws = Workspace.__new__(Workspace)
        ws.semantic_layer = self._LAYER
        return ws

    def test_precompute_injects_reconciled_cited_attribution(self):
        from diracdata.memory.working_memory import WorkingMemory
        from diracdata.memory.results import ResultStore
        from diracdata.utils.duckdb_engine import DuckDBEngine
        from diracdata.utils.object_store import LocalObjectStore
        eng = DuckDBEngine(data_root=ROOT / "data", schema_name="retail_analytics")
        memory = WorkingMemory(goal="why did online revenue fall 2001->2002 by age/gender?")
        with tempfile.TemporaryDirectory() as tmp:
            rs = ResultStore(engine=eng, store=LocalObjectStore(tmp), schema="retail_analytics")
            out = precompute_rca(target={"metric": "online_revenue", "period_a": 2001, "period_b": 2002},
                                 workspace=self._ws(), engine=eng, result_store=rs, memory=memory,
                                 config=Config())
        self.assertIsNotNone(out)                                    # target resolved -> pre-computed
        self.assertEqual(out["metric"], "online_revenue")
        self.assertTrue(out["result_ids"])                          # SQL ran + stored
        for rid in out["result_ids"]:                               # every one is citable in memory
            self.assertIn(rid, memory.results)
        self.assertIn("gender", out["movers"])                      # ALL defined dims ranked (not guessed)
        self.assertIn("product_category", out["movers"])
        self.assertIn("PRE-COMPUTED", out["brief"])
        self.assertIn("online_active_buyers", out["brief"])         # driver tree is in the brief
        self.assertTrue(memory.facts)                               # a headline fact was injected

    def test_bad_target_falls_back(self):
        from diracdata.memory.working_memory import WorkingMemory
        from diracdata.utils.duckdb_engine import DuckDBEngine
        eng = DuckDBEngine(data_root=ROOT / "data", schema_name="retail_analytics")
        m = WorkingMemory(goal="q")
        # undefined metric -> None (agentic fallback), no crash, nothing injected
        out = precompute_rca(target={"metric": "not_a_metric", "period_a": 2001, "period_b": 2002},
                             workspace=self._ws(), engine=eng, result_store=None, memory=m, config=Config())
        self.assertIsNone(out)
        self.assertFalse(m.results)


if __name__ == "__main__":
    unittest.main()
