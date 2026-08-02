"""Phase 3: multi-source routing. run_sql(source=...) picks the right engine + echoes its dialect;
the default source is used when source is omitted (back-compat); the estate map renders every source;
and results from two different sources combine. Self-contained: two DuckDB sources, no live model.
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
from diracdata.context.estate import render_estate  # noqa: E402
from diracdata.engines import EngineSpec, SourceRegistry  # noqa: E402
from diracdata.memory.results import ResultStore  # noqa: E402
from diracdata.memory.working_memory import WorkingMemory  # noqa: E402
from diracdata.tools.query import build_query_tools  # noqa: E402
from diracdata.utils.object_store import LocalObjectStore  # noqa: E402


class MultiSourceTests(unittest.TestCase):
    def setUp(self):
        self._t1, _ = make_duckdb_source(schema="s1", table="orders")
        self._t2, _ = make_duckdb_source(schema="s2", table="customers")
        self._sdir = tempfile.TemporaryDirectory()
        self.reg = SourceRegistry([
            EngineSpec(name="a", kind="duckdb", data_root=Path(self._t1.name), schema="s1"),
            EngineSpec(name="b", kind="duckdb", data_root=Path(self._t2.name), schema="s2"),
        ])
        self.memory = WorkingMemory(goal="x")
        self.rs = ResultStore(engine=self.reg.get("a"), store=LocalObjectStore(self._sdir.name),
                              schema="est", sources=self.reg)
        self.tools = {t.name: t for t in build_query_tools(
            engine=self.reg.get("a"), result_store=self.rs, memory=self.memory, sources=self.reg)}

    def tearDown(self):
        self._t1.cleanup()
        self._t2.cleanup()
        self._sdir.cleanup()

    def _run(self, sql, source=None):
        args = {"sql": sql} if source is None else {"sql": sql, "source": source}
        return json.loads(str(self.tools["run_sql"].invoke(args)))

    def test_run_sql_routes_to_named_source(self):
        out = self._run("SELECT COUNT(*) n FROM customers", source="b")
        self.assertEqual(out["source"], "b")          # ran on source b
        self.assertEqual(out["dialect"], "duckdb")    # echoes the dialect used

    def test_run_sql_without_source_uses_default(self):
        out = self._run("SELECT COUNT(*) n FROM orders")   # default source a has `orders`
        self.assertEqual(out["source"], "a")
        self.assertEqual(out["preview"][0][0], 3)

    def test_wrong_table_for_source_is_rejected(self):
        out = str(self.tools["run_sql"].invoke({"sql": "SELECT * FROM customers", "source": "a"}))
        self.assertIn("rejected", out.lower())        # `customers` isn't in source a

    def test_estate_map_lists_every_source(self):
        md = render_estate(self.reg, default_name="a")
        self.assertIn("orders(", md)
        self.assertIn("customers(", md)
        self.assertIn("[duckdb]", md)
        self.assertIn("(default)", md)

    def test_combine_across_two_sources(self):
        r1 = self._run("SELECT user_id, amount FROM orders", source="a")["result_id"]
        r2 = self._run("SELECT user_id, seg FROM customers", source="b")["result_id"]
        out = json.loads(str(self.tools["combine_results"].invoke({
            "result_ids": [r1, r2],
            "sql": f"SELECT {r2}.seg AS seg, SUM({r1}.amount) AS amt FROM {r1} JOIN {r2} "
                   f"ON {r1}.user_id = {r2}.user_id GROUP BY seg ORDER BY seg"})))
        self.assertEqual({row[0]: float(row[1]) for row in out["preview"]}, {"a": 40.0, "b": 20.0})


if __name__ == "__main__":
    unittest.main()
