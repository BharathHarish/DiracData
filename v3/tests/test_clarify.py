"""The analyst asks a plain-language clarifying question on real ambiguity -- never guesses,
never uses technical/schema words, and does NOT author when it clarifies.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "v2" / "src"))
sys.path.insert(0, str(ROOT / "v3" / "src"))

from diracdata_v2.query import DuckDBEngine  # noqa: E402
from diracdata_v3 import V3Agent, Workspace  # noqa: E402
from diracdata_v3.agent import _ANALYST_PROMPT  # noqa: E402

_META = ROOT / "v2" / "context" / "retail_analytics_metadata_descriptions.json"
_GOLD = ROOT / "v2" / "evals" / "Goldset_retail_queries.csv"


class _RawModel:
    """Returns fixed content on every turn; no .stream and no tool calls, so the analyst loop
    treats the content as its final message."""

    def __init__(self, content: str) -> None:
        self._content = content

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        return AIMessage(content=self._content)


class AnalystPromptTests(unittest.TestCase):
    def test_prompt_bars_technical_language_and_demands_plain_intent(self) -> None:
        low = _ANALYST_PROMPT.lower()
        self.assertIn("clarify", low)
        self.assertIn("non-technical", low)
        self.assertIn("never mention tables, columns, joins", low)
        self.assertIn("materially different", low)  # conservative: only real ambiguity


@unittest.skipUnless(_META.exists() and _GOLD.exists(), "retail context not present")
class ClarifyRouteTests(unittest.TestCase):
    def _agent(self, analyst_content):
        ws = Workspace.load(metadata_path=_META, gold_pairs_path=_GOLD)
        engine = DuckDBEngine(data_root=ROOT / "v2" / "data", schema_name="retail_analytics")
        return V3Agent(model=_RawModel(analyst_content), steward_model=_RawModel("{}"),
                       workspace=ws, engine=engine)

    def test_clarify_short_circuits_without_authoring(self) -> None:
        clar = ("CLARIFY: Do you mean where the customer lives now, or where they lived when they bought?")
        agent = self._agent(clar)
        ans = agent.answer("count female users from Arizona who bought jewelry online, sliced by income")
        self.assertEqual(ans.route, "clarify")
        self.assertFalse(ans.answered)
        self.assertIn("where", ans.clarify.lower())
        self.assertEqual(ans.final_sql, "")  # never authored -- it asked instead of guessing

    def test_clarification_carries_no_schema_words(self) -> None:
        clar = ("CLARIFY: Should ordered nothing count only online orders, or any purchase at all?")
        ans = self._agent(clar).answer("customers who bought online in 2001 and ordered nothing in 2002")
        for banned in ("join", "column", "table", "sql", "select", "grain"):
            self.assertNotIn(banned, ans.clarify.lower())


if __name__ == "__main__":
    unittest.main()
