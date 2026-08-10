"""ResultStore -- large results go to parquet in the object store; a compact envelope + preview
come back; query_result slices the stored result. Uses the real retail DuckDB data; skipped if
absent. Also covers the tools wiring (run_sql registers the result in WorkingMemory).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from _fabric import DATA_PRESENT, HAS_RETAIL, SCHEMA, engine, retail_workspace  # noqa: E402
from diracdata.utils.object_store import LocalObjectStore  # noqa: E402
from diracdata.runtime.results import ResultStore  # noqa: E402


@unittest.skipUnless(DATA_PRESENT, "retail parquet data not present")
class ResultStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = LocalObjectStore(self._tmp.name)
        self.rs = ResultStore(engine=engine(), store=self.store, schema=SCHEMA)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_run_returns_envelope_and_persists_parquet(self) -> None:
        env = self.rs.run("SELECT DISTINCT category FROM merchandise WHERE category IS NOT NULL")
        self.assertTrue(env["result_id"])
        self.assertIn("category", env["columns"])
        self.assertEqual(env["row_count"], 10)               # 10 non-null categories
        self.assertEqual(len(env["preview"]), 10)            # small -> full preview
        self.assertFalse(env["truncated"])
        # the full result is persisted to the object store as parquet
        self.assertTrue(self.store.exists(f"results/{SCHEMA}/{env['result_id']}.parquet"))

    def test_preview_caps_a_large_result(self) -> None:
        env = self.rs.run("SELECT * FROM store_purchases")   # millions of rows
        self.assertGreater(env["row_count"], 1000)
        self.assertEqual(len(env["preview"]), 100)           # preview capped
        self.assertTrue(env["truncated"])

    def test_query_result_slices_a_stored_result(self) -> None:
        env = self.rs.run("SELECT category FROM merchandise")   # 18000 rows, one column
        self.assertTrue(env["truncated"])
        out = self.rs.query(env["result_id"], "SELECT COUNT(DISTINCT category) AS c FROM result")
        self.assertEqual(out["rows"][0][0], 10)              # aggregates the stored parquet, no re-run

    def test_query_result_reloads_from_object_store(self) -> None:
        env = self.rs.run("SELECT category, COUNT(*) n FROM merchandise GROUP BY category")
        # a fresh store over the SAME object store must re-fetch the parquet to slice it
        rs2 = ResultStore(engine=engine(), store=self.store, schema=SCHEMA)
        out = rs2.query(env["result_id"], "SELECT SUM(n) AS total FROM result")
        self.assertEqual(out["rows"][0][0], 18000)           # total merchandise rows


@unittest.skipUnless(HAS_RETAIL and DATA_PRESENT, "retail fabric/data not present")
class ToolsWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        from diracdata.runtime.working_memory import WorkingMemory
        from diracdata.tools import build_tools
        self._tmp = tempfile.TemporaryDirectory()
        self.memory = WorkingMemory(goal="how many categories?")
        rs = ResultStore(engine=engine(), store=LocalObjectStore(self._tmp.name), schema=SCHEMA)
        self.tools = {t.name: t for t in build_tools(
            workspace=retail_workspace(), engine=engine(), result_store=rs, memory=self.memory)}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_run_sql_registers_result_in_memory(self) -> None:
        out = json.loads(str(self.tools["run_sql"].invoke(
            {"sql": "SELECT category, COUNT(*) n FROM merchandise GROUP BY category"})))
        rid = out["result_id"]
        self.assertIn(rid, self.memory.results)              # durable RESULT INDEX updated
        self.assertIn("r", rid)

    def test_run_sql_rejects_non_select(self) -> None:
        out = str(self.tools["run_sql"].invoke({"sql": "DROP TABLE merchandise"}))
        self.assertIn("rejected", out.lower())

    def test_v3_retrieval_tools_are_present_and_run_sql_swapped(self) -> None:
        for name in ("get_tables", "get_columns", "describe_columns", "profile_column",
                     "join_path", "run_sql", "query_result"):
            self.assertIn(name, self.tools)


if __name__ == "__main__":
    unittest.main()
