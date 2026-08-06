"""Anthropic prompt caching: the stable system+tools prefix is marked with cache_control, split from
the mutating WORKING-MEMORY tail so the cache actually hits across turns. Non-anthropic is untouched."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from diracdata.streaming.collector import _cache_anthropic_prefix  # noqa: E402


class PromptCacheTests(unittest.TestCase):
    def test_splits_stable_head_from_working_memory_tail(self):
        sys_text = "You are an analyst. Rules...\n\n## WORKING MEMORY (authoritative)\nGOAL: x\nRESULTS: r1"
        out = _cache_anthropic_prefix([SystemMessage(content=sys_text), HumanMessage(content="hi")])
        blocks = out[0].content
        self.assertIsInstance(blocks, list)
        self.assertEqual(blocks[0]["cache_control"], {"type": "ephemeral"})   # stable head cached
        self.assertIn("You are an analyst", blocks[0]["text"])
        self.assertNotIn("WORKING MEMORY", blocks[0]["text"])                 # ...and it stops before the tail
        self.assertNotIn("cache_control", blocks[1])                          # mutating tail NOT cached
        self.assertIn("WORKING MEMORY", blocks[1]["text"])

    def test_no_working_memory_caches_whole_system(self):
        out = _cache_anthropic_prefix([SystemMessage(content="Verify this answer."), HumanMessage(content="x")])
        self.assertEqual(out[0].content[0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(out[0].content[0]["text"], "Verify this answer.")

    def test_does_not_mutate_caller_messages(self):
        orig = SystemMessage(content="sys\n\n## WORKING MEMORY\nx")
        msgs = [orig, HumanMessage(content="h")]
        _cache_anthropic_prefix(msgs)
        self.assertEqual(orig.content, "sys\n\n## WORKING MEMORY\nx")          # original untouched (new list)


if __name__ == "__main__":
    unittest.main()
