"""Tiered, column-first retrieval + the persistent value cache.

The compact column list must show EVERY column of even the widest table (the old describe_table
truncated), column detail must return the full description, and the value cache must round-trip.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src"))

from _fabric import GOLD as _GOLD  # noqa: E402
from _fabric import HAS_RETAIL, retail_workspace  # noqa: E402
from diracdata.utils.object_store import LocalObjectStore  # noqa: E402
from diracdata.context.fabric import FabricStore  # noqa: E402
from diracdata.context.valuecache import ColumnValueCache  # noqa: E402


class ValueCacheTests(unittest.TestCase):
    def test_roundtrip_and_persistence_via_object_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fs = FabricStore(LocalObjectStore(tmp))
            c = ColumnValueCache(fs, "retail_analytics")
            self.assertIsNone(c.get("merchandise", "category"))
            c.put("merchandise", "category", ["Books", "Music"])
            self.assertEqual(c.get("merchandise", "category"), ["Books", "Music"])
            # a fresh cache over the SAME store reloads the persisted values
            self.assertEqual(ColumnValueCache(fs, "retail_analytics").get("merchandise", "category"), ["Books", "Music"])

    def test_schemas_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fs = FabricStore(LocalObjectStore(tmp))
            ColumnValueCache(fs, "fin").put("t", "c", [1, 2])
            self.assertIsNone(ColumnValueCache(fs, "retail_analytics").get("t", "c"))  # not crossed


@unittest.skipUnless(HAS_RETAIL and _GOLD.exists(), "retail fabric not in object store")
class TieredRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = retail_workspace(gold_pairs_path=_GOLD)

    def test_compact_lists_every_column_of_the_widest_table(self) -> None:
        cols = self.ws.columns_compact("online_purchases")
        self.assertIsNotNone(cols)
        # the real widest fact table -- the old describe_table truncated this; compact must not.
        self.assertEqual(len(cols), len(self.ws.column_names("online_purchases")))
        self.assertTrue(any(c["name"] == "net_paid" for c in cols))
        self.assertTrue(all(c.get("description") for c in cols))

    def test_compact_can_narrow_to_one_column(self) -> None:
        one = self.ws.columns_compact("online_purchases", "net_paid")
        self.assertEqual([c["name"] for c in one], ["net_paid"])

    def test_column_detail_gives_full_description(self) -> None:
        short = self.ws.columns_compact("online_purchases", "net_paid")[0]["description"]
        full = self.ws.column_detail("online_purchases", "net_paid")["description"]
        self.assertGreaterEqual(len(full), len(short))  # detail is at least as complete

    def test_missing_table_or_column_returns_none(self) -> None:
        self.assertIsNone(self.ws.columns_compact("no_such_table"))
        self.assertIsNone(self.ws.column_detail("online_purchases", "no_such_col"))


@unittest.skipUnless(HAS_RETAIL, "retail fabric not in object store")
class RetrievalTierToolsTests(unittest.TestCase):
    """The four registered navigation tools the analyst actually calls: scan (short) then
    detail (long) for tables and for columns. This is the tiered drill-down, end to end."""

    @classmethod
    def setUpClass(cls) -> None:
        from diracdata.utils.duckdb_engine import DuckDBEngine
        from diracdata.tools.navigation import build_navigation_tools
        ws = retail_workspace()
        engine = DuckDBEngine(data_root=ROOT / "data", schema_name="retail_analytics")
        cls.tools = {t.name: t for t in build_navigation_tools(workspace=ws, engine=engine)}

    def _call(self, name, **kwargs):
        return str(self.tools[name].invoke(kwargs))

    def test_all_four_tiers_are_registered(self) -> None:
        for name in ("get_tables", "describe_tables", "get_columns", "describe_columns"):
            self.assertIn(name, self.tools)
        self.assertNotIn("describe_column", self.tools)  # the old single-column tool is gone

    def test_get_tables_scans_every_table_as_one_liners(self) -> None:
        out = self._call("get_tables")
        # one line per table, each "- <name>: <desc>"; the widest fact table is listed
        self.assertIn("online_purchases", out)
        self.assertTrue(all(line.startswith("- ") for line in out.splitlines() if line.strip()))

    def test_describe_tables_tie_breaks_candidates_with_full_text(self) -> None:
        out = self._call("describe_tables", tables=["online_purchases", "store_purchases"])
        self.assertIn("online_purchases", out)
        self.assertIn("store_purchases", out)
        self.assertIn("columns)", out)                       # reports the column count
        # a bad candidate is reported, not fatal
        self.assertIn("No such table", self._call("describe_tables", tables=["nope"]))

    def test_get_columns_scans_the_full_width_no_truncation(self) -> None:
        out = self._call("get_columns", table_name="online_purchases")
        self.assertIn("net_paid", out)
        # every real column appears in the compact scan (the old describe_table truncated)
        names = [ln.split(":")[0].strip(" -") for ln in out.splitlines() if ln.strip().startswith("- ")]
        self.assertIn("net_paid", names)

    def test_describe_columns_tie_breaks_near_synonyms(self) -> None:
        out = self._call("describe_columns", table_name="online_purchases",
                         columns=["net_paid", "unit_price"])
        self.assertIn("net_paid", out)
        self.assertIn("unit_price", out)
        self.assertIn("No column 'nope'", self._call(
            "describe_columns", table_name="online_purchases", columns=["nope"]))


if __name__ == "__main__":
    unittest.main()
