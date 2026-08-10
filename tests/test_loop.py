"""The single agent loop, driven by a scripted fake model (no tokens): it calls a tool, sees the
result, then commits a final answer. Also pins the last-turn tool withdrawal (must answer).
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.harness.loop import run_loop  # noqa: E402
from diracdata.runtime.working_memory import WorkingMemory  # noqa: E402


class _ScriptedModel:
    """Emits queued AIMessages. No .stream -> stream_and_collect falls back to .invoke."""

    def __init__(self, steps):
        self._steps = list(steps)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        step = self._steps.pop(0) if self._steps else {"content": ""}
        return AIMessage(content=step.get("content", ""), tool_calls=step.get("tool_calls", []))


def _echo_tool():
    from langchain.tools import tool

    @tool("echo")
    def echo(x: str) -> str:
        """Echo the input back."""
        return f"echoed:{x}"

    return echo


class LoopTests(unittest.TestCase):
    def test_calls_tool_then_finishes(self) -> None:
        seen = []
        model = _ScriptedModel([
            {"tool_calls": [{"name": "echo", "args": {"x": "hi"}, "id": "c1"}]},
            {"content": "FINAL ANSWER: done"},
        ])
        out = run_loop(model=model, tools=[_echo_tool()], system_prompt="sys",
                       memory=WorkingMemory(goal="g"), max_steps=8,
                       observe=lambda n, a, r: seen.append((n, r)))
        self.assertIn("done", out["text"])
        self.assertEqual(out["steps"], 2)
        self.assertEqual(seen, [("echo", "echoed:hi")])   # tool actually dispatched

    def test_last_turn_withdraws_tools_and_forces_an_answer(self) -> None:
        # even if the model keeps trying to call tools, the final turn returns whatever text it has
        model = _ScriptedModel([
            {"tool_calls": [{"name": "echo", "args": {"x": "1"}, "id": "c1"}], "content": "partial"},
        ])
        out = run_loop(model=model, tools=[_echo_tool()], system_prompt="sys",
                       memory=WorkingMemory(goal="g"), max_steps=1)
        self.assertEqual(out["steps"], 1)
        self.assertEqual(out["text"], "partial")          # committed, did not loop forever

    def test_a_malformed_tool_call_is_feedback_not_a_crash(self) -> None:
        # the model calls echo with no args (missing required `x`) -> the loop must survive and
        # feed the error back, then let the model recover on the next turn
        seen = []
        model = _ScriptedModel([
            {"tool_calls": [{"name": "echo", "args": {}, "id": "c1"}]},   # invalid: missing x
            {"content": "recovered"},
        ])
        out = run_loop(model=model, tools=[_echo_tool()], system_prompt="sys",
                       memory=WorkingMemory(goal="g"), max_steps=8,
                       observe=lambda n, a, r: seen.append(r))
        self.assertEqual(out["text"], "recovered")            # did not crash; recovered
        self.assertTrue(any("errored" in r for r in seen))    # the error was fed back as an observation

    def test_finish_gate_rejects_then_accepts(self) -> None:
        # a bare-text finish is gated: first rejected (feedback appended), then the model fixes it
        from diracdata.harness.verify import FinishGate
        mem = WorkingMemory(goal="g")
        mem.seen_numbers = {52.0}
        calls = {"n": 0}

        def verifier(answer, m):
            calls["n"] += 1
            return ({"ok": calls["n"] > 1, "reason": "state the source", "ambiguity": False}, 0)

        gate = FinishGate(memory=mem, verifier=verifier)
        model = _ScriptedModel([{"content": "52 customers"}, {"content": "52 customers (from r1)"}])
        out = run_loop(model=model, tools=[_echo_tool()], system_prompt="sys", memory=mem,
                       max_steps=8, finish_gate=gate)
        self.assertEqual(calls["n"], 2)                   # first submit rejected, second accepted
        self.assertEqual(out["text"], "52 customers (from r1)")
        self.assertTrue(out["verdict"]["ok"])

    def test_budget_exhaustion_synthesizes_from_memory_not_blank(self) -> None:
        # regression (deep-RCA): the orchestrator ran out of steps without a composed answer and returned
        # BLANK, wasting the results it (and its sub-agents) had gathered. Now the loop force-composes a
        # best-effort answer from working memory on exhaustion -- never blank when results exist.
        from diracdata.harness.verify import FinishGate
        mem = WorkingMemory(goal="why did online net revenue fall")
        mem.results["r1"] = {"columns": ["decline"], "row_count": 1, "sql": "SELECT ...", "preview": [[-14600000]]}
        gate = FinishGate(memory=mem, verifier=lambda a, m: ({"ok": True, "reason": "", "ambiguity": False}, 0))
        model = _ScriptedModel([
            {"content": ""},                                                    # last turn: no finish -> synthesise
            {"content": "Online net revenue fell $14.6M. CHECKS: from r1."},    # the forced compose call
        ])
        out = run_loop(model=model, tools=[_echo_tool()], system_prompt="sys", memory=mem,
                       max_steps=1, finish_gate=gate)
        self.assertIn("$14.6M", out["text"])                       # the synthesis, not blank
        self.assertIn("Composed from working memory", out["text"]) # honest caveat
        self.assertEqual(out["result_ids"], ["r1"])

    def test_m5_dedup_skips_a_repeated_readonly_lookup(self) -> None:
        # M5 anti-churn: the same read-only lookup (define/data_health/...) with EXACT args is not
        # re-executed -- it returns a "already done" note (the 24x data_health storm on the deep RCA).
        calls = {"n": 0}
        from langchain.tools import tool

        @tool("define")
        def define(name: str) -> str:
            """Look up a term."""
            calls["n"] += 1
            return f"def:{name}"

        model = _ScriptedModel([
            {"tool_calls": [{"name": "define", "args": {"name": "revenue"}, "id": "c1"}]},
            {"tool_calls": [{"name": "define", "args": {"name": "revenue"}, "id": "c2"}]},   # exact repeat
            {"content": "done"},
        ])
        seen = []
        run_loop(model=model, tools=[define], system_prompt="s", memory=WorkingMemory(goal="g"),
                 max_steps=8, observe=lambda n, a, r: seen.append(r))
        self.assertEqual(calls["n"], 1)                            # the tool ran ONCE, not twice
        self.assertTrue(any("already done" in r.lower() for r in seen))   # repeat got the skip note

    def test_m5_stall_nudge_fires_when_no_progress(self) -> None:
        # after N steps with no new result/verified item, the loop injects a one-time STALL nudge to
        # push the agent to finish instead of churning.
        from diracdata.harness.verify import FinishGate
        from langchain.tools import tool

        @tool("get_columns")
        def get_columns(table: str) -> str:
            """List columns."""
            return "cols: a,b,c"

        from diracdata.config import Config
        mem = WorkingMemory(goal="g")
        gate = FinishGate(memory=mem, verifier=lambda a, m: ({"ok": True, "reason": "", "ambiguity": False}, 0))
        # 6 no-progress turns (varying args so dedup doesn't short-circuit), then a bare-text finish
        steps = [{"tool_calls": [{"name": "get_columns", "args": {"table": f"t{k}"}, "id": f"c{k}"}]} for k in range(6)]
        steps.append({"content": "the answer"})
        out = run_loop(model=_ScriptedModel(steps), tools=[get_columns], system_prompt="s", memory=mem,
                       max_steps=12, finish_gate=gate, config=Config(no_progress_nudge_steps=3))
        self.assertEqual(out["text"], "the answer")               # converged; the stall nudge did not break the loop

    def test_budget_exhaustion_with_no_results_is_still_blank(self) -> None:
        # nothing was computed -> there is nothing to synthesise; blank is correct (don't fabricate).
        from diracdata.harness.verify import FinishGate
        mem = WorkingMemory(goal="g")
        gate = FinishGate(memory=mem, verifier=lambda a, m: ({"ok": True, "reason": "", "ambiguity": False}, 0))
        out = run_loop(model=_ScriptedModel([{"content": ""}]), tools=[_echo_tool()], system_prompt="s",
                       memory=mem, max_steps=1, finish_gate=gate)
        self.assertEqual(out["text"], "")


if __name__ == "__main__":
    unittest.main()
