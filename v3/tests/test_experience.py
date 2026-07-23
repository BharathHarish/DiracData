"""The experience curator: remember only what's worth a free replay next time.

Verified + novel + Brain-route, deterministically -- no LLM, and never a wrong answer.
These pin the policy that v2 got wrong (it wrote every run) using a fake Brain so the
gates are exercised without spending tokens.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "v2" / "src"))
sys.path.insert(0, str(ROOT / "v3" / "src"))

from diracdata_v2.query import DuckDBEngine  # noqa: E402
from diracdata_v3 import ExperienceStore, Workspace  # noqa: E402
from diracdata_v3.agent import V3Agent  # noqa: E402

_META = ROOT / "v2" / "context" / "retail_analytics_metadata_descriptions.json"
_GOLD = ROOT / "v2" / "evals" / "Goldset_retail_queries.csv"


class ExperienceStoreTests(unittest.TestCase):
    def test_append_and_reload_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceStore(Path(tmp) / "exp.jsonl")
            self.assertEqual(store.load(), [])
            store.append(question="How many stores are in TN?", sql="SELECT COUNT(*) FROM retail_locations", route="cold_start")
            recs = store.load()
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0]["tier"], "provisional")
            self.assertTrue(store.has_question("how many stores are in tn"))  # normalized


class _NullModel:
    """A stand-in model; _record is deterministic and never calls it."""

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        return AIMessage(content="{}")


@unittest.skipUnless(_META.exists() and _GOLD.exists(), "retail context not present")
class RecordTests(unittest.TestCase):
    """Write-back is deterministic: the independent verify decides worth_remembering; _record
    then wires it only if the query is genuinely novel (never duplicating gold/experience)."""

    def _agent(self, tmp):
        store = ExperienceStore(Path(tmp) / "exp.jsonl")
        ws = Workspace.load(metadata_path=_META, gold_pairs_path=_GOLD, experience_store=store)
        engine = DuckDBEngine(data_root=ROOT / "v2" / "data", schema_name="retail_analytics")
        agent = V3Agent(model=_NullModel(), steward_model=None, workspace=ws, engine=engine,
                        experience_store=store)
        return agent, store

    def test_records_when_verify_says_worth_remembering_and_novel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent, store = self._agent(tmp)
            q = "How many retail locations have more than 200 employees?"
            sql = "SELECT COUNT(*) FROM retail_locations WHERE number_employees > 200"
            self.assertTrue(agent._record(q, sql, worth_remembering=True))
            self.assertEqual(len(store.load()), 1)
            self.assertTrue(any(e.source == "experience"
                                for e in agent.workspace.find_examples("retail_locations employees")))

    def test_skips_when_verify_says_not_worth_remembering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent, store = self._agent(tmp)
            self.assertFalse(agent._record("How many delivery methods exist?",
                                           "SELECT COUNT(*) FROM delivery_methods", worth_remembering=False))
            self.assertEqual(store.load(), [])

    def test_gold_covered_question_is_not_recorded_even_if_worth_remembering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent, store = self._agent(tmp)  # verify says yes, but coverage blocks the duplicate
            ok = agent._record("Count current F customers from TX whose first sale year is 2001.",
                               "SELECT 1", worth_remembering=True)
            self.assertFalse(ok)
            self.assertEqual(store.load(), [])


if __name__ == "__main__":
    unittest.main()
