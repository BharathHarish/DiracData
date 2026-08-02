"""Phase 1c: the Collector folds a stream into a clean answer (reasoning removed), separate reasoning,
tool calls, and usage -- and preserves answer-token streaming to the sink WITHOUT streaming reasoning.
A non-streaming model falls back to a single invoke with the same result."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from langchain_core.messages import AIMessage, AIMessageChunk  # noqa: E402

from diracdata.streaming.collector import Collector  # noqa: E402


class _StreamModel:
    def __init__(self, chunks):
        self._chunks = chunks

    def stream(self, messages):
        yield from self._chunks


class _NoStreamModel:
    """`.stream` raises -> Collector must fall back to `.invoke`."""
    def __init__(self, message):
        self._message = message

    def stream(self, messages):
        raise RuntimeError("streaming unsupported")

    def invoke(self, messages):
        return self._message


class CollectorTests(unittest.TestCase):
    def test_reasoning_separated_from_answer(self) -> None:
        chunks = [
            AIMessageChunk(content=[{"type": "reasoning", "reasoning": "the model is thinking..."}]),
            AIMessageChunk(content="The answer is 100,000."),
        ]
        seen = []
        res = Collector().run(model=_StreamModel(chunks), messages=[], stage="analyst",
                              sink=lambda s, k, t: seen.append((k, t)))
        self.assertEqual(res.answer, "The answer is 100,000.")   # clean
        self.assertIn("thinking", res.reasoning)                 # kept separate
        # only the answer was streamed to the sink -- reasoning never is
        streamed = "".join(t for k, t in seen if k == "token")
        self.assertEqual(streamed, "The answer is 100,000.")
        self.assertNotIn("thinking", streamed)

    def test_tool_calls_from_gathered(self) -> None:
        chunks = [
            AIMessageChunk(content="", tool_call_chunks=[
                {"name": "run_sql", "args": '{"sql":"SELECT 1"}', "id": "c1", "index": 0,
                 "type": "tool_call_chunk"}]),
        ]
        res = Collector().run(model=_StreamModel(chunks), messages=[], stage="analyst")
        self.assertEqual(len(res.tool_calls), 1)
        self.assertEqual(res.tool_calls[0]["name"], "run_sql")
        self.assertEqual(res.tool_calls[0]["args"], {"sql": "SELECT 1"})
        self.assertIsNotNone(res.message)                        # message present for the loop

    def test_usage_and_tokens(self) -> None:
        chunk = AIMessageChunk(content="hi", usage_metadata={
            "input_tokens": 20, "output_tokens": 8, "total_tokens": 28})
        res = Collector().run(model=_StreamModel([chunk]), messages=[])
        self.assertEqual(res.tokens, 28)
        self.assertEqual(res.usage.input_tokens, 20)

    def test_non_streaming_fallback(self) -> None:
        msg = AIMessage(content="fallback answer")
        res = Collector().run(model=_NoStreamModel(msg), messages=[])
        self.assertEqual(res.answer, "fallback answer")
        self.assertIs(res.message, msg)

    def test_text_property_is_drop_in(self) -> None:
        res = Collector().run(model=_StreamModel([AIMessageChunk(content="x")]), messages=[])
        self.assertEqual(res.text, res.answer)   # legacy {text} shape


if __name__ == "__main__":
    unittest.main()
