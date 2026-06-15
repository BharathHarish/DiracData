import unittest

from langchain.tools import tool
from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from diracdata_v2.primitive import PrimitiveAgentRunner, SupervisorPrimitiveWorkflow


class SubagentInput(BaseModel):
    task: str = Field(description="Task for the subagent.")
    context: str | None = Field(default=None, description="Optional context.")


class ExecuteInput(BaseModel):
    sql: str = Field(description="Final approved SQL.")
    max_rows: int | None = Field(default=None, description="Max rows.")


class ScriptedSupervisorModel:
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
                content="Create intent.",
                tool_calls=[
                    {
                        "id": "intent_1",
                        "name": "intent_subagent",
                        "args": {"task": "Create intent from original question."},
                    }
                ],
            )
        if self.calls == 2:
            return AIMessage(
                content="Write SQL.",
                tool_calls=[
                    {
                        "id": "sql_1",
                        "name": "sql_author_subagent",
                        "args": {"task": "Write SQL from approved intent."},
                    }
                ],
            )
        if self.calls == 3:
            return AIMessage(
                content="Validate SQL.",
                tool_calls=[
                    {
                        "id": "steward_1",
                        "name": "data_steward_subagent",
                        "args": {"task": "Validate SQL author packet."},
                    }
                ],
            )
        if self.calls == 4:
            return AIMessage(
                content="Steward failed; repair SQL authoring.",
                tool_calls=[
                    {
                        "id": "sql_2",
                        "name": "sql_author_subagent",
                        "args": {
                            "task": (
                                "Rewrite SQL with Steward feedback. Preserve original income-band "
                                "and warehouse grain."
                            )
                        },
                    }
                ],
            )
        if self.calls == 5:
            return AIMessage(
                content="Validate repaired SQL.",
                tool_calls=[
                    {
                        "id": "steward_2",
                        "name": "data_steward_subagent",
                        "args": {"task": "Validate repaired SQL author packet."},
                    }
                ],
            )
        if self.calls == 6:
            return AIMessage(
                content="Complex query; run DE review.",
                tool_calls=[
                    {
                        "id": "de_1",
                        "name": "data_engineer_subagent",
                        "args": {"task": "Review Steward-approved SQL for cost and CTE shape."},
                    }
                ],
            )
        if self.calls == 7:
            return AIMessage(
                content="Execute final Steward-approved SQL.",
                tool_calls=[
                    {
                        "id": "exec_1",
                        "name": "execute_sql",
                        "args": {"sql": "select 7 as answer", "max_rows": 20},
                    }
                ],
            )
        return AIMessage(
            content=(
                "FINAL_STATUS: PASS\n"
                "ANSWER:\n7\n"
                "VERIFICATION:\n- intent: passed\n- steward: passed\n- data_engineering: run\n- execution: passed\n"
            )
        )


class SupervisorPrimitiveWorkflowTests(unittest.TestCase):
    def test_supervisor_compiler_clarification_stops_before_model(self) -> None:
        class ShouldNotRunModel:
            def bind_tools(self, tools):
                return self

            def invoke(self, messages):
                raise AssertionError("Supervisor model should not run before compiler clarification")

        runner = PrimitiveAgentRunner(
            name="primitive_supervisor_agent",
            model=ShouldNotRunModel(),
            tools=[],
            system_prompt="Supervisor prompt",
        )
        workflow = SupervisorPrimitiveWorkflow(
            supervisor=runner,
            context_compiler=lambda question: {
                "needs_clarification": True,
                "unresolved_terms": [
                    {
                        "term": "broad action",
                        "reason": (
                            "Requires clarification: does the action mean "
                            "(a) source A only, (b) all sources, or (c) source A and source B?"
                        ),
                    }
                ],
            },
        )

        result = workflow.run("hard query")

        self.assertEqual(result.stop_reason, "needs_clarification")
        self.assertIn("Semantic context found", result.output_text)
        clarification_events = [
            event for event in result.trace_events if event.event_type == "clarification_required"
        ]
        self.assertEqual(clarification_events[0].payload["source"], "semantic_catalog_compiler")
        self.assertEqual(clarification_events[0].payload["choices"][0], "source A only")
        self.assertEqual(clarification_events[0].payload["choices"][1], "all sources")

    def test_supervisor_can_repair_after_steward_fail_and_call_de(self) -> None:
        calls: list[tuple[str, str]] = []

        @tool("intent_subagent", args_schema=SubagentInput)
        def intent_subagent(task: str, context: str | None = None) -> dict[str, str]:
            """Create intent."""
            calls.append(("intent_subagent", task))
            return {"status": "ok", "output": "INTENT_STATUS: OK\nUNRESOLVED_TERMS:\n<none>"}

        @tool("sql_author_subagent", args_schema=SubagentInput)
        def sql_author_subagent(task: str, context: str | None = None) -> dict[str, str]:
            """Write SQL."""
            calls.append(("sql_author_subagent", task))
            return {"status": "ok", "output": "SQL_AUTHOR_STATUS: OK\nFINAL_SQL:\nselect 7 as answer"}

        @tool("data_steward_subagent", args_schema=SubagentInput)
        def data_steward_subagent(task: str, context: str | None = None) -> dict[str, str]:
            """Validate SQL."""
            calls.append(("data_steward_subagent", task))
            if sum(1 for name, _ in calls if name == "data_steward_subagent") == 1:
                return {
                    "status": "ok",
                    "output": (
                        "STEWARD_STATUS: FAIL\n"
                        "REQUIRED_ANALYST_CORRECTION:\n"
                        "Restore the missing warehouse grain."
                    ),
                }
            return {"status": "ok", "output": "STEWARD_STATUS: PASS\nISSUES:\n- none"}

        @tool("data_engineer_subagent", args_schema=SubagentInput)
        def data_engineer_subagent(task: str, context: str | None = None) -> dict[str, str]:
            """Optimize SQL."""
            calls.append(("data_engineer_subagent", task))
            return {"status": "ok", "output": "DE_STATUS: UNCHANGED\nOPTIMIZED_SQL:\nselect 7 as answer"}

        @tool("execute_sql", args_schema=ExecuteInput)
        def execute_sql(sql: str, max_rows: int | None = None) -> dict[str, object]:
            """Execute final SQL."""
            calls.append(("execute_sql", sql))
            return {"status": "ok", "columns": ["answer"], "rows": [{"answer": 7}], "row_count": 1}

        model = ScriptedSupervisorModel()
        runner = PrimitiveAgentRunner(
            name="primitive_supervisor_agent",
            model=model,
            tools=[
                intent_subagent,
                sql_author_subagent,
                data_steward_subagent,
                data_engineer_subagent,
                execute_sql,
            ],
            system_prompt="Supervisor prompt",
            max_iterations=8,
        )
        workflow = SupervisorPrimitiveWorkflow(supervisor=runner)

        result = workflow.run("hard query")

        self.assertEqual(result.stop_reason, "final")
        self.assertIn("FINAL_STATUS: PASS", result.output_text)
        self.assertEqual(model.bound_tool_names[-1], "execute_sql")
        self.assertEqual(
            [name for name, _ in calls],
            [
                "intent_subagent",
                "sql_author_subagent",
                "data_steward_subagent",
                "sql_author_subagent",
                "data_steward_subagent",
                "data_engineer_subagent",
                "execute_sql",
            ],
        )
        self.assertIn("warehouse grain", calls[3][1])

    def test_supervisor_clarification_output_sets_clarification_stop_reason(self) -> None:
        class ClarifyingModel:
            def bind_tools(self, tools):
                return self

            def invoke(self, messages):
                return AIMessage(content="CLARIFICATION_REQUIRED\nWhich warehouse relationship should I use?")

        runner = PrimitiveAgentRunner(
            name="primitive_supervisor_agent",
            model=ClarifyingModel(),
            tools=[],
            system_prompt="Supervisor prompt",
        )
        workflow = SupervisorPrimitiveWorkflow(supervisor=runner)

        result = workflow.run("hard query")

        self.assertEqual(result.stop_reason, "needs_clarification")
        self.assertIn("Which warehouse relationship", result.output_text)
        self.assertIn("clarification_required", [event.event_type for event in result.trace_events])

    def test_supervisor_detects_embedded_clarification_packet(self) -> None:
        class EmbeddedClarifyingModel:
            def bind_tools(self, tools):
                return self

            def invoke(self, messages):
                return AIMessage(
                    content=(
                        "The intent subagent found an ambiguity.\n\n"
                        "```\n"
                        "CLARIFICATION_REQUIRED\n"
                        "Choose flat rows or a state-level summary.\n"
                        "```"
                    )
                )

        runner = PrimitiveAgentRunner(
            name="primitive_supervisor_agent",
            model=EmbeddedClarifyingModel(),
            tools=[],
            system_prompt="Supervisor prompt",
        )
        workflow = SupervisorPrimitiveWorkflow(supervisor=runner)

        result = workflow.run("hard query")

        self.assertEqual(result.stop_reason, "needs_clarification")
        clarification_events = [
            event for event in result.trace_events if event.event_type == "clarification_required"
        ]
        self.assertEqual(len(clarification_events), 1)
        self.assertIn("Choose flat rows", clarification_events[0].payload["question"])

    def test_supervisor_detects_markdown_clarification_header_and_options(self) -> None:
        class MarkdownClarifyingModel:
            def bind_tools(self, tools):
                return self

            def invoke(self, messages):
                return AIMessage(
                    content=(
                        "The intent subagent found an ambiguity.\n\n"
                        "## CLARIFICATION_REQUIRED\n"
                        "Should I use source A or all sources?\n"
                        "- **Option A:** Source A only.\n"
                        "- **Option B:** All sources.\n"
                    )
                )

        runner = PrimitiveAgentRunner(
            name="primitive_supervisor_agent",
            model=MarkdownClarifyingModel(),
            tools=[],
            system_prompt="Supervisor prompt",
        )
        workflow = SupervisorPrimitiveWorkflow(supervisor=runner)

        result = workflow.run("hard query")

        self.assertEqual(result.stop_reason, "needs_clarification")
        clarification_events = [
            event for event in result.trace_events if event.event_type == "clarification_required"
        ]
        self.assertEqual(clarification_events[0].payload["choices"][0], "Source A only.")
        self.assertEqual(clarification_events[0].payload["choices"][1], "All sources.")


if __name__ == "__main__":
    unittest.main()
