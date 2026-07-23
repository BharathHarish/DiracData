"""Tiered, column-first retrieval + the persistent value cache.

The compact column list must show EVERY column of even the widest table (the old describe_table
truncated), column detail must return the full description, and the value cache must round-trip.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "v2" / "src"))
sys.path.insert(0, str(ROOT / "v3" / "src"))

from diracdata_v3 import Workspace  # noqa: E402
from diracdata_v3.valuecache import ColumnValueCache  # noqa: E402

_META = ROOT / "v2" / "context" / "retail_analytics_metadata_descriptions.json"
_GOLD = ROOT / "v2" / "evals" / "Goldset_retail_queries.csv"
_DOMAINS = ROOT / "v2" / "context" / "retail_analytics_value_domains.json"


class ValueCacheTests(unittest.TestCase):
    def test_roundtrip_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "vc.json"
            c = ColumnValueCache(p)
            self.assertIsNone(c.get("merchandise", "category"))
            c.put("merchandise", "category", ["Books", "Music"])
            self.assertEqual(c.get("merchandise", "category"), ["Books", "Music"])
            self.assertEqual(ColumnValueCache(p).get("merchandise", "category"), ["Books", "Music"])  # reloaded

    def test_no_path_is_in_memory_only(self) -> None:
        c = ColumnValueCache(None)
        c.put("t", "c", [1, 2])
        self.assertEqual(c.get("t", "c"), [1, 2])


@unittest.skipUnless(_META.exists() and _GOLD.exists(), "retail context not present")
class TieredRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = Workspace.load(metadata_path=_META, gold_pairs_path=_GOLD,
                                 value_domains_path=_DOMAINS if _DOMAINS.exists() else None)

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


if __name__ == "__main__":
    unittest.main()
