"""v3 workspace: structural retrieval must land the exact gold pattern.

This is the hypothesis in a unit test: indexing examples by the tables/columns their SQL
uses (not a fuzzy question embedding) surfaces the correct precedent as the top hit --
the case v2's lossy retrieval got wrong.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src"))

from _fabric import GOLD as _GOLD  # noqa: E402
from _fabric import HISTORY as _HISTORY  # noqa: E402
from _fabric import HAS_RETAIL, retail_workspace  # noqa: E402


@unittest.skipUnless(HAS_RETAIL and _GOLD.exists(), "retail fabric not in object store")
class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ws = retail_workspace(gold_pairs_path=_GOLD, query_history_path=_HISTORY)

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

    def test_describe_table_shows_complete_value_domains(self) -> None:
        ws = self.ws  # from_store already carries the compiled value domains
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


if __name__ == "__main__":
    unittest.main()
