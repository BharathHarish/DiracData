"""Subagents: the spawn tool merges a sub's distilled result (result_ids + numbers + facts) back
into the parent so the parent can cite and re-verify them; tokens propagate. The heavy sub-loop is
stubbed so this stays a fast, deterministic unit test of the contract.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import tempfile  # noqa: E402

import diracdata.agents.subagents as subagents  # noqa: E402
from _fabric import DATA_PRESENT, SCHEMA, engine  # noqa: E402
from diracdata.memory.working_memory import WorkingMemory  # noqa: E402


class SpawnMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = subagents.run_subagent

    def tearDown(self) -> None:
        subagents.run_subagent = self._orig

    def _tools(self, parent, on_tokens=None):
        tools = subagents.build_subagent_tool(
            model=None, workspace=None, engine=None, result_store=None, value_cache=None,
            parent_memory=parent, system_prompt="p", sink=lambda *a: None, asker=None,
            max_steps=8, depth=0, max_depth=1, on_tokens=on_tokens)
        return {t.name: t for t in tools}   # now returns [spawn_subagent, spawn_subagents]

    def _tool(self, parent, on_tokens=None):
        return self._tools(parent, on_tokens)["spawn_subagent"]

    def test_merges_sub_results_numbers_and_facts(self) -> None:
        parent = WorkingMemory(goal="AZ then CA")
        subagents.run_subagent = lambda **kw: {
            "answer": "AZ: 5 customers", "result_ids": ["r7"],
            "results": {"r7": {"columns": ["n"], "row_count": 1, "sql": "SELECT ..."}},
            "seen_numbers": [5.0], "facts": ["AZ bound to state='AZ'"], "verdict": {"ok": True},
            "tokens": 4200}
        out = json.loads(str(self._tool(parent).invoke({"task": "count AZ", "context": ""})))
        self.assertEqual(out["result_ids"], ["r7"])
        self.assertIn("r7", parent.results)                  # citable in the parent's finish gate
        self.assertIn(5.0, parent.seen_numbers)              # faithful in the parent's answer
        self.assertTrue(any("AZ bound" in f for f in parent.facts))

    def test_passes_confirmed_intent_and_task_through(self) -> None:
        parent = WorkingMemory(goal="g", confirmed_intent={"intent": "female buyers", "concepts": []})
        seen = {}
        subagents.run_subagent = lambda **kw: seen.update(kw) or {
            "answer": "x", "result_ids": [], "results": {}, "seen_numbers": [], "facts": [],
            "verdict": None, "tokens": 0}
        self._tool(parent).invoke({"task": "count CA", "context": "gender=F"})
        self.assertEqual(seen["task"], "count CA")
        self.assertIn("gender=F", seen["context"])            # caller context preserved

    def test_m3_hands_off_parent_context_so_subagents_dont_re_derive(self) -> None:
        # M3: a sub-agent auto-inherits the parent's confirmed intent + DQ ledger + resolved bindings,
        # told to REUSE them and not re-probe -- the fix for sub-agents re-running data_health/define.
        parent = WorkingMemory(goal="rca", confirmed_intent={"intent": "why revenue fell", "concepts": []})
        parent.add_fact("data_health[retail.online_purchases]: 719384 rows; billing_client_ref null 0.02%; no drift")
        parent.add_fact("online_revenue = SUM(online_purchases.net_paid)")
        seen = {}
        subagents.run_subagent = lambda **kw: seen.update(kw) or {
            "answer": "x", "result_ids": [], "results": {}, "seen_numbers": [], "facts": [],
            "verdict": None, "tokens": 0}
        self._tool(parent).invoke({"task": "slice by category", "context": "category dimension"})
        ctx = seen["context"]
        self.assertIn("why revenue fell", ctx)                # inherits framed intent (no re-frame)
        self.assertIn("data_health[retail.online_purchases]", ctx)   # inherits DQ ledger (no re-probe)
        self.assertIn("do NOT re-run data_health", ctx)
        self.assertIn("online_revenue = SUM", ctx)            # inherits bindings (no re-derive)
        self.assertIn("category dimension", ctx)              # ...and still carries the caller's task context
        self.assertEqual(seen["confirmed_intent"], parent.confirmed_intent)  # inherits framed meaning
        self.assertEqual(seen["depth"], 1)                   # main is depth 0 -> child runs at depth 1

    def test_tokens_propagate_to_parent(self) -> None:
        parent = WorkingMemory(goal="g")
        subagents.run_subagent = lambda **kw: {
            "answer": "x", "result_ids": [], "results": {}, "seen_numbers": [], "facts": [],
            "verdict": None, "tokens": 4200}
        acc: list[int] = []
        self._tool(parent, on_tokens=acc.append).invoke({"task": "t", "context": ""})
        self.assertEqual(acc, [4200])

    def test_spawn_subagents_runs_all_branches_and_merges(self) -> None:
        parent = WorkingMemory(goal="A and B")
        subagents.run_subagent = lambda **kw: {
            "answer": f"{kw['task']}: done", "result_ids": [f"r_{kw['task']}"],
            "results": {f"r_{kw['task']}": {"row_count": 1}}, "seen_numbers": [1.0],
            "facts": [f"fact for {kw['task']}"], "verdict": {"ok": True}, "tokens": 100}
        out = json.loads(str(self._tools(parent)["spawn_subagents"].invoke(
            {"tasks": [{"task": "A"}, {"task": "B", "context": "c"}]})))
        self.assertEqual(len(out), 2)                                   # both branches ran
        self.assertIn("r_A", parent.results)                           # both result indexes merged
        self.assertIn("r_B", parent.results)
        self.assertTrue(any("fact for A" in f for f in parent.facts))  # both fact sets merged
        self.assertTrue(any("fact for B" in f for f in parent.facts))

    def test_spawn_subagents_rejects_empty(self) -> None:
        parent = WorkingMemory(goal="g")
        self.assertIn("needs", str(self._tools(parent)["spawn_subagents"].invoke({"tasks": []})))


class ParallelPrimitiveTests(unittest.TestCase):
    def test_run_parallel_is_concurrent_and_bounded(self) -> None:
        import time
        from diracdata.agents.subagents import _run_parallel

        def sleepy():
            time.sleep(0.2)
            return 1

        t0 = time.perf_counter()
        self.assertEqual(_run_parallel([sleepy, sleepy, sleepy], max_workers=3), [1, 1, 1])
        concurrent = time.perf_counter() - t0
        t0 = time.perf_counter()
        _run_parallel([sleepy, sleepy, sleepy], max_workers=1)          # bounded to one at a time
        serial = time.perf_counter() - t0
        self.assertLess(concurrent, 0.45)                              # 3 slots run as ~1
        self.assertGreater(serial, 0.55)                               # ...vs ~3 when bounded to 1

    def test_concurrent_result_ids_are_distinct(self) -> None:
        import tempfile
        import threading
        from _engine_fixture import make_duckdb_source
        from diracdata.memory.results import ResultStore
        from diracdata.utils.object_store import LocalObjectStore

        tmp, eng = make_duckdb_source()
        try:
            with tempfile.TemporaryDirectory() as sd:
                rs = ResultStore(engine=eng, store=LocalObjectStore(sd), schema="s")
                ids: list[str] = []
                lock = threading.Lock()

                def one():
                    rid = rs.run("SELECT 1 AS x")["result_id"]
                    with lock:
                        ids.append(rid)

                threads = [threading.Thread(target=one) for _ in range(20)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                self.assertEqual(len(ids), 20)
                self.assertEqual(len(set(ids)), 20)                    # no _seq race -> all unique
        finally:
            tmp.cleanup()


class _FakeWorkspace:
    semantic_layer = None


class _ScriptedModel:
    def __init__(self, steps):
        self._steps = list(steps)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        s = self._steps.pop(0) if self._steps else {"content": "{}"}
        return AIMessage(content=s.get("content", ""), tool_calls=s.get("tool_calls", []))


@unittest.skipUnless(DATA_PRESENT, "retail parquet data not present")
class RunSubagentEndToEndTests(unittest.TestCase):
    """A real sub-loop: it runs an actual query against the DB, passes its own finish gate + verify,
    and returns a citable result_id -- driven by a scripted model (no live LLM)."""

    def test_subagent_runs_a_real_query_and_returns_result_id(self) -> None:
        from diracdata.utils.object_store import LocalObjectStore
        from diracdata.memory.results import ResultStore
        with tempfile.TemporaryDirectory() as tmp:
            rs = ResultStore(engine=engine(), store=LocalObjectStore(tmp), schema=SCHEMA)
            model = _ScriptedModel([
                {"tool_calls": [{"name": "run_sql", "id": "c1",
                                 "args": {"sql": "SELECT COUNT(*) AS n FROM merchandise"}}]},
                {"tool_calls": [{"name": "finish", "id": "c2",
                                 "args": {"answer": "There are 18000 merchandise items.",
                                          "result_ids": ["r1"]}}]},
                {"content": '{"ok": true, "reason": "correct", "ambiguity": false}'},
            ])
            res = subagents.run_subagent(
                task="how many merchandise items are there?", context="", model=model,
                workspace=_FakeWorkspace(), engine=engine(), result_store=rs, value_cache=None,
                confirmed_intent={}, system_prompt="You are an analyst.", sink=lambda *a: None,
                asker=None, max_steps=6, depth=1, max_depth=1)
            self.assertEqual(res["result_ids"], ["r1"])       # produced a citable result
            self.assertIn("18000", res["answer"])             # its gated answer
            self.assertTrue(res["verdict"]["ok"])             # passed its own verify
            self.assertIn(18000.0, res["seen_numbers"])       # number is faithful


if __name__ == "__main__":
    unittest.main()
