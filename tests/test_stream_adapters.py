"""Phase 1b: provider adapters translate real AIMessageChunk shapes into canonical events, keeping
reasoning SEPARATE from the answer, surfacing tool calls, and reading usage."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from langchain_core.messages import AIMessageChunk  # noqa: E402

from diracdata.streaming.adapters import (  # noqa: E402
    AnthropicAdapter, BedrockConverseAdapter, GenericAdapter, OpenAIAdapter,
    build_adapter, detect_provider,
)
from diracdata.streaming.events import EventType  # noqa: E402


def drive(adapter, chunks):
    events = []
    for c in chunks:
        events += adapter.translate(c)
    events += adapter.finalize()
    return events


def answer_of(events):
    return "".join(e.text for e in events if e.type == EventType.ANSWER_DELTA)


def reasoning_of(events):
    return "".join(e.text for e in events if e.type == EventType.REASONING_DELTA)


def tool_ends(events):
    return [(e.data.get("name"), e.data.get("args")) for e in events if e.type == EventType.TOOL_CALL_END]


class ContentSplitTests(unittest.TestCase):
    def test_anthropic_thinking_block_is_reasoning_not_answer(self) -> None:
        chunks = [
            AIMessageChunk(content=[{"type": "thinking", "thinking": "let me count rows"}]),
            AIMessageChunk(content=[{"type": "text", "text": "There are 100,000 clients."}]),
        ]
        ev = drive(AnthropicAdapter(), chunks)
        self.assertEqual(answer_of(ev), "There are 100,000 clients.")
        self.assertEqual(reasoning_of(ev), "let me count rows")
        # channels open then close exactly once
        self.assertEqual(sum(1 for e in ev if e.type == EventType.ANSWER_START), 1)
        self.assertEqual(sum(1 for e in ev if e.type == EventType.REASONING_START), 1)

    def test_bedrock_reasoning_content_block(self) -> None:
        chunks = [
            AIMessageChunk(content=[{"type": "reasoning_content",
                                     "reasoning_content": {"text": "the bat is $1.05"}}]),
            AIMessageChunk(content="The ball is 5 cents."),
        ]
        ev = drive(BedrockConverseAdapter(), chunks)
        self.assertEqual(answer_of(ev), "The ball is 5 cents.")
        self.assertIn("the bat is $1.05", reasoning_of(ev))

    def test_openai_reasoning_in_additional_kwargs(self) -> None:
        chunks = [
            AIMessageChunk(content="", additional_kwargs={"reasoning_content": "step-by-step here"}),
            AIMessageChunk(content="Answer: 42"),
        ]
        ev = drive(OpenAIAdapter(), chunks)
        self.assertEqual(answer_of(ev), "Answer: 42")
        self.assertIn("step-by-step here", reasoning_of(ev))

    def test_plain_string_content_is_all_answer(self) -> None:
        ev = drive(GenericAdapter(), [AIMessageChunk(content="hello "), AIMessageChunk(content="world")])
        self.assertEqual(answer_of(ev), "hello world")
        self.assertEqual(reasoning_of(ev), "")


class ToolAndUsageTests(unittest.TestCase):
    def test_tool_call_chunks_assemble(self) -> None:
        chunks = [
            AIMessageChunk(content="", tool_call_chunks=[
                {"name": "run_sql", "args": '{"sql":"SEL', "id": "c1", "index": 0, "type": "tool_call_chunk"}]),
            AIMessageChunk(content="", tool_call_chunks=[
                {"name": None, "args": 'ECT 1"}', "id": None, "index": 0, "type": "tool_call_chunk"}]),
        ]
        ad = GenericAdapter()
        ev = drive(ad, chunks)
        starts = [e for e in ev if e.type == EventType.TOOL_CALL_START]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0].data["name"], "run_sql")
        self.assertEqual(tool_ends(ev), [("run_sql", '{"sql":"SELECT 1"}')])

    def test_usage_captured(self) -> None:
        chunk = AIMessageChunk(content="hi", usage_metadata={
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
            "output_token_details": {"reasoning": 3}})
        ev = drive(GenericAdapter(), [chunk])
        usage = next(e.data["usage"] for e in ev if e.type == EventType.USAGE)
        self.assertEqual(usage.input_tokens, 10)
        self.assertEqual(usage.output_tokens, 5)
        self.assertEqual(usage.reasoning_tokens, 3)


class RegistryTests(unittest.TestCase):
    def test_build_adapter_by_provider(self) -> None:
        self.assertIsInstance(build_adapter("anthropic"), AnthropicAdapter)
        self.assertIsInstance(build_adapter("bedrock_converse"), BedrockConverseAdapter)
        self.assertIsInstance(build_adapter("openai"), OpenAIAdapter)
        self.assertIsInstance(build_adapter("something_new"), GenericAdapter)  # unknown -> generic
        self.assertIsInstance(build_adapter(None), GenericAdapter)

    def test_detect_provider_from_class(self) -> None:
        class ChatBedrockConverse: ...
        class ChatAnthropic: ...
        class ChatOpenAI: ...
        self.assertEqual(detect_provider(ChatBedrockConverse()), "bedrock_converse")
        self.assertEqual(detect_provider(ChatAnthropic()), "anthropic")
        self.assertEqual(detect_provider(ChatOpenAI()), "openai")
        self.assertEqual(detect_provider(object()), "generic")


if __name__ == "__main__":
    unittest.main()
