"""Phase 2b: the Collector emits token/reasoning/usage kinds, and a mode-wrapped sink governs what is
shown -- reasoning is hidden in MESSAGES/OFF and shown in ALL, with the answer identical in every mode.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from langchain_core.messages import AIMessageChunk  # noqa: E402

from diracdata.streaming import Collector, StreamMode, mode_sink  # noqa: E402


class _StreamModel:
    def __init__(self, chunks):
        self._chunks = chunks

    def stream(self, messages):
        yield from self._chunks


def _chunks():
    return [
        AIMessageChunk(content=[{"type": "reasoning", "reasoning": "let me think about it"}]),
        AIMessageChunk(content="The answer is 100,000."),
        AIMessageChunk(content="", usage_metadata={"input_tokens": 10, "output_tokens": 5,
                                                   "total_tokens": 15}),
    ]


def _run_with_mode(mode):
    seen = []
    sink = mode_sink(lambda s, k, t: seen.append((k, t)), mode)
    res = Collector().run(model=_StreamModel(_chunks()), messages=[], stage="analyst", sink=sink)
    return res, seen


class ModeWiringTests(unittest.TestCase):
    def test_messages_hides_reasoning_and_usage(self) -> None:
        res, seen = _run_with_mode(StreamMode.MESSAGES)
        kinds = [k for k, _ in seen]
        self.assertIn("token", kinds)
        self.assertNotIn("reasoning", kinds)      # hidden
        self.assertNotIn("usage", kinds)
        self.assertEqual(res.answer, "The answer is 100,000.")   # answer intact

    def test_all_shows_reasoning_and_usage(self) -> None:
        res, seen = _run_with_mode(StreamMode.ALL)
        kinds = [k for k, _ in seen]
        self.assertIn("token", kinds)
        self.assertIn("reasoning", kinds)         # shown as its own kind
        self.assertIn("usage", kinds)
        self.assertEqual(res.answer, "The answer is 100,000.")   # answer identical to messages mode

    def test_off_shows_nothing_but_answer_still_collected(self) -> None:
        res, seen = _run_with_mode(StreamMode.OFF)
        self.assertEqual(seen, [])
        self.assertEqual(res.answer, "The answer is 100,000.")   # final answer unaffected by display mode

    def test_answer_identical_across_modes(self) -> None:
        answers = {m: _run_with_mode(m)[0].answer for m in StreamMode}
        self.assertEqual(len(set(answers.values())), 1)          # display mode never changes the answer


if __name__ == "__main__":
    unittest.main()
