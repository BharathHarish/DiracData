"""Phase 2a: stream modes filter what reaches the display sink. Same event stream; the mode picks the
allowed sink kinds. mode_sink wraps a base sink at one point and governs every kind."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.streaming.modes import StreamMode, coerce_mode, mode_sink  # noqa: E402


def _capture():
    seen = []
    return seen, (lambda stage, kind, text: seen.append((kind, text)))


ALL_KINDS = [("token", "hi"), ("reasoning", "thinking"), ("tool_call", "run_sql(...)"),
             ("tool_result", "r1: 1 row"), ("info", "framing"), ("usage", "in=10 out=5")]


class ModeSinkTests(unittest.TestCase):
    def _kinds_through(self, mode):
        seen, base = _capture()
        s = mode_sink(base, mode)
        for kind, text in ALL_KINDS:
            s("analyst", kind, text)
        return [k for k, _ in seen]

    def test_off_shows_nothing(self) -> None:
        self.assertEqual(self._kinds_through(StreamMode.OFF), [])

    def test_messages_shows_answer_and_tools_not_reasoning(self) -> None:
        k = self._kinds_through(StreamMode.MESSAGES)
        self.assertIn("token", k)
        self.assertIn("tool_call", k)
        self.assertIn("tool_result", k)
        self.assertNotIn("reasoning", k)      # reasoning hidden in messages
        self.assertNotIn("usage", k)

    def test_updates_hides_token_shows_progress(self) -> None:
        k = self._kinds_through(StreamMode.UPDATES)
        self.assertNotIn("token", k)          # no token spam
        self.assertIn("info", k)
        self.assertIn("tool_call", k)

    def test_all_shows_everything(self) -> None:
        k = self._kinds_through(StreamMode.ALL)
        for kind in ("token", "reasoning", "tool_call", "tool_result", "info", "usage"):
            self.assertIn(kind, k)

    def test_coerce_unknown_defaults_to_messages(self) -> None:
        self.assertEqual(coerce_mode("bogus"), StreamMode.MESSAGES)
        self.assertEqual(coerce_mode("ALL"), StreamMode.ALL)     # case-insensitive
        self.assertEqual(coerce_mode(None), StreamMode.MESSAGES)


if __name__ == "__main__":
    unittest.main()
