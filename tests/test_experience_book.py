"""M0: the ExperienceBook -- schema-scoped experiences.md in the object store, section-aware, so the
curator can make targeted edits. Same persistence shape as the conversation summary."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.memory.book import ExperienceBook  # noqa: E402
from diracdata.utils.object_store import LocalObjectStore  # noqa: E402


class ExperienceBookTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = LocalObjectStore(Path(self._tmp.name))
        self.book = ExperienceBook("retail_analytics", self.store)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_then_roundtrip(self) -> None:
        self.assertEqual(self.book.read(), "")
        self.book.write("## SQL PATTERNS\n- cohort new-vs-returning: MIN(year) per client CTE")
        self.assertIn("cohort new-vs-returning", self.book.read())
        # persisted under the schema-scoped key in the object store
        self.assertTrue(self.store.exists("experiences/retail_analytics/experiences.md"))

    def test_sections_parse(self) -> None:
        self.book.write("## SQL PATTERNS\n- p1\n\n## RCA LEADS\n- online_revenue drop -> check channel")
        secs = self.book.sections()
        self.assertIn("SQL PATTERNS", secs)
        self.assertIn("RCA LEADS", secs)
        self.assertIn("check channel", secs["RCA LEADS"])

    def test_update_section_adds_and_replaces(self) -> None:
        self.book.update_section("SQL PATTERNS", "- p1")
        self.book.update_section("RCA LEADS", "- lead1")
        self.assertEqual(len(self.book.sections()), 2)
        # replace one section's body -> other untouched
        self.book.update_section("SQL PATTERNS", "- p1\n- p2")
        secs = self.book.sections()
        self.assertIn("p2", secs["SQL PATTERNS"])
        self.assertIn("lead1", secs["RCA LEADS"])

    def test_update_section_empty_drops_it(self) -> None:
        self.book.update_section("GOTCHAS", "- billing_client_ref has nulls")
        self.book.update_section("GOTCHAS", "")   # empty -> remove
        self.assertNotIn("GOTCHAS", self.book.sections())

    def test_canonical_section_order(self) -> None:
        # add out of order -> rendered in canonical order (SQL PATTERNS before RCA LEADS before GOTCHAS)
        self.book.update_section("GOTCHAS", "- g")
        self.book.update_section("SQL PATTERNS", "- p")
        self.book.update_section("RCA LEADS", "- l")
        text = self.book.read()
        self.assertLess(text.index("SQL PATTERNS"), text.index("RCA LEADS"))
        self.assertLess(text.index("RCA LEADS"), text.index("GOTCHAS"))

    def test_reload_is_durable(self) -> None:
        self.book.update_section("BINDINGS", "- online_revenue = SUM(net_paid)")
        again = ExperienceBook("retail_analytics", self.store)   # fresh handle, same store
        self.assertIn("online_revenue", again.read())


if __name__ == "__main__":
    unittest.main()
