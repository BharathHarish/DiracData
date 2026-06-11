import json
from pathlib import Path
import tempfile
import unittest

from diracdata.evals.uat_suite import (
    ExpectedBehavior,
    UatConversation,
    UatTurn,
    evaluate_trace,
    load_uat_conversations,
)


ROOT = Path(__file__).resolve().parents[1]


class UatSuiteTest(unittest.TestCase):
    def test_loads_csv_conversations_with_follow_ups(self) -> None:
        conversations = load_uat_conversations(ROOT / "tests/harness/data_analyst_uat.csv")

        case = {conversation.case_id: conversation for conversation in conversations}["uat_001"]

        self.assertEqual(len(conversations), 17)
        self.assertEqual(len(case.turns), 4)
        self.assertEqual(case.question, "How many male customers do we have in California?")
        self.assertEqual(
            case.follow_ups,
            (
                "Now show the same number for female customers.",
                "What is the total if I do not split by gender?",
                "Before we move on what gender values exist in the data?",
            ),
        )
        self.assertEqual(case.turns[0].expected_behavior, ExpectedBehavior.EXECUTE_SQL)
        self.assertEqual(
            {conversation.case_id: conversation for conversation in conversations}[
                "uat_017"
            ].turns[0].category,
            "fintech_metric_contract",
        )

    def test_evaluate_trace_passes_when_expected_tool_sql_and_result_are_present(self) -> None:
        conversation = UatConversation(
            case_id="case_001",
            turns=(
                UatTurn(
                    case_id="case_001",
                    turn_index=1,
                    category="smoke",
                    question="count customers",
                    expected_behavior=ExpectedBehavior.EXECUTE_SQL,
                    expected_result="42",
                    required_tools=("business_term_search_tool", "run_sql_tool"),
                    required_grounding_ids=("customer_count_grain",),
                    required_tables=("clients",),
                    required_columns=("client_record",),
                    required_sql_contains=("count(distinct",),
                ),
            ),
        )
        trace_path = self._write_trace(
            [
                {"type": "turn_start", "turn_index": 1, "question": "count customers"},
                {
                    "type": "updates",
                    "data": {
                        "model": {
                            "messages": [
                                {
                                    "type": "AIMessage",
                                    "tool_calls": [
                                        {"name": "business_term_search_tool"},
                                        {"name": "run_sql_tool"},
                                    ],
                                }
                            ]
                        }
                    },
                },
                {
                    "type": "messages",
                    "data": [
                        {
                            "type": "ToolMessage",
                            "name": "business_term_search_tool",
                            "content": json.dumps(
                                {
                                    "matches": [
                                        {"id": "customer_count_grain"},
                                    ]
                                }
                            ),
                        }
                    ],
                },
                {
                    "type": "messages",
                    "data": [
                        {
                            "type": "ToolMessage",
                            "name": "run_sql_tool",
                            "content": json.dumps(
                                {
                                    "status": "ok",
                                    "sql": "SELECT COUNT(DISTINCT client_record) FROM clients",
                                    "rows": [{"customer_count": 42}],
                                }
                            ),
                        }
                    ],
                },
                {"type": "turn_end", "turn_index": 1, "final_answer": "42 customers"},
            ]
        )

        evaluation = evaluate_trace(trace_path=trace_path, conversation=conversation)

        self.assertTrue(evaluation.passed, evaluation.failures)

    def test_evaluate_trace_fails_when_sql_runs_for_clarification_case(self) -> None:
        conversation = UatConversation(
            case_id="case_002",
            turns=(
                UatTurn(
                    case_id="case_002",
                    turn_index=1,
                    category="clarify",
                    question="how many active customers",
                    expected_behavior=ExpectedBehavior.CLARIFY,
                ),
            ),
        )
        trace_path = self._write_trace(
            [
                {"type": "turn_start", "turn_index": 1, "question": "how many active customers"},
                {
                    "type": "messages",
                    "data": [
                        {
                            "type": "ToolMessage",
                            "name": "run_sql_tool",
                            "content": json.dumps(
                                {
                                    "status": "ok",
                                    "sql": "SELECT COUNT(*) FROM clients",
                                    "rows": [{"count": 1}],
                                }
                            ),
                        }
                    ],
                },
                {"type": "turn_end", "turn_index": 1, "final_answer": "1"},
            ]
        )

        evaluation = evaluate_trace(trace_path=trace_path, conversation=conversation)

        self.assertFalse(evaluation.passed)
        self.assertIn("expected clarify without SQL execution", evaluation.failures)

    def test_evaluate_trace_allows_sql_for_data_inspection_case(self) -> None:
        conversation = UatConversation(
            case_id="case_003",
            turns=(
                UatTurn(
                    case_id="case_003",
                    turn_index=1,
                    category="inspect",
                    question="what values exist",
                    expected_behavior=ExpectedBehavior.INSPECT_DATA,
                    expected_answer_contains=("M", "F"),
                    required_tools=("profile_column_values_tool",),
                    required_tables=("client_profiles",),
                    required_columns=("gender",),
                ),
            ),
        )
        trace_path = self._write_trace(
            [
                {"type": "turn_start", "turn_index": 1, "question": "what values exist"},
                {
                    "type": "messages",
                    "data": [
                        {
                            "type": "ToolMessage",
                            "name": "profile_column_values_tool",
                            "content": json.dumps({"distinct_values": ["F", "M"]}),
                        }
                    ],
                },
                {
                    "type": "messages",
                    "data": [
                        {
                            "type": "ToolMessage",
                            "name": "run_sql_tool",
                            "content": json.dumps(
                                {
                                    "status": "ok",
                                    "sql": "SELECT gender FROM client_profiles GROUP BY gender",
                                    "rows": [{"gender": "M"}, {"gender": "F"}],
                                }
                            ),
                        }
                    ],
                },
                {"type": "turn_end", "turn_index": 1, "final_answer": "M and F"},
            ]
        )

        evaluation = evaluate_trace(trace_path=trace_path, conversation=conversation)

        self.assertTrue(evaluation.passed, evaluation.failures)

    def _write_trace(self, events: list[dict[str, object]]) -> Path:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / "trace.jsonl"
        path.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )
        return path


if __name__ == "__main__":
    unittest.main()
