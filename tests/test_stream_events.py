"""Phase 1a: the canonical event taxonomy -- immutable events, text accessor, usage totals."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.streaming.events import EventType, StreamEvent, Usage  # noqa: E402


class EventTests(unittest.TestCase):
    def test_answer_delta_text_accessor(self) -> None:
        e = StreamEvent(EventType.ANSWER_DELTA, seq=1, data={"text": "hi"}, phase="analyst")
        self.assertEqual(e.text, "hi")
        self.assertEqual(e.type, EventType.ANSWER_DELTA)
        self.assertEqual(e.phase, "analyst")

    def test_event_is_frozen(self) -> None:
        e = StreamEvent(EventType.RUN_START, seq=0)
        with self.assertRaises(Exception):
            e.seq = 5  # type: ignore[misc]

    def test_usage_total(self) -> None:
        u = Usage(input_tokens=10, output_tokens=5, reasoning_tokens=3)
        self.assertEqual(u.total_tokens, 15)   # total = input + output (reasoning tracked separately)

    def test_default_text_is_empty(self) -> None:
        self.assertEqual(StreamEvent(EventType.RUN_END, seq=9).text, "")


if __name__ == "__main__":
    unittest.main()
