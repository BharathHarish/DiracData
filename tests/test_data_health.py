"""T2 data-sanity layer: the one-pass probe, the object-store snapshot history (keep N), drift
detection vs the previous snapshot, and the two agent tools (data_health / read_dq_history).

Uses the self-contained DuckDB parquet fixture (3 rows, one NULL, a numeric + a categorical column) --
no external services. The live-Postgres case (TO-T2-09) lives in test_multi_engine_live.
"""

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
from diracdata.quality import DQHistory, detect_drift, probe_table  # noqa: E402
from diracdata.quality.probe import _kind  # noqa: E402
from diracdata.tools.quality import build_quality_tools  # noqa: E402
from diracdata.utils.object_store import LocalObjectStore  # noqa: E402


class ProbeTests(unittest.TestCase):
    """TO-T2-01: one type-aware pass -> per-column facts; complex types stay counts-only."""

    def setUp(self):
        self.tmp, self.eng = make_duckdb_source()

    def tearDown(self):
        self.tmp.cleanup()

    def test_probe_is_type_aware_in_one_pass(self):
        snap = probe_table(self.eng, "t", ["user_id", "seg", "amount", "note"])
        self.assertEqual(snap["row_count"], 3)
        # numeric amount -> range + mean
        amt = snap["columns"]["amount"]
        self.assertEqual((amt["min"], amt["max"]), (10.0, 30.0))
        self.assertAlmostEqual(amt["avg"], 20.0)
        self.assertEqual(amt["null_pct"], 0.0)
        # categorical seg -> distinct + null%, two distinct values ('a','b')
        self.assertEqual(snap["columns"]["seg"]["distinct"], 2)
        # note has one NULL of three rows -> ~33.33% null
        self.assertAlmostEqual(snap["columns"]["note"]["null_pct"], 33.33, places=1)

    def test_kind_gating_never_min_maxes_complex_types(self):
        self.assertEqual(_kind("BIGINT"), "numeric")
        self.assertEqual(_kind("DOUBLE"), "numeric")
        self.assertEqual(_kind("TIMESTAMP WITH TIME ZONE"), "temporal")
        self.assertEqual(_kind("DATE"), "temporal")
        self.assertEqual(_kind("jsonb"), "complex")
        self.assertEqual(_kind("VARCHAR[]"), "complex")
        self.assertEqual(_kind("VARCHAR"), "text")

    def test_probe_json_serializable(self):
        # snapshots must round-trip through the JSONL history unchanged
        snap = probe_table(self.eng, "t")
        self.assertEqual(json.loads(json.dumps(snap, default=str))["row_count"], 3)


class HistoryTests(unittest.TestCase):
    """TO-T2-02: append + trim to keep=N; read returns the retained series in order."""

    def test_append_trims_to_keep_and_reads_back(self):
        with tempfile.TemporaryDirectory() as sd:
            hist = DQHistory(LocalObjectStore(sd), schema="s", keep=20)
            for i in range(23):
                hist.append("src", "t", {"run_ts": f"r{i}", "row_count": i})
            series = hist.read("src", "t")
            self.assertEqual(len(series), 20)                       # only the last 20 kept
            self.assertEqual(series[0]["run_ts"], "r3")             # r0..r2 trimmed off the front
            self.assertEqual(series[-1]["run_ts"], "r22")           # newest last

    def test_read_empty_is_empty(self):
        with tempfile.TemporaryDirectory() as sd:
            self.assertEqual(DQHistory(LocalObjectStore(sd), schema="s", keep=5).read("src", "t"), [])


class DriftTests(unittest.TestCase):
    """TO-T2-03 / TO-T2-06: evidence vs the previous snapshot; empty history -> baseline note."""

    def _snap(self, *, rows, null_pct, distinct, avg):
        return {"row_count": rows,
                "columns": {"amount": {"null_pct": null_pct, "distinct": distinct, "avg": avg,
                                       "min": 0.0, "max": avg * 2}}}

    def test_first_run_is_baseline_not_false_drift(self):
        out = detect_drift(self._snap(rows=100, null_pct=0, distinct=100, avg=20), [],
                           drift_pct=20.0, null_delta=5.0)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "baseline")

    def test_flags_null_spike_and_row_drop_and_mean_shift(self):
        prev = self._snap(rows=1000, null_pct=1.0, distinct=900, avg=20.0)
        cur = self._snap(rows=500, null_pct=30.0, distinct=900, avg=50.0)     # half the rows, null spike, mean 2.5x
        kinds = {f["kind"] for f in detect_drift(cur, [prev], drift_pct=20.0, null_delta=5.0)}
        self.assertIn("row_count", kinds)
        self.assertIn("null_pct", kinds)
        self.assertIn("avg", kinds)

    def test_stable_data_reports_no_drift(self):
        prev = self._snap(rows=1000, null_pct=1.0, distinct=900, avg=20.0)
        cur = self._snap(rows=1010, null_pct=1.2, distinct=905, avg=20.1)     # all within tolerance
        self.assertEqual(detect_drift(cur, [prev], drift_pct=20.0, null_delta=5.0), [])


class ToolTests(unittest.TestCase):
    """TO-T2-04 / TO-T2-05: data_health probes fresh + drifts + appends; read_dq_history returns series."""

    def setUp(self):
        self.tmp, self.eng = make_duckdb_source()
        self.sd = tempfile.TemporaryDirectory()
        tools = build_quality_tools(engine=self.eng, store=LocalObjectStore(self.sd.name),
                                    schema="s", sources=None, config=Config())
        self.tools = {t.name: t for t in tools}

    def tearDown(self):
        self.tmp.cleanup()
        self.sd.cleanup()

    def test_data_health_records_then_drifts_on_second_call(self):
        first = json.loads(self.tools["data_health"].invoke({"table": "t", "columns": ["amount"]}))
        self.assertEqual(first["history_len"], 1)
        self.assertEqual(first["drift"][0]["kind"], "baseline")           # first run: no history
        self.assertEqual(first["snapshot"]["columns"]["amount"]["min"], 10.0)
        # a second call runs a FRESH probe (no reuse-cache) and appends -> history grows
        second = json.loads(self.tools["data_health"].invoke({"table": "t", "columns": ["amount"]}))
        self.assertEqual(second["history_len"], 2)

    def test_read_dq_history_returns_the_series(self):
        self.assertIn("No DQ history yet", str(self.tools["read_dq_history"].invoke({"table": "t"})))
        self.tools["data_health"].invoke({"table": "t", "columns": ["amount"]})
        self.tools["data_health"].invoke({"table": "t", "columns": ["amount"]})
        series = json.loads(self.tools["read_dq_history"].invoke({"table": "t"}))
        self.assertEqual(len(series["snapshots"]), 2)                     # both recorded probes visible
        self.assertEqual(series["table"], "t")

    def test_unknown_table_is_reported_not_raised(self):
        self.assertIn("No such table", str(self.tools["data_health"].invoke({"table": "nope"})))

    def test_probe_numbers_are_registered_faithful(self):
        # a probe runs real SQL, so its measured numbers must be citable without the finish gate
        # flagging them (regression: DQ facts in an RCA answer were rejected as unfaithful).
        from diracdata.runtime.working_memory import WorkingMemory
        mem = WorkingMemory(goal="dq")
        tools = {t.name: t for t in build_quality_tools(
            engine=self.eng, store=LocalObjectStore(tempfile.mkdtemp()), schema="s",
            sources=None, memory=mem, config=Config())}
        tools["data_health"].invoke({"table": "t", "columns": ["amount"]})
        self.assertIn(3.0, mem.seen_numbers)                     # row_count = 3 registered
        self.assertIn(30.0, mem.seen_numbers)                    # amount max = 30.0 registered

    def test_data_health_writes_a_ledger_fact_for_the_sanity_gate(self):
        # regression: the SANITY gate re-demanded a probe that had already run because the check was
        # invisible to it. data_health must record a DQ-ledger fact so the gate SEES the table was probed.
        from diracdata.runtime.working_memory import WorkingMemory
        mem = WorkingMemory(goal="dq")
        tools = {t.name: t for t in build_quality_tools(
            engine=self.eng, store=LocalObjectStore(tempfile.mkdtemp()), schema="s",
            sources=None, memory=mem, config=Config())}
        tools["data_health"].invoke({"table": "t", "columns": ["amount"]})
        ledger = [f for f in mem.facts if f.startswith("data_health[")]
        self.assertTrue(ledger, "expected a data_health[...] ledger fact in working memory")
        self.assertIn("t]", ledger[0])                            # names the probed table
        self.assertIn("rows", ledger[0])                          # carries the row count / shape


if __name__ == "__main__":
    unittest.main()
