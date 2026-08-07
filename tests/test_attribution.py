"""The one attribution primitive (P1). Unit tests cover the pure pieces (sliceable-metric fallback,
default/primary dims, the completeness CONTRACT: a failing dimension is present as `unavailable`, never
dropped). The integration test drives the real primitive on the retail parquet: reconciled driver tree,
every requested dimension present + cited, and the catalog rides along."""

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
from diracdata.rca.attribution import (  # noqa: E402
    AttributionResult, _rank_dimension, _sliceable_metric, attribute, default_dimensions,
)

_HAS_RETAIL = (ROOT / "data" / "retail_analytics").exists()

_LAYER = {
    "metrics": {
        "net_revenue": {"formula": "revenue - refunds", "depends_on": ["revenue", "refunds"]},
        "revenue": {"sql": "SUM(online_purchases.net_paid)", "decomposition": "multiplicative",
                    "depends_on": ["buyers", "rev_per_buyer"]},
        "buyers": {"sql": "COUNT(DISTINCT online_purchases.billing_client_ref)"},
        "rev_per_buyer": {"sql": "SUM(online_purchases.net_paid)::DOUBLE / "
                                 "NULLIF(COUNT(DISTINCT online_purchases.billing_client_ref), 0)"},
        "refunds": {"sql": "SUM(online_refunds.return_amount)"},
    },
    "time": {
        "online_purchases": {
            "join": "JOIN calendar_days ON online_purchases.sale_calendar_day_ref = calendar_days.calendar_day_record",
            "period_column": "calendar_days.year"},
        "online_refunds": {
            "join": "JOIN calendar_days ON online_refunds.return_calendar_day_ref = calendar_days.calendar_day_record",
            "period_column": "calendar_days.year"}},
    "dimensions": {
        "gender": {"sql": "client_profiles.gender", "group": "demographics", "primary": True,
                   "join": "JOIN client_profiles ON online_purchases.billing_client_profile_ref = client_profiles.client_profile_record"},
        "age_band": {"sql": "CASE WHEN clients.birth_year >= 1980 THEN 'GenY+' ELSE 'older' END",
                     "group": "demographics", "primary": True,
                     "join": "JOIN clients ON online_purchases.billing_client_ref = clients.client_record"},
        "brand": {"sql": "merchandise.brand", "cardinality": "high",
                  "join": "JOIN merchandise ON online_purchases.merchandise_ref = merchandise.merchandise_record"},
    },
}


def _ws():
    ws = SimpleNamespace(semantic_layer=_LAYER)
    from diracdata.context.workspace import Workspace
    ws.metric_tree = Workspace.metric_tree.__get__(ws, SimpleNamespace)
    ws.metric = Workspace.metric.__get__(ws, SimpleNamespace)
    return ws


class PureHelperTests(unittest.TestCase):
    def test_sliceable_metric_falls_through_formula_only_top(self):
        self.assertEqual(_sliceable_metric(_ws(), "net_revenue"), "revenue")   # top sql-bearing arm
        self.assertEqual(_sliceable_metric(_ws(), "revenue"), "revenue")

    def test_default_dimensions_are_the_primary_ones(self):
        self.assertEqual(set(default_dimensions(_ws())), {"gender", "age_band"})  # brand is not primary

    def test_rank_dimension_reports_unavailable_never_drops(self):
        # a runner that always errors -> the dimension is PRESENT with status unavailable, not missing
        def boom(sql):
            raise RuntimeError("boom")
        out = _rank_dimension(boom, _LAYER, "revenue", "gender", 2001, 2002, top_k=5, retries=1)
        self.assertEqual(out["status"], "unavailable")
        self.assertIn("boom", out["reason"])


class BriefContractTests(unittest.TestCase):
    def test_brief_names_every_requested_dimension_even_when_unavailable(self):
        res = AttributionResult(
            metric="revenue", period_a=2001, period_b=2002, total_delta=-100.0,
            tree={"name": "revenue", "v0": 500.0, "v1": 400.0, "delta": -100.0},
            slice_metric="revenue",
            dimensions={"gender": {"status": "ranked", "movers": [{"slice": "M", "delta": -80.0}]},
                        "age_band": {"status": "unavailable", "reason": "clients join failed"}},
            catalog={"metrics": ["revenue"], "dimensions": {"gender": {}, "age_band": {}, "brand": {}}},
            result_ids=["r1", "r2"])
        brief = res.to_brief()
        self.assertIn("gender:", brief)
        self.assertIn("age_band:", brief)                     # the unavailable dim is STILL named
        self.assertIn("could not attribute", brief)           # with an explicit gap note
        self.assertIn("brand", brief)                         # catalog surfaces other dims
        self.assertIn("EACH", brief)                          # per-dimension completeness instruction
        self.assertIn("r1", brief)                            # provenance to cite


@unittest.skipUnless(_HAS_RETAIL, "retail parquet not present")
class AttributeIntegrationTests(unittest.TestCase):
    def _run(self, metric, dims):
        from diracdata.memory.working_memory import WorkingMemory
        from diracdata.memory.results import ResultStore
        from diracdata.utils.duckdb_engine import DuckDBEngine
        from diracdata.utils.object_store import LocalObjectStore
        eng = DuckDBEngine(data_root=ROOT / "data", schema_name="retail_analytics")
        mem = WorkingMemory(goal="rca")
        with tempfile.TemporaryDirectory() as tmp:
            rs = ResultStore(engine=eng, store=LocalObjectStore(tmp), schema="retail_analytics")
            res = attribute(workspace=_ws(), engine=eng, result_store=rs, memory=mem,
                            metric=metric, period_a=2001, period_b=2002, dimensions=dims, config=Config())
        return res, mem

    def test_reconciles_and_every_requested_dim_present_and_cited(self):
        res, mem = self._run("revenue", ["gender", "age_band", "brand"])
        self.assertIsNotNone(res)
        # driver tree reconciles (buyers x rev_per_buyer == revenue move)
        self.assertLess(abs(res.tree.get("residual", 0)), 1.0)
        # ALL three requested dims present, ranked, and each cites a stored result_id in memory
        for d in ("gender", "age_band", "brand"):
            self.assertIn(d, res.dimensions)
            self.assertEqual(res.dimensions[d]["status"], "ranked")
            self.assertIn(res.dimensions[d]["result_id"], mem.results)
        self.assertTrue(res.result_ids)
        self.assertIn("age_band", res.to_brief())
        self.assertIn("brand", res.catalog["dimensions"])

    def test_formula_only_top_attributes_on_measured_arm(self):
        res, _ = self._run("net_revenue", ["gender"])
        self.assertEqual(res.slice_metric, "revenue")          # sliced on the measured arm
        self.assertEqual(res.dimensions["gender"]["status"], "ranked")

    def test_default_dimensions_when_none_requested(self):
        res, _ = self._run("revenue", None)
        self.assertEqual(set(res.dimensions), {"gender", "age_band"})   # the primary dims

    def test_undefined_metric_returns_none(self):
        res, mem = self._run("not_a_metric", ["gender"])
        self.assertIsNone(res)
        self.assertFalse(mem.results)


if __name__ == "__main__":
    unittest.main()
