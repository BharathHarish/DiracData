"""Phase 1d: with the envelope ON, the analyst loop still drives tools correctly and the final answer
has reasoning stripped out. Scripted streaming fake model (real AIMessageChunks), no network."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from langchain_core.messages import AIMessageChunk  # noqa: E402

from diracdata.agents.loop import run_loop  # noqa: E402
from diracdata.config import Config  # noqa: E402
from diracdata.runtime.working_memory import WorkingMemory  # noqa: E402


class _ScriptedStreamModel:
    """Each .stream() call yields the next scripted list of chunks."""
    def __init__(self, scripts):
        self._scripts = list(scripts)
        self._i = 0

    def bind_tools(self, tools):
        return self

    def stream(self, messages):
        chunks = self._scripts[self._i]
        self._i += 1
        yield from chunks


def _echo_tool():
    from langchain.tools import tool

    @tool("echo")
    def echo(x: str) -> str:
        """Echo x."""
        return f"echoed:{x}"

    return echo


class LoopEnvelopeTests(unittest.TestCase):
    def test_tool_cycle_then_clean_answer_with_envelope_on(self) -> None:
        model = _ScriptedStreamModel([
            # step 0: a tool call
            [AIMessageChunk(content="", tool_call_chunks=[
                {"name": "echo", "args": '{"x": "hi"}', "id": "c1", "index": 0, "type": "tool_call_chunk"}])],
            # step 1: reasoning + final answer
            [AIMessageChunk(content=[{"type": "reasoning", "reasoning": "the model is thinking hard"}]),
             AIMessageChunk(content="FINAL ANSWER: done")],
        ])
        seen = []
        cfg = Config(stream_envelope_enabled=True)
        out = run_loop(model=model, tools=[_echo_tool()], system_prompt="sys",
                       memory=WorkingMemory(goal="g"), max_steps=8, config=cfg,
                       observe=lambda n, a, r: seen.append((n, r)))
        self.assertIn("done", out["text"])
        self.assertNotIn("thinking hard", out["text"])       # reasoning stripped from the answer
        self.assertEqual(seen, [("echo", "echoed:hi")])      # tool actually dispatched
        self.assertEqual(out["steps"], 2)

    def test_envelope_off_still_works(self) -> None:
        # same script, flag off -> legacy path; still completes
        model = _ScriptedStreamModel([
            [AIMessageChunk(content="", tool_call_chunks=[
                {"name": "echo", "args": '{"x": "hi"}', "id": "c1", "index": 0, "type": "tool_call_chunk"}])],
            [AIMessageChunk(content="FINAL ANSWER: done")],
        ])
        out = run_loop(model=model, tools=[_echo_tool()], system_prompt="sys",
                       memory=WorkingMemory(goal="g"), max_steps=8)  # default config: envelope off
        self.assertIn("done", out["text"])


if __name__ == "__main__":
    unittest.main()
