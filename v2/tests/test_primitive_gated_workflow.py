import contextlib
import io
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import AIMessageChunk

from diracdata_v2.primitive import (
    GatedPrimitiveWorkflow,
    PrimitiveAgentRunner,
    PrimitiveRunResult,
    PrimitiveTraceEvent,
    parse_status_packet,
)


class FakeRunner:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.tasks: list[str] = []

    def run(self, task: str) -> PrimitiveRunResult:
        self.tasks.append(task)
        if not self.outputs:
            raise AssertionError("No fake output left")
        return PrimitiveRunResult(
            output_text=self.outputs.pop(0),
            trace_events=[],
            iterations=1,
            stop_reason="final",
        )


class FakeExecuteTool:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def invoke(self, args):
        self.calls.append(args)
        return {
            "status": "ok",
            "columns": ["answer"],
            "rows": [{"answer": 7}],
            "row_count": 1,
            "sql": args["sql"],
        }


class StreamingStatusModel:
    def __init__(self, text: str) -> None:
        self.text = text

    def bind_tools(self, tools):
        return self

    def stream(self, messages):
        midpoint = max(1, len(self.text) // 2)
        yield AIMessageChunk(content=self.text[:midpoint])
        yield AIMessageChunk(content=self.text[midpoint:])

    def invoke(self, messages):
        raise AssertionError("streaming test should use stream")


class GatedPrimitiveWorkflowTests(unittest.TestCase):
    def test_parse_status_packet_supports_pass_with_assumptions(self) -> None:
        packet = parse_status_packet(
            """STEWARD_STATUS: PASS_WITH_ASSUMPTIONS
ISSUES:
- none
EVIDENCE:
- intent alignment: passed
"""
        )

        self.assertIsNotNone(packet)
        assert packet is not None
        self.assertEqual(packet.component, "steward")
        self.assertEqual(packet.status, "PASS_WITH_ASSUMPTIONS")
        self.assertIn("EVIDENCE", packet.sections)

    def test_steward_clarification_blocks_stale_final_answer(self) -> None:
        analyst = FakeRunner(
            [
                """ANALYST_STATUS: OK
INTERPRETATION:
- chosen meaning: old interpretation
FINAL_SQL:
select 15
RESULT_PREVIEW:
15
ROW_COUNT:
1
DATA_ENGINEERING_REVIEW:
not needed
"""
            ]
        )
        steward = FakeRunner(
            [
                """STEWARD_STATUS: NEEDS_CLARIFICATION
ISSUES:
- product phrase could map to broad category or narrow class
REQUIRED_ANALYST_CORRECTION:
Ask whether the broad category or narrow class is intended.
"""
            ]
        )
        workflow = GatedPrimitiveWorkflow(
            analyst=analyst, steward=steward, data_engineer=FakeRunner([])
        )

        result = workflow.run("count customers")

        self.assertEqual(result.stop_reason, "needs_clarification")
        self.assertIn("CLARIFICATION_REQUIRED", result.output_text)
        self.assertNotIn("FINAL_STATUS: PASS", result.output_text)
        event_types = [event.event_type for event in result.trace_events]
        self.assertIn("clarification_required", event_types)

    def test_steward_fail_forces_analyst_rewrite_before_final(self) -> None:
        analyst = FakeRunner(
            [
                """ANALYST_STATUS: OK
INTERPRETATION:
- chosen meaning: current state
FINAL_SQL:
select 15
RESULT_PREVIEW:
15
ROW_COUNT:
1
DATA_ENGINEERING_REVIEW:
not needed
""",
                """ANALYST_STATUS: OK
INTERPRETATION:
- chosen meaning: event-time state
FINAL_SQL:
select 9
RESULT_PREVIEW:
9
ROW_COUNT:
1
DATA_ENGINEERING_REVIEW:
not needed
""",
            ]
        )
        steward = FakeRunner(
            [
                """STEWARD_STATUS: FAIL
ISSUES:
- current dimension used where event-time dimension is required
REQUIRED_ANALYST_CORRECTION:
Use the event-time address/profile references.
""",
                """STEWARD_STATUS: PASS
ISSUES:
- none
EVIDENCE:
- intent alignment: passed
""",
            ]
        )
        workflow = GatedPrimitiveWorkflow(
            analyst=analyst, steward=steward, data_engineer=FakeRunner([])
        )

        result = workflow.run("count customers")

        self.assertEqual(result.stop_reason, "final")
        self.assertIn("FINAL_STATUS: PASS", result.output_text)
        self.assertIn("9", result.output_text)
        self.assertNotIn("RESULT:\n15", result.output_text)
        self.assertEqual(len(analyst.tasks), 2)

    def test_pass_with_assumptions_can_finalize_but_discloses_status(self) -> None:
        analyst = FakeRunner(
            [
                """ANALYST_STATUS: OK
INTERPRETATION:
- chosen meaning: broad category
FINAL_SQL:
select 5
RESULT_PREVIEW:
5
ROW_COUNT:
1
ASSUMPTIONS:
Use broad category instead of narrow subclass.
DATA_ENGINEERING_REVIEW:
not needed
"""
            ]
        )
        steward = FakeRunner(
            [
                """STEWARD_STATUS: PASS_WITH_ASSUMPTIONS
ISSUES:
- none
EVIDENCE:
- value grounding: passed with disclosed assumption
"""
            ]
        )
        workflow = GatedPrimitiveWorkflow(
            analyst=analyst, steward=steward, data_engineer=FakeRunner([])
        )

        result = workflow.run("count customers")

        self.assertEqual(result.stop_reason, "final")
        self.assertIn("FINAL_STATUS: PASS_WITH_ASSUMPTIONS", result.output_text)
        self.assertIn("Use broad category", result.output_text)

    def test_inferred_business_definition_requires_clarification_before_steward(self) -> None:
        analyst = FakeRunner(
            [
                """ANALYST_STATUS: OK
INTERPRETATION:
- chosen meaning: Count customers using an inferred segment definition.
FINAL_SQL:
select 15
RESULT_PREVIEW:
15
ROW_COUNT:
1
ASSUMPTIONS:
- The requested segment has no explicit status field, so it was treated as a standard interpretation based on observed activity.
DATA_ENGINEERING_REVIEW:
not needed
"""
            ]
        )
        steward = FakeRunner(
            [
                """STEWARD_STATUS: PASS
ISSUES:
- none
"""
            ]
        )
        workflow = GatedPrimitiveWorkflow(
            analyst=analyst, steward=steward, data_engineer=FakeRunner([])
        )

        result = workflow.run("count the requested segment")

        self.assertEqual(result.stop_reason, "needs_clarification")
        self.assertIn("CLARIFICATION_REQUIRED", result.output_text)
        self.assertIn("business term was not defined", result.output_text)
        self.assertEqual(steward.tasks, [])

    def test_staged_workflow_stops_unresolved_intent_before_sql_authoring(self) -> None:
        intent = FakeRunner(
            [
                """INTENT_STATUS: OK
INTENT_SUMMARY:
- measure: count customers
BUSINESS_TERMS:
- term: active customer
  status: UNRESOLVED
UNRESOLVED_TERMS:
- active customer
ASSUMPTIONS:
none
"""
            ]
        )
        sql_author = FakeRunner(["SQL_AUTHOR_STATUS: OK\nFINAL_SQL:\nselect 1\nASSUMPTIONS:\nnone"])
        steward = FakeRunner(["STEWARD_STATUS: PASS\nISSUES:\n- none"])
        execute_tool = FakeExecuteTool()
        workflow = GatedPrimitiveWorkflow(
            analyst=FakeRunner([]),
            steward=steward,
            data_engineer=FakeRunner([]),
            intent=intent,
            sql_author=sql_author,
            final_execute_tool=execute_tool,
        )

        result = workflow.run("count active customers")

        self.assertEqual(result.stop_reason, "needs_clarification")
        self.assertIn("CLARIFICATION_REQUIRED", result.output_text)
        self.assertEqual(sql_author.tasks, [])
        self.assertEqual(steward.tasks, [])
        self.assertEqual(execute_tool.calls, [])

    def test_staged_workflow_surfaces_blocking_assumption_details(self) -> None:
        intent = FakeRunner(
            [
                """INTENT_STATUS: OK
INTENT_SUMMARY:
- measure: count customers
UNRESOLVED_TERMS:
<none>
ASSUMPTIONS:
- Treat missing warehouse relationship as any warehouse with stock.
"""
            ]
        )
        sql_author = FakeRunner(["SQL_AUTHOR_STATUS: OK\nFINAL_SQL:\nselect 1\nASSUMPTIONS:\nnone"])
        steward = FakeRunner(["STEWARD_STATUS: PASS\nISSUES:\n- none"])
        workflow = GatedPrimitiveWorkflow(
            analyst=FakeRunner([]),
            steward=steward,
            data_engineer=FakeRunner([]),
            intent=intent,
            sql_author=sql_author,
            final_execute_tool=FakeExecuteTool(),
        )

        result = workflow.run("count customers")

        self.assertEqual(result.stop_reason, "needs_clarification")
        self.assertIn("Assumption details", result.output_text)
        self.assertIn("missing warehouse relationship", result.output_text)
        self.assertEqual(sql_author.tasks, [])

    def test_staged_workflow_does_not_block_none_with_resolution_text(self) -> None:
        intent = FakeRunner(
            [
                """INTENT_STATUS: OK
INTENT_SUMMARY:
- measure: count customers
UNRESOLVED_TERMS:
<none>
ASSUMPTIONS:
<none; all SQL-affecting terms are resolved>
"""
            ]
        )
        sql_author = FakeRunner(
            [
                """SQL_AUTHOR_STATUS: OK
INTERPRETATION:
- chosen meaning: count customers
FINAL_SQL:
select 7 as answer
DRY_RUN:
passed
VALUE_PROBES:
not needed
ASSUMPTIONS:
None. All SQL-affecting terms are resolved.
"""
            ]
        )
        steward = FakeRunner(
            [
                """STEWARD_STATUS: PASS
ISSUES:
- none
EVIDENCE:
- dry run: passed
"""
            ]
        )
        execute_tool = FakeExecuteTool()
        workflow = GatedPrimitiveWorkflow(
            analyst=FakeRunner([]),
            steward=steward,
            data_engineer=FakeRunner([]),
            intent=intent,
            sql_author=sql_author,
            final_execute_tool=execute_tool,
        )

        result = workflow.run("count customers")

        self.assertEqual(result.stop_reason, "final")
        self.assertIn("FINAL_STATUS: PASS", result.output_text)
        self.assertEqual(execute_tool.calls, [{"sql": "select 7 as answer"}])

    def test_staged_workflow_executes_final_sql_only_after_steward_pass(self) -> None:
        intent = FakeRunner(
            [
                """INTENT_STATUS: OK
INTENT_SUMMARY:
- measure: count customers
BUSINESS_TERMS:
- term: customer
  status: DEFINED
UNRESOLVED_TERMS:
<none>
ASSUMPTIONS:
<none>
"""
            ]
        )
        sql_author = FakeRunner(
            [
                """SQL_AUTHOR_STATUS: OK
INTERPRETATION:
- chosen meaning: count customers
FINAL_SQL:
select 7 as answer
DRY_RUN:
passed
ASSUMPTIONS:
None. All SQL-affecting terms are defined in the approved intent packet.
"""
            ]
        )
        steward = FakeRunner(
            [
                """STEWARD_STATUS: PASS
ISSUES:
- none
EVIDENCE:
- dry run: passed
"""
            ]
        )
        execute_tool = FakeExecuteTool()
        workflow = GatedPrimitiveWorkflow(
            analyst=FakeRunner([]),
            steward=steward,
            data_engineer=FakeRunner([]),
            intent=intent,
            sql_author=sql_author,
            final_execute_tool=execute_tool,
        )

        result = workflow.run("count customers")

        self.assertEqual(result.stop_reason, "final")
        self.assertIn("FINAL_STATUS: PASS", result.output_text)
        self.assertIn("| answer |", result.output_text)
        self.assertEqual(execute_tool.calls, [{"sql": "select 7 as answer"}])
        event_types = [event.event_type for event in result.trace_events]
        self.assertIn("tool_call", event_types)
        self.assertIn("tool_result", event_types)

    def test_staged_workflow_compiler_unresolved_terms_stop_before_subagents(self) -> None:
        intent = FakeRunner(["INTENT_STATUS: OK\nUNRESOLVED_TERMS:\n<none>\nASSUMPTIONS:\n<none>"])
        sql_author = FakeRunner(["SQL_AUTHOR_STATUS: OK\nFINAL_SQL:\nselect 1\nASSUMPTIONS:\nnone"])
        steward = FakeRunner(["STEWARD_STATUS: PASS\nISSUES:\n- none"])
        execute_tool = FakeExecuteTool()
        workflow = GatedPrimitiveWorkflow(
            analyst=FakeRunner([]),
            steward=steward,
            data_engineer=FakeRunner([]),
            intent=intent,
            sql_author=sql_author,
            final_execute_tool=execute_tool,
            context_compiler=lambda question: {
                "question": question,
                "needs_clarification": True,
                "unresolved_terms": [{"term": "active"}],
                "candidate_cards": [],
                "sql_patterns": [],
                "join_edges": [],
            },
        )

        result = workflow.run("count active customers")

        self.assertEqual(result.stop_reason, "needs_clarification")
        self.assertIn("active", result.output_text)
        self.assertEqual(intent.tasks, [])
        self.assertEqual(sql_author.tasks, [])
        self.assertEqual(steward.tasks, [])
        self.assertEqual(execute_tool.calls, [])
        event_types = [event.event_type for event in result.trace_events]
        self.assertIn("context_compiled", event_types)

    def test_staged_workflow_injects_compiled_context_to_all_stages(self) -> None:
        intent = FakeRunner(
            [
                """INTENT_STATUS: OK
INTENT_SUMMARY:
- measure: count customers
UNRESOLVED_TERMS:
<none>
ASSUMPTIONS:
<none>
"""
            ]
        )
        sql_author = FakeRunner(
            [
                """SQL_AUTHOR_STATUS: OK
INTERPRETATION:
- chosen meaning: count customers
FINAL_SQL:
select 7 as answer
DRY_RUN:
passed
VALUE_PROBES:
not needed
ASSUMPTIONS:
none
"""
            ]
        )
        steward = FakeRunner(
            [
                """STEWARD_STATUS: PASS
ISSUES:
- none
EVIDENCE:
- dry run: passed
"""
            ]
        )
        workflow = GatedPrimitiveWorkflow(
            analyst=FakeRunner([]),
            steward=steward,
            data_engineer=FakeRunner([]),
            intent=intent,
            sql_author=sql_author,
            final_execute_tool=FakeExecuteTool(),
            context_compiler=lambda question: {
                "question": question,
                "needs_clarification": False,
                "candidate_cards": [{"id": "column:customers.gender", "sql_ref": "customers.gender"}],
                "sql_patterns": [],
                "join_edges": [],
            },
        )

        result = workflow.run("count customers")

        self.assertEqual(result.stop_reason, "final")
        self.assertIn("COMPILED_SEMANTIC_CONTEXT", intent.tasks[0])
        self.assertIn("customers.gender", intent.tasks[0])
        self.assertIn("COMPILED_SEMANTIC_CONTEXT", sql_author.tasks[0])
        self.assertIn("customers.gender", sql_author.tasks[0])
        self.assertIn("COMPILED_SEMANTIC_CONTEXT", steward.tasks[0])
        self.assertIn("customers.gender", steward.tasks[0])

    def test_staged_workflow_blocks_string_predicate_without_value_grounding(self) -> None:
        intent = FakeRunner(
            [
                """INTENT_STATUS: OK
INTENT_SUMMARY:
- measure: count orders
UNRESOLVED_TERMS:
<none>
ASSUMPTIONS:
<none>
"""
            ]
        )
        sql_author = FakeRunner(
            [
                """SQL_AUTHOR_STATUS: OK
INTERPRETATION:
- chosen meaning: count completed orders
FINAL_SQL:
select count(*) as answer from orders where status = 'completed'
DRY_RUN:
passed
VALUE_PROBES:
none
ASSUMPTIONS:
none
"""
            ]
        )
        steward = FakeRunner(
            [
                """STEWARD_STATUS: PASS
ISSUES:
- none
EVIDENCE:
- dry run: passed
"""
            ]
        )
        execute_tool = FakeExecuteTool()
        workflow = GatedPrimitiveWorkflow(
            analyst=FakeRunner([]),
            steward=steward,
            data_engineer=FakeRunner([]),
            intent=intent,
            sql_author=sql_author,
            final_execute_tool=execute_tool,
        )

        result = workflow.run("count completed orders")

        self.assertEqual(result.stop_reason, "blocked")
        self.assertIn("value-grounding evidence", result.output_text)
        self.assertEqual(execute_tool.calls, [])
        event_types = [event.event_type for event in result.trace_events]
        self.assertIn("value_grounding_blocked", event_types)

    def test_staged_workflow_allows_string_predicate_with_value_probe_evidence(self) -> None:
        intent = FakeRunner(
            [
                """INTENT_STATUS: OK
INTENT_SUMMARY:
- measure: count orders
UNRESOLVED_TERMS:
<none>
ASSUMPTIONS:
<none>
"""
            ]
        )
        sql_author = FakeRunner(
            [
                """SQL_AUTHOR_STATUS: OK
INTERPRETATION:
- chosen meaning: count completed orders
FINAL_SQL:
select count(*) as answer from orders where status = 'completed'
DRY_RUN:
passed
VALUE_PROBES:
orders.status -> completed observed by column_values
ASSUMPTIONS:
none
"""
            ]
        )
        steward = FakeRunner(
            [
                """STEWARD_STATUS: PASS
ISSUES:
- none
EVIDENCE:
- value grounding: passed
- dry run: passed
"""
            ]
        )
        execute_tool = FakeExecuteTool()
        workflow = GatedPrimitiveWorkflow(
            analyst=FakeRunner([]),
            steward=steward,
            data_engineer=FakeRunner([]),
            intent=intent,
            sql_author=sql_author,
            final_execute_tool=execute_tool,
        )

        result = workflow.run("count completed orders")

        self.assertEqual(result.stop_reason, "final")
        self.assertEqual(
            execute_tool.calls,
            [{"sql": "select count(*) as answer from orders where status = 'completed'"}],
        )

    def test_gated_workflow_streams_nested_subagent_tokens(self) -> None:
        analyst = PrimitiveAgentRunner(
            name="analyst_subagent",
            model=StreamingStatusModel(
                """ANALYST_STATUS: OK
INTERPRETATION:
- chosen meaning: streamed
FINAL_SQL:
select 1
RESULT_PREVIEW:
1
ROW_COUNT:
1
DATA_ENGINEERING_REVIEW:
not needed
"""
            ),
            tools=[],
            system_prompt="analyst",
        )
        steward = PrimitiveAgentRunner(
            name="data_steward_subagent",
            model=StreamingStatusModel(
                """STEWARD_STATUS: PASS
ISSUES:
- none
EVIDENCE:
- streamed evidence
"""
            ),
            tools=[],
            system_prompt="steward",
        )
        workflow = GatedPrimitiveWorkflow(
            analyst=analyst,
            steward=steward,
            data_engineer=FakeRunner([]),
        )
        streamed: list[PrimitiveTraceEvent] = []

        result = workflow.run("count customers", event_sink=streamed.append)

        self.assertEqual(result.stop_reason, "final")
        deltas = [event for event in streamed if event.event_type == "model_delta"]
        self.assertGreaterEqual(len(deltas), 2)
        self.assertTrue(any(event.agent_name == "analyst_subagent" for event in deltas))
        self.assertTrue(any(event.agent_name == "data_steward_subagent" for event in deltas))
        result_deltas = [event for event in result.trace_events if event.event_type == "model_delta"]
        self.assertEqual(len(result_deltas), len(deltas))


class InteractiveCliTests(unittest.TestCase):
    def test_interactive_session_resumes_with_user_clarification(self) -> None:
        cli = _load_cli_module()
        runtime = FakeInteractiveRuntime()
        printed: list[str] = []
        inputs = iter(["Use the broad category."])

        turns = cli.run_interactive_session(
            runtime=runtime,
            question="count customers",
            workflow="gated",
            max_clarifications=2,
            input_func=lambda prompt: next(inputs),
            output_func=printed.append,
        )

        self.assertEqual(len(turns), 2)
        self.assertEqual(runtime.calls[1]["clarification"], "Use the broad category.")
        self.assertEqual(runtime.calls[1]["previous_context"], "prior packet")
        self.assertIn("FINAL_STATUS: PASS", printed[-1])

    def test_interactive_session_supports_numbered_clarification_choice(self) -> None:
        cli = _load_cli_module()
        runtime = FakeInteractiveRuntime(
            choices=[
                "Check only the scoped action source.",
                "Check all available sources for the broad action.",
            ]
        )
        printed: list[str] = []
        inputs = iter(["2"])

        turns = cli.run_interactive_session(
            runtime=runtime,
            question="count customers",
            workflow="gated",
            max_clarifications=2,
            input_func=lambda prompt: next(inputs),
            output_func=printed.append,
        )

        self.assertEqual(len(turns), 2)
        self.assertEqual(
            runtime.calls[1]["clarification"],
            "Check all available sources for the broad action.",
        )
        self.assertTrue(any(line.startswith("1. Check only") for line in printed))
        self.assertTrue(any(line.startswith("2. Check all") for line in printed))

    def test_clarification_choices_from_text_adds_other_option(self) -> None:
        cli = _load_cli_module()
        choices = cli._with_other_choice(
            cli._clarification_choices_from_text(
                """CLARIFICATION_REQUIRED
CLARIFICATION_QUESTION:
Which scope should I use?
MCQ_OPTIONS:
1. Only the named source.
2. All available sources.
"""
            )
        )

        self.assertEqual(choices[0], "Only the named source.")
        self.assertEqual(choices[1], "All available sources.")
        self.assertTrue(choices[-1].startswith("Other:"))

    def test_clarification_choices_from_markdown_options(self) -> None:
        cli = _load_cli_module()
        choices = cli._clarification_choices_from_text(
            """The intent subagent found an ambiguity.

## CLARIFICATION_REQUIRED
Should I use source A or all sources?
- **Option A:** Source A only.
- **Option B:** All sources.
"""
        )

        self.assertEqual(choices, ["Source A only.", "All sources."])

    def test_interactive_session_streams_events_from_runtime(self) -> None:
        cli = _load_cli_module()
        runtime = FakeInteractiveRuntime()
        streamed: list[PrimitiveTraceEvent] = []

        cli.run_interactive_session(
            runtime=runtime,
            question="count customers",
            workflow="gated",
            max_clarifications=0,
            input_func=lambda prompt: "",
            output_func=lambda text: None,
            event_sink=streamed.append,
        )

        self.assertEqual(runtime.calls[0]["event_sink"], "provided")
        self.assertEqual(streamed[0].event_type, "model_delta")

    def test_interactive_session_supports_typed_workflow(self) -> None:
        cli = _load_cli_module()
        runtime = FakeInteractiveRuntime(choices=["Use the scoped exclusion.", "Use broad exclusion."])
        printed: list[str] = []
        inputs = iter(["1"])

        turns = cli.run_interactive_session(
            runtime=runtime,
            question="count customers",
            workflow="typed",
            max_clarifications=2,
            input_func=lambda prompt: next(inputs),
            output_func=printed.append,
        )

        self.assertEqual(len(turns), 2)
        self.assertEqual(runtime.calls[1]["method"], "typed")
        self.assertEqual(runtime.calls[1]["clarification"], "Use the scoped exclusion.")
        self.assertTrue(any(line.startswith("1. Use the scoped") for line in printed))

    def test_resume_context_can_be_read_from_prior_output_file(self) -> None:
        cli = _load_cli_module()
        payload = {
            "trace_events": [
                {
                    "event_type": "clarification_required",
                    "payload": {"previous_context": "prior intent packet"},
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "prior.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            args = argparse_namespace(
                clarification="Use income band by warehouse.",
                resume_from_output_file=str(path),
                output_file=None,
            )

            context = cli._resume_context_for_args(args)

        self.assertEqual(context, "prior intent packet")

    def test_latest_clarification_context_reads_interactive_turns(self) -> None:
        cli = _load_cli_module()
        payload = {
            "turns": [
                {"trace_events": []},
                {
                    "trace_events": [
                        {
                            "event_type": "clarification_required",
                            "payload": {"previous_context": "turn two packet"},
                        }
                    ]
                },
            ]
        }

        self.assertEqual(cli._latest_clarification_context(payload), "turn two packet")

    def test_non_interactive_clarification_hint_prints_to_stderr(self) -> None:
        cli = _load_cli_module()
        stderr = io.StringIO()
        result = PrimitiveRunResult(
            output_text="CLARIFICATION_REQUIRED",
            trace_events=[],
            iterations=1,
            stop_reason="needs_clarification",
        )
        args = argparse_namespace(interactive=False)

        with contextlib.redirect_stderr(stderr):
            cli._print_clarification_hint_if_needed(result, args)

        self.assertIn("--interactive", stderr.getvalue())
        self.assertIn("--clarification", stderr.getvalue())

    def test_text_stream_prints_tool_args_and_result_preview(self) -> None:
        cli = _load_cli_module()
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            cli._print_stream_event(
                {
                    "event_type": "tool_call",
                    "agent_name": "agent",
                    "payload": {"name": "execute_sql", "args": {"sql": "select 1"}},
                },
                "text",
            )
            cli._print_stream_event(
                {
                    "event_type": "tool_result",
                    "agent_name": "agent",
                    "payload": {
                        "name": "execute_sql",
                        "preview": '{"rows": [{"x": 1}]}',
                        "truncated": False,
                    },
                },
                "text",
            )

        rendered = stderr.getvalue()
        self.assertIn("[tool_call:execute_sql]", rendered)
        self.assertIn('"sql": "select 1"', rendered)
        self.assertIn("[tool_result:execute_sql]", rendered)
        self.assertIn('"rows"', rendered)

    def test_text_stream_prints_supervisor_events(self) -> None:
        cli = _load_cli_module()
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            cli._print_stream_event(
                {
                    "event_type": "supervisor_start",
                    "agent_name": "supervisor_workflow",
                    "payload": {},
                },
                "text",
            )
            cli._print_stream_event(
                {
                    "event_type": "supervisor_done",
                    "agent_name": "supervisor_workflow",
                    "payload": {"stop_reason": "final"},
                },
                "text",
            )

        rendered = stderr.getvalue()
        self.assertIn("[supervisor_start:supervisor_workflow]", rendered)
        self.assertIn("[supervisor_done:supervisor_workflow:final]", rendered)


class FakeInteractiveRuntime:
    def __init__(self, choices: list[str] | None = None) -> None:
        self.calls: list[dict[str, str | None]] = []
        self.choices = choices or []

    def invoke(
        self,
        question: str,
        *,
        clarification: str | None = None,
        previous_context: str | None = None,
        event_sink=None,
    ) -> PrimitiveRunResult:
        return self._invoke(
            method="gated",
            question=question,
            clarification=clarification,
            previous_context=previous_context,
            event_sink=event_sink,
        )

    def invoke_typed(
        self,
        question: str,
        *,
        clarification: str | None = None,
        previous_context: str | None = None,
        event_sink=None,
    ) -> PrimitiveRunResult:
        return self._invoke(
            method="typed",
            question=question,
            clarification=clarification,
            previous_context=previous_context,
            event_sink=event_sink,
        )

    def _invoke(
        self,
        *,
        method: str,
        question: str,
        clarification: str | None,
        previous_context: str | None,
        event_sink=None,
    ) -> PrimitiveRunResult:
        if event_sink is not None:
            event_sink(
                PrimitiveTraceEvent(
                    event_type="model_delta",
                    agent_name="fake_runtime",
                    payload={"text": "streamed"},
                )
            )
        self.calls.append(
            {
                "method": method,
                "question": question,
                "clarification": clarification,
                "previous_context": previous_context,
                "event_sink": "provided" if event_sink is not None else None,
            }
        )
        if len(self.calls) == 1:
            return PrimitiveRunResult(
                output_text="CLARIFICATION_REQUIRED\nChoose category or class.",
                trace_events=[
                    PrimitiveTraceEvent(
                        event_type="clarification_required",
                        agent_name="gated_workflow",
                        payload={
                            "question": "Choose category or class.",
                            "choices": self.choices,
                            "previous_context": "prior packet",
                        },
                    )
                ],
                iterations=1,
                stop_reason="needs_clarification",
            )
        return PrimitiveRunResult(
            output_text="FINAL_STATUS: PASS\nRESULT:\n5",
            trace_events=[],
            iterations=1,
            stop_reason="final",
        )


def _load_cli_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_primitive_agent.py"
    spec = importlib.util.spec_from_file_location("run_primitive_agent_cli", script)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load CLI module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def argparse_namespace(**kwargs):
    class Namespace:
        pass

    namespace = Namespace()
    for key, value in kwargs.items():
        setattr(namespace, key, value)
    return namespace


if __name__ == "__main__":
    unittest.main()
