"""Phase 1: the RECONCILER (separate from any source, locked down, spills instead of OOM) and
`combine_results` -- join stored results (potentially from different sources) in one DuckDB step,
output stored as a NEW faithful result. Self-contained: a DuckDB source fixture + generated
VALUES/range results, no external data or services.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from _engine_fixture import make_duckdb_source  # noqa: E402
from diracdata.memory.results import ResultStore  # noqa: E402
from diracdata.utils.object_store import LocalObjectStore  # noqa: E402


class ReconcilerCombineTests(unittest.TestCase):
    def setUp(self):
        self._tmp, self.engine = make_duckdb_source()
        self._sdir = tempfile.TemporaryDirectory()
        self.rs = ResultStore(engine=self.engine, store=LocalObjectStore(self._sdir.name), schema="s")

    def tearDown(self):
        self._tmp.cleanup()
        self._sdir.cleanup()

    def _run(self, sql):
        return self.rs.run(sql)["result_id"]

    def test_reconciler_is_separate_and_locked_down(self):
        self.assertIsNot(self.rs.reconciler, self.rs.engine)   # independent of any source
        rec = self.rs.reconciler
        self.assertTrue(rec.query("SELECT current_setting('memory_limit')", 1).rows[0][0])   # cap set
        self.assertTrue(rec.query("SELECT current_setting('temp_directory')", 1).rows[0][0])  # spill dir set
        # insertion-order preservation off -> large results stream/spill instead of buffering
        self.assertFalse(rec.query("SELECT current_setting('preserve_insertion_order')", 1).rows[0][0])

    def test_combine_joins_two_results(self):
        r1 = self._run("SELECT * FROM (VALUES (1,10.0),(2,20.0)) v(user_id, rev)")
        r2 = self._run("SELECT * FROM (VALUES (1,'A'),(2,'B')) v(user_id, seg)")
        env = self.rs.combine([r1, r2],
            f"SELECT {r2}.seg AS seg, SUM({r1}.rev) AS rev FROM {r1} JOIN {r2} "
            f"ON {r1}.user_id = {r2}.user_id GROUP BY seg ORDER BY seg")
        self.assertNotIn(env["result_id"], (r1, r2))          # a NEW result id
        self.assertEqual({row[0]: row[1] for row in env["preview"]}, {"A": 10.0, "B": 20.0})

    def test_asof_freshness_join(self):
        # real-time events (r1) aligned to a daily snapshot (r2) with ASOF -- no temporal double count
        r1 = self._run("SELECT * FROM (VALUES (1, TIMESTAMP '2024-01-02 09:00', 5.0),"
                       "(1, TIMESTAMP '2024-01-03 09:00', 7.0)) v(uid, ts, amt)")
        r2 = self._run("SELECT * FROM (VALUES (1, TIMESTAMP '2024-01-01', 'jan1'),"
                       "(1, TIMESTAMP '2024-01-03', 'jan3')) v(uid, valid_from, seg)")
        env = self.rs.combine([r1, r2],
            f"SELECT {r1}.ts AS ts, {r2}.seg AS seg FROM {r1} ASOF JOIN {r2} "
            f"ON {r1}.uid = {r2}.uid AND {r1}.ts >= {r2}.valid_from ORDER BY ts")
        self.assertEqual([row[1] for row in env["preview"]], ["jan1", "jan3"])

    def test_nulls_and_nested_roundtrip(self):
        r = self._run("SELECT * FROM (VALUES (1, [10,20,30], {'k':1}, NULL),"
                      "(2, [40], {'k':2}, 'x')) v(id, arr, strct, note)")
        env = self.rs.combine([r], f"SELECT * FROM {r} ORDER BY id")
        self.assertIn(None, [row[3] for row in env["preview"]])       # NULLs preserved
        self.assertEqual(list(env["preview"][0][1]), [10, 20, 30])    # LIST preserved
        self.assertEqual(dict(env["preview"][0][2]), {"k": 1})        # STRUCT preserved

    def test_large_combine_never_floods_context(self):
        # The Phase-1 guarantee: a large combine keeps the FULL result on disk (a new parquet) and
        # returns only a bounded preview -- the agent's context never sees 500k rows. (OOM *containment*
        # of a runaway op is Phase 1.5's process isolation; the spill knobs are asserted above.)
        r = self._run("SELECT i AS id, (i % 5000) AS grp FROM range(500000) t(i)")
        env = self.rs.combine([r], f"SELECT grp, COUNT(*) AS n FROM {r} GROUP BY grp")
        self.assertEqual(env["row_count"], 5000)          # full result: 5000 groups
        self.assertEqual(sum(row[0] for row in self.rs.query(env["result_id"],
                            "SELECT n FROM result", max_rows=5000)["rows"]), 500000)  # totals faithful
        self.assertLessEqual(len(env["preview"]), 100)    # ...but context sees only a bounded preview
        self.assertTrue(env["truncated"])


class CombineToolFaithfulnessTests(unittest.TestCase):
    def setUp(self):
        from diracdata.memory.working_memory import WorkingMemory
        from diracdata.tools.query import build_query_tools
        self._tmp, self.engine = make_duckdb_source()
        self._sdir = tempfile.TemporaryDirectory()
        self.memory = WorkingMemory(goal="combine")
        rs = ResultStore(engine=self.engine, store=LocalObjectStore(self._sdir.name), schema="s")
        self.tools = {t.name: t for t in build_query_tools(
            engine=self.engine, result_store=rs, memory=self.memory)}

    def tearDown(self):
        self._tmp.cleanup()
        self._sdir.cleanup()

    def _run_sql(self, sql):
        return json.loads(str(self.tools["run_sql"].invoke({"sql": sql})))["result_id"]

    def test_combine_tool_registers_faithful_number(self):
        r1 = self._run_sql("SELECT * FROM (VALUES (1,10.0),(2,20.0)) v(user_id, rev)")
        r2 = self._run_sql("SELECT * FROM (VALUES (1,'A'),(2,'B')) v(user_id, seg)")
        out = json.loads(str(self.tools["combine_results"].invoke({
            "result_ids": [r1, r2],
            "sql": f"SELECT SUM({r1}.rev) AS total FROM {r1} JOIN {r2} ON {r1}.user_id={r2}.user_id"})))
        self.assertIn(out["result_id"], self.memory.results)   # combined result is in the index
        self.assertIn(30.0, self.memory.seen_numbers)          # its number is faithful (citable)

    def test_combine_tool_rejects_unknown_result_id(self):
        out = str(self.tools["combine_results"].invoke({"result_ids": ["rX"], "sql": "SELECT 1 FROM rX"}))
        self.assertIn("No such result_id", out)


if __name__ == "__main__":
    unittest.main()
