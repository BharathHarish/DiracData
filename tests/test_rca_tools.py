"""RCA engine-backed tools on the retail dataset with an INLINE semantic layer (no object store needed):
metric_series assembles the query from the layer, attribute_change reconciles exactly, rank_movers
returns the slices that carry the move. Skips if the retail parquet isn't present."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.config import Config  # noqa: E402
from diracdata.rca.tools import build_rca_tools  # noqa: E402
from diracdata.utils.duckdb_engine import DuckDBEngine  # noqa: E402

_HAS_RETAIL = (ROOT / "data" / "retail_analytics").exists()

_LAYER = {
    "metrics": {
        "online_revenue": {"sql": "SUM(online_purchases.net_paid)"},
        "online_active_buyers": {"sql": "COUNT(DISTINCT online_purchases.billing_client_ref)"},
        "revenue_per_buyer": {"sql": "SUM(online_purchases.net_paid)::DOUBLE / "
                                     "NULLIF(COUNT(DISTINCT online_purchases.billing_client_ref), 0)"},
    },
    "time": {"online_purchases": {
        "join": "JOIN calendar_days ON online_purchases.sale_calendar_day_ref = calendar_days.calendar_day_record",
        "period_column": "calendar_days.year"}},
    "dimensions": {"product_category": {
        "sql": "merchandise.category",
        "join": "JOIN merchandise ON online_purchases.merchandise_ref = merchandise.merchandise_record"}},
}


@unittest.skipUnless(_HAS_RETAIL, "retail parquet not present")
class RcaToolsTests(unittest.TestCase):
    def setUp(self):
        self.eng = DuckDBEngine(data_root=ROOT / "data", schema_name="retail_analytics")
        ws = SimpleNamespace(semantic_layer=_LAYER)
        self.tools = {t.name: t for t in build_rca_tools(workspace=ws, engine=self.eng, config=Config())}

    def _vals(self):
        d = json.loads(self.tools["metric_series"].invoke(
            {"metrics": ["online_revenue", "online_active_buyers", "revenue_per_buyer"], "periods": [2001, 2002]}))
        return d, {row[0]: dict(zip(d["columns"], row)) for row in d["rows"]}

    def test_metric_series_assembles_and_evaluates(self):
        d, v = self._vals()
        self.assertEqual([r[0] for r in d["rows"]], [2001, 2002])          # both periods, from the layer
        self.assertEqual(int(v[2001]["online_active_buyers"]), 11552)      # known value
        self.assertEqual(int(v[2002]["online_active_buyers"]), 11252)

    def test_attribute_change_reconciles_to_zero_residual(self):
        _, v = self._vals()
        f = float
        ac = json.loads(self.tools["attribute_change"].invoke({"kind": "multiplicative", "children": [
            {"name": "buyers", "v0": f(v[2001]["online_active_buyers"]), "v1": f(v[2002]["online_active_buyers"])},
            {"name": "rpb", "v0": f(v[2001]["revenue_per_buyer"]), "v1": f(v[2002]["revenue_per_buyer"])}]}))
        actual = f(v[2002]["online_revenue"]) - f(v[2001]["online_revenue"])
        self.assertAlmostEqual(ac["parent_delta"], actual, places=2)       # matches the real revenue delta
        self.assertAlmostEqual(ac["residual"], 0.0, places=2)              # exact split, nothing unexplained
        self.assertEqual(len(ac["contributions"]), 2)

    def test_rank_movers_finds_the_declining_categories(self):
        rm = json.loads(self.tools["rank_movers"].invoke(
            {"metric": "online_revenue", "dimension": "product_category", "period_a": 2001, "period_b": 2002, "top_k": 5}))
        movers = {m["slice"]: m["delta"] for m in rm["movers"]}
        self.assertIn("Shoes", movers)                                     # a known large decliner surfaces
        self.assertTrue(any(d < 0 for d in movers.values()))

    def test_unknown_metric_is_clear_feedback_not_a_crash(self):
        out = self.tools["metric_series"].invoke({"metrics": ["nope"], "periods": [2001]})
        self.assertIn("no predefined sql", out)


if __name__ == "__main__":
    unittest.main()
