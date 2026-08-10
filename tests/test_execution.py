"""Phase 1.5a: the Executor seam + bounded inline backend. An over-budget combine OOMs into a CLEAN
tool error (the agent process survives and keeps working); a hang past the timeout is interrupted;
inline is the default backend."""

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
from diracdata.config import Config  # noqa: E402
from diracdata.engines.duckdb import Reconciler  # noqa: E402
from diracdata.execution import InlineExecutor, make_executor  # noqa: E402
from diracdata.runtime.results import ResultStore  # noqa: E402
from diracdata.runtime.working_memory import WorkingMemory  # noqa: E402
from diracdata.tools.query import build_query_tools  # noqa: E402
from diracdata.utils.object_store import LocalObjectStore  # noqa: E402


class InlineExecutorTests(unittest.TestCase):
    def test_default_executor_is_inline(self):
        self.assertIsInstance(make_executor(Config()), InlineExecutor)   # inline is the default

    def test_no_timeout_runs_to_completion(self):
        rec = Reconciler(memory_limit="2GB")
        out = str(Path(tempfile.mkdtemp()) / "o.parquet")
        n = InlineExecutor().run(rec, lambda: rec.copy_to_parquet("SELECT * FROM range(1000)", out))
        self.assertEqual(n, 1000)

    def test_timeout_interrupts_a_hang(self):
        rec = Reconciler(memory_limit="2GB")
        out = str(Path(tempfile.mkdtemp()) / "o.parquet")
        with self.assertRaises(Exception):   # interrupted -> raises, never hangs the caller
            InlineExecutor(job_timeout_s=0.05).run(rec, lambda: rec.copy_to_parquet(
                "SELECT COUNT(*) FROM (SELECT i FROM range(40000000) t(i) ORDER BY hash(i))", out))


class OomSurvivalTests(unittest.TestCase):
    """ME-P15-01 as a composition: (1) a real reconciler OOM is a CATCHABLE exception (not a process
    crash), and (2) the combine tool converts ANY failure -- OOM included -- into a clean error string
    while the agent keeps working."""

    def setUp(self):
        self._tmp, self.engine = make_duckdb_source()
        self._sdir = tempfile.TemporaryDirectory()
        self.memory = WorkingMemory(goal="oom")
        self.rs = ResultStore(engine=self.engine, store=LocalObjectStore(self._sdir.name), schema="s")
        self.tools = {t.name: t for t in build_query_tools(
            engine=self.engine, result_store=self.rs, memory=self.memory)}

    def tearDown(self):
        self._tmp.cleanup()
        self._sdir.cleanup()

    def _run(self, sql):
        return json.loads(str(self.tools["run_sql"].invoke({"sql": sql})))["result_id"]

    def test_real_reconciler_oom_is_catchable(self):
        rec = Reconciler(memory_limit="50MB")
        big = Path(tempfile.mkdtemp()) / "big.parquet"
        self.engine.copy_to_parquet(
            "SELECT i AS id, repeat(md5(i::VARCHAR), 6) AS s FROM range(400000) t(i)", str(big))  # ~76MB
        rec.register_view("big", big.as_posix())
        with self.assertRaises(Exception) as ctx:   # a catchable OutOfMemoryException, not a crash
            InlineExecutor().run(rec, lambda: rec.copy_to_parquet(
                "SELECT COUNT(*) FROM (SELECT * FROM big ORDER BY s)",
                str(Path(tempfile.mkdtemp()) / "o.parquet")))
        self.assertIn("memory", str(ctx.exception).lower())

    def test_combine_tool_turns_failure_into_clean_error_and_agent_survives(self):
        r = self._run("SELECT * FROM (VALUES (1),(2)) v(id)")
        out = str(self.tools["combine_results"].invoke(
            {"result_ids": [r], "sql": f"SELECT nosuchcol FROM {r}"}))   # a failing combine
        self.assertIn("error", out.lower())          # clean error string, NOT a raised exception
        self.assertTrue(self._run("SELECT 1 AS x"))  # agent survives and keeps working


if __name__ == "__main__":
    unittest.main()
