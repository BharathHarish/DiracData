import json
import unittest

from langchain.tools import tool
from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import BaseModel, Field

from diracdata_v2.primitive import PrimitiveAgentRunner, build_subagent_tool


class EchoInput(BaseModel):
    text: str = Field(description="Text to echo.")


@tool("echo_tool", args_schema=EchoInput)
def echo_tool(text: str) -> dict[str, str]:
    """Echo input text."""
    return {"echo": text}


class FakeToolCallingModel:
    def __init__(self) -> None:
        self.calls = 0
        self.bound_tool_names: list[str] = []

    def bind_tools(self, tools):
        self.bound_tool_names = [tool.name for tool in tools]
        return self

    def invoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="I will call a tool.",
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "echo_tool",
                        "args": {"text": "hello"},
                    }
                ],
            )
        return AIMessage(content="Final answer after observing the tool.")


class FakeFinalizeAfterToolBudgetModel:
    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="I need one tool.",
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "echo_tool",
                        "args": {"text": "observed"},
                    }
                ],
            )
        return AIMessage(content="ANALYST_STATUS: OK\nRESULT_PREVIEW:\nobserved")


class FakeFinalModel:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="Subagent final.")


class FakeStreamingFinalModel:
    def bind_tools(self, tools):
        return self

    def stream(self, messages):
        yield AIMessageChunk(content="Subagent ")
        yield AIMessageChunk(content="final.")

    def invoke(self, messages):
        return AIMessage(content="Subagent final.")


class FakeStreamingToolCallingModel:
    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, tools):
        return self

    def stream(self, messages):
        self.calls += 1
        if self.calls == 1:
            yield AIMessageChunk(content="I will call ")
            yield AIMessageChunk(
                content="a tool.",
                tool_call_chunks=[
                    {
                        "id": "call_1",
                        "name": "echo_tool",
                        "args": "{\"text\": \"hello\"}",
                        "index": 0,
                    }
                ],
            )
            return
        yield AIMessageChunk(content="Final ")
        yield AIMessageChunk(content="answer.")

    def invoke(self, messages):
        raise AssertionError("streaming path should not call invoke")


class FakeStreamingSubagentCallingModel:
    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, tools):
        return self

    def stream(self, messages):
        self.calls += 1
        if self.calls == 1:
            yield AIMessageChunk(
                content="Delegating.",
                tool_call_chunks=[
                    {
                        "id": "call_sub",
                        "name": "subagent_tool",
                        "args": "{\"task\": \"do work\"}",
                        "index": 0,
                    }
                ],
            )
            return
        yield AIMessageChunk(content="Outer done.")

    def invoke(self, messages):
        raise AssertionError("streaming path should not call invoke")


class FakeStoppingModel:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(
            content="partial SQL that must not be trusted",
            tool_calls=[
                {
                    "id": "missing",
                    "name": "missing_tool",
                    "args": {},
                }
            ],
        )


class PrimitiveRunnerTests(unittest.TestCase):
    def test_runner_executes_tool_and_finishes(self) -> None:
        model = FakeToolCallingModel()
        runner = PrimitiveAgentRunner(
            name="test_agent",
            model=model,
            tools=[echo_tool],
            system_prompt="You are a test agent.",
        )

        result = runner.run("echo hello")

        self.assertEqual(result.stop_reason, "final")
        self.assertEqual(model.bound_tool_names, ["echo_tool"])
        self.assertIn("Final answer", result.output_text)
        event_types = [event.event_type for event in result.trace_events]
        self.assertIn("tool_call", event_types)
        self.assertIn("tool_result", event_types)

    def test_runner_allows_final_packet_after_last_tool_at_budget(self) -> None:
        model = FakeFinalizeAfterToolBudgetModel()
        runner = PrimitiveAgentRunner(
            name="test_agent",
            model=model,
            tools=[echo_tool],
            system_prompt="Return a packet.",
            max_iterations=1,
        )

        result = runner.run("echo hello")

        self.assertEqual(result.stop_reason, "final")
        self.assertEqual(result.iterations, 2)
        self.assertIn("ANALYST_STATUS: OK", result.output_text)
        finalization_starts = [
            event
            for event in result.trace_events
            if event.event_type == "model_start" and event.payload.get("finalization")
        ]
        self.assertEqual(len(finalization_starts), 1)

    def test_stream_yields_trace_events(self) -> None:
        runner = PrimitiveAgentRunner(
            name="test_agent",
            model=FakeToolCallingModel(),
            tools=[echo_tool],
            system_prompt="You are a test agent.",
        )

        events = list(runner.stream("echo hello"))

        self.assertEqual(events[0].event_type, "agent_start")
        self.assertEqual(events[-1].event_type, "agent_done")

    def test_stream_yields_model_delta_events_before_final_message(self) -> None:
        runner = PrimitiveAgentRunner(
            name="test_agent",
            model=FakeStreamingToolCallingModel(),
            tools=[echo_tool],
            system_prompt="You are a test agent.",
        )

        events = list(runner.stream("echo hello"))

        event_types = [event.event_type for event in events]
        self.assertIn("model_delta", event_types)
        self.assertIn("tool_call_delta", event_types)
        self.assertIn("tool_call", event_types)
        self.assertIn("tool_result", event_types)
        self.assertLess(event_types.index("model_delta"), event_types.index("model_message"))
        deltas = "".join(
            event.payload["text"] for event in events if event.event_type == "model_delta"
        )
        self.assertIn("Final answer.", deltas)

    def test_stream_exposes_nested_subagent_deltas(self) -> None:
        subagent = PrimitiveAgentRunner(
            name="subagent",
            model=FakeStreamingFinalModel(),
            tools=[],
            system_prompt="You are a subagent.",
        )
        subagent_tool = build_subagent_tool(
            name="subagent_tool",
            description="Run subagent.",
            runner=subagent,
        )
        outer = PrimitiveAgentRunner(
            name="outer",
            model=FakeStreamingSubagentCallingModel(),
            tools=[subagent_tool],
            system_prompt="You are an outer agent.",
        )

        events = list(outer.stream("delegate"))

        subagent_deltas = [
            event
            for event in events
            if event.event_type == "model_delta" and event.agent_name == "subagent"
        ]
        self.assertGreaterEqual(len(subagent_deltas), 2)
        self.assertEqual(
            "".join(event.payload["text"] for event in subagent_deltas),
            "Subagent final.",
        )

    def test_subagent_tool_wraps_nested_runner(self) -> None:
        runner = PrimitiveAgentRunner(
            name="subagent",
            model=FakeFinalModel(),
            tools=[],
            system_prompt="You are a subagent.",
        )
        subagent_tool = build_subagent_tool(
            name="subagent_tool",
            description="Run subagent.",
            runner=runner,
        )

        result = subagent_tool.invoke({"task": "do work", "context": "small context"})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["subagent"], "subagent")
        self.assertIn("Subagent final", result["output"])
        json.dumps(result)

    def test_stopped_subagent_does_not_return_partial_output_as_evidence(self) -> None:
        runner = PrimitiveAgentRunner(
            name="subagent",
            model=FakeStoppingModel(),
            tools=[],
            system_prompt="You are a subagent.",
            max_iterations=1,
        )
        subagent_tool = build_subagent_tool(
            name="subagent_tool",
            description="Run subagent.",
            runner=runner,
        )

        result = subagent_tool.invoke({"task": "do work"})

        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["output"], "")
        self.assertIn("not valid evidence", result["error"])
        self.assertIn("partial SQL", result["partial_output_preview"])


if __name__ == "__main__":
    unittest.main()
