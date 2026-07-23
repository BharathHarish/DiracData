"""The join graph: derive canonical edges from the naming convention, compose multi-hop
paths, and LEARN joins the agent discovers so the graph grows.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "v2" / "src"))
sys.path.insert(0, str(ROOT / "v3" / "src"))

from diracdata_v3 import ExperienceStore, JoinStore, Workspace  # noqa: E402

_META = ROOT / "v2" / "context" / "retail_analytics_metadata_descriptions.json"


@unittest.skipUnless(_META.exists(), "retail context not present")
class JoinGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ws = Workspace.load(metadata_path=_META)

    def test_derives_canonical_edges(self) -> None:
        # billing_client_ref -> clients.client_record, disambiguated from shipping_client_ref
        path = self.ws.join_path("online_purchases", "clients")
        self.assertIsNotNone(path)
        self.assertTrue(any("billing_client_ref = clients.client_record" in c
                            or "clients.client_record = online_purchases.billing_client_ref" in c for c in path))

    def test_multi_hop_path_is_composed(self) -> None:
        # online_purchases -> income_ranges is a 3-way join through household_profiles
        path = self.ws.join_path("online_purchases", "income_ranges")
        self.assertIsNotNone(path)
        self.assertEqual(len(path), 2)
        joined = " ".join(path)
        self.assertIn("household_profile", joined)
        self.assertIn("income_range", joined)

    def test_unreachable_returns_none(self) -> None:
        self.assertIsNone(self.ws.join_path("clients", "no_such_table"))

    def test_describe_table_exposes_canonical_joins(self) -> None:
        info = self.ws.describe_table("store_purchases")
        self.assertTrue(info["joins"])
        self.assertTrue(any("retail_location_ref = retail_locations.retail_location_record" in j
                            or "retail_locations.retail_location_record = store_purchases.retail_location_ref" in j
                            for j in info["joins"]))


class JoinLearningTests(unittest.TestCase):
    def test_learn_join_records_a_new_edge_and_makes_it_reachable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JoinStore(Path(tmp) / "joins.jsonl")
            ws = Workspace.load(metadata_path=_META, join_store=store)
            # a hypothetical non-convention bridge the agent might discover
            self.assertTrue(ws.learn_join("clients.email_address", "site_pages.url"))
            # already derived edges are not re-learned
            self.assertFalse(ws.learn_join("online_purchases.billing_client_ref", "clients.client_record"))

    def test_join_store_dedupes_and_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JoinStore(Path(tmp) / "joins.jsonl")
            self.assertTrue(store.append(left="a.x", right="b.y"))
            self.assertFalse(store.append(left="b.y", right="a.x"))  # same edge, undirected
            self.assertEqual(len(store.load()), 1)


if __name__ == "__main__":
    unittest.main()
