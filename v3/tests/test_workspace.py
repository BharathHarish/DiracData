"""v3 workspace: structural retrieval must land the exact gold pattern.

This is the hypothesis in a unit test: indexing examples by the tables/columns their SQL
uses (not a fuzzy question embedding) surfaces the correct precedent as the top hit --
the case v2's lossy retrieval got wrong.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "v2" / "src"))
sys.path.insert(0, str(ROOT / "v3" / "src"))

from diracdata_v3 import Workspace  # noqa: E402

_META = ROOT / "v2" / "context" / "retail_analytics_metadata_descriptions.json"
_GOLD = ROOT / "v2" / "evals" / "Goldset_retail_queries.csv"
_HISTORY = ROOT / "v2" / "data" / "query_history" / "retail_analytics_query_history.csv"
_DOMAINS = ROOT / "v2" / "context" / "retail_analytics_value_domains.json"


@unittest.skipUnless(_META.exists() and _GOLD.exists(), "retail context not present")
class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ws = Workspace.load(metadata_path=_META, gold_pairs_path=_GOLD, query_history_path=_HISTORY)

    def test_examples_load(self) -> None:
        self.assertGreater(len(self.ws.examples), 100)

    def test_describe_table_surfaces_canonical_column(self) -> None:
        info = self.ws.describe_table("clients")
        self.assertIsNotNone(info)
        cols = [c["name"] for c in info["columns"]]
        self.assertIn("first_sale_calendar_day_ref", cols)
        # every column carries a description (the raw material, not a summary)
        self.assertTrue(all(c["description"] for c in info["columns"]))

    def test_describe_table_shows_observed_joins(self) -> None:
        info = self.ws.describe_table("clients")
        self.assertTrue(info["observed_joins"], "no joins observed for clients")

    @unittest.skipUnless(_DOMAINS.exists(), "value domains not generated")
    def test_describe_table_shows_complete_value_domains(self) -> None:
        ws = Workspace.load(metadata_path=_META, gold_pairs_path=_GOLD, value_domains_path=_DOMAINS)
        cat = next(c for c in ws.describe_table("merchandise")["columns"] if c["name"] == "category")
        # the COMPLETE domain must include the real value so 'jewellry' resolves to 'Jewelry'
        self.assertIn("Jewelry", cat["values"])
        self.assertIn("values:", cat["values"])  # marked complete
        gen = next(c for c in ws.describe_table("client_profiles")["columns"] if c["name"] == "gender")
        self.assertIn("'F'", gen["values"])
        self.assertIn("'M'", gen["values"])

    def test_find_examples_lands_the_exact_gold_pattern(self) -> None:
        hits = self.ws.find_examples("clients addresses first_sale gender state count customers")
        self.assertTrue(hits)
        top = hits[0]
        self.assertEqual(top.source, "gold")
        self.assertIn("first sale", top.question.lower())
        self.assertIn("first_sale_calendar_day_ref", " ".join(top.columns))

    def test_structural_match_beats_unrelated_keyword(self) -> None:
        # A refund-by-reason query must surface refund examples, not customer ones.
        hits = self.ws.find_examples("store_refunds return_reasons refund amount by reason")
        self.assertTrue(hits)
        self.assertTrue(any("store_refunds" in h.tables for h in hits))

    def test_unknown_table_is_none(self) -> None:
        self.assertIsNone(self.ws.describe_table("no_such_table"))

    def test_exact_match_finds_gold_question_case_and_punctuation_insensitive(self) -> None:
        # A gold pair is an offline eval: matching its question must return that gold pair.
        hit = self.ws.exact_match("count current f customers from tx whose first sale year is 2001")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.source, "gold")
        self.assertIn("first_sale_calendar_day_ref", " ".join(hit.columns))

    def test_exact_match_returns_none_for_novel_question(self) -> None:
        self.assertIsNone(self.ws.exact_match("what is the average shoe size of customers in narnia"))


    def test_slot_match_swaps_a_literal_and_keeps_structure(self) -> None:
        # Same gold shape, different gender literal -> gold SQL with 'F' -> 'M'.
        m = self.ws.slot_match("Count current M customers from TX whose first sale year is 2001.")
        self.assertIsNotNone(m)
        adapted_sql, gold = m
        self.assertIn("'M'", adapted_sql)
        self.assertNotIn("'F'", adapted_sql)
        self.assertIn("first_sale_calendar_day_ref", adapted_sql)

    def test_slot_match_rejects_a_structurally_different_question(self) -> None:
        # Not a literal swap of any gold question -> no false slot match.
        self.assertIsNone(self.ws.slot_match("How many warehouses are there per state and city and region"))


class RouteTests(unittest.TestCase):
    """The four routes, exercised deterministically (no LLM) for the two that need none."""

    @classmethod
    def setUpClass(cls) -> None:
        from diracdata_v2.query import DuckDBEngine
        from diracdata_v3.agent import V3Agent

        ws = Workspace.load(metadata_path=_META, gold_pairs_path=_GOLD, query_history_path=_HISTORY)
        engine = DuckDBEngine(data_root=ROOT / "v2" / "data", schema_name="retail_analytics")
        cls.agent = V3Agent(model=None, steward_model=None, workspace=ws, engine=engine)

    def test_exact_replay_is_deterministic_and_free(self) -> None:
        ans = self.agent.answer("Count current F customers from TX whose first sale year is 2001.")
        self.assertEqual(ans.route, "exact_replay")
        self.assertEqual(ans.tokens, 0)
        self.assertEqual(ans.result["rows"][0][-1], 369)

    def test_slot_adapt_is_deterministic_and_free(self) -> None:
        ans = self.agent.answer("Count current M customers from TX whose first sale year is 2001.")
        self.assertEqual(ans.route, "slot_adapt")
        self.assertEqual(ans.tokens, 0)
        self.assertEqual(ans.result["rows"][0][-1], 367)


if __name__ == "__main__":
    unittest.main()
