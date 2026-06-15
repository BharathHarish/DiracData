from __future__ import annotations

import unittest
from typing import Any

from diracdata_v2.primitive import TypedPrimitiveWorkflow, TypedWorkflowConfig
from diracdata_v2.primitive.runner import PrimitiveRunResult


class FakeRunner:
    def __init__(self, name: str, outputs: list[str]) -> None:
        self.name = name
        self.outputs = list(outputs)
        self.tasks: list[str] = []

    def run(self, task: str, *, context: str | None = None) -> PrimitiveRunResult:
        self.tasks.append(task)
        if not self.outputs:
            raise AssertionError(f"{self.name} has no output left")
        return PrimitiveRunResult(
            output_text=self.outputs.pop(0),
            trace_events=[],
            iterations=1,
            stop_reason="final",
        )


class FakeTool:
    def __init__(self, name: str, payload: dict[str, Any]) -> None:
        self.name = name
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(args)
        return dict(self.payload)


def _intent_ok() -> str:
    return """INTENT_STATUS: OK

INTENT_SUMMARY:
Count distinct customers who bought jewelry online in 2002 and did not buy jewelry online in 2001.

UNRESOLVED_TERMS:
none

ASSUMPTIONS:
none
"""


def _sql_ok(sql: str) -> str:
    return f"""SQL_AUTHOR_STATUS: OK

INTERPRETATION:
Implements the approved intent.

FINAL_SQL:
```sql
{sql}
```

ASSUMPTIONS:
none
"""


def _steward(status: str = "PASS") -> str:
    return f"""STEWARD_STATUS: {status}

EVIDENCE:
intent alignment: passed
schema alignment: passed
dry run: passed

ASSUMPTIONS:
none
"""


def _de_optimized(sql: str) -> str:
    return f"""DE_STATUS: OPTIMIZED

OPTIMIZED_SQL:
```sql
{sql}
```

SEMANTIC_PRESERVATION:
Preserved every filter, exclusion, grain, and output dimension.
"""


def _workflow(
    *,
    intent: FakeRunner | None = None,
    sql_author: FakeRunner | None = None,
    steward: FakeRunner | None = None,
    data_engineer: FakeRunner | None = None,
    context_compiler: Any | None = None,
    config: TypedWorkflowConfig | None = None,
    dry_run: FakeTool | None = None,
    execute: FakeTool | None = None,
) -> TypedPrimitiveWorkflow:
    return TypedPrimitiveWorkflow(
        intent=intent or FakeRunner("intent_subagent", [_intent_ok()]),
        sql_author=sql_author or FakeRunner("sql_author_subagent", []),
        steward=steward or FakeRunner("data_steward_subagent", [_steward()]),
        data_engineer=data_engineer or FakeRunner("data_engineer_subagent", []),
        sql_dry_run_tool=dry_run or FakeTool("sql_dry_run", {"status": "ok"}),
        final_execute_tool=execute or FakeTool(
            "execute_sql",
            {"status": "ok", "columns": ["customers"], "rows": [{"customers": 7}]},
        ),
        context_compiler=context_compiler,
        config=config or TypedWorkflowConfig(enable_data_engineering=False),
    )


class TypedWorkflowTests(unittest.TestCase):
    def test_compiler_clarification_stops_before_model(self) -> None:
        intent = FakeRunner("intent_subagent", [_intent_ok()])

        workflow = _workflow(
            intent=intent,
            context_compiler=lambda _question: {
                "needs_clarification": True,
                "unresolved_terms": [
                    {
                        "term": "did not buy",
                        "reason": "scope ambiguous",
                        "choices": ["same scope", "broader scope"],
                    }
                ],
            },
        )

        result = workflow.run("customers who bought jewelry online in 2002 but did not buy in 2001")

        self.assertEqual(result.stop_reason, "needs_clarification")
        self.assertEqual(intent.tasks, [])
        clarification_events = [event for event in result.trace_events if event.event_type == "clarification_required"]
        self.assertEqual(clarification_events[-1].payload["choices"], ["same scope", "broader scope"])

    def test_steward_failure_forces_sql_repair(self) -> None:
        bad_sql = """
WITH y2002 AS (SELECT 1 AS customer_id),
y2001 AS (SELECT 1 AS customer_id)
SELECT COUNT(DISTINCT y2002.customer_id) AS customers
FROM y2002
JOIN y2001 ON y2002.customer_id = y2001.customer_id
"""
        good_sql = """
WITH y2002 AS (SELECT 1 AS customer_id),
y2001 AS (SELECT 1 AS customer_id)
SELECT COUNT(DISTINCT y2002.customer_id) AS customers
FROM y2002
WHERE NOT EXISTS (
  SELECT 1 FROM y2001 WHERE y2001.customer_id = y2002.customer_id
)
"""
        sql_author = FakeRunner("sql_author_subagent", [_sql_ok(bad_sql), _sql_ok(good_sql)])
        steward = FakeRunner("data_steward_subagent", [_steward("FAIL"), _steward()])
        execute = FakeTool("execute_sql", {"status": "ok", "columns": ["customers"], "rows": [{"customers": 1}]})

        workflow = _workflow(
            sql_author=sql_author,
            steward=steward,
            execute=execute,
            config=TypedWorkflowConfig(max_sql_repairs=1, enable_data_engineering=False),
        )

        result = workflow.run("count customers who bought in 2002 but did not buy in 2001")

        self.assertEqual(result.stop_reason, "final")
        self.assertEqual(len(sql_author.tasks), 2)
        self.assertIn("Steward failed the SQL", sql_author.tasks[1])
        self.assertEqual(len(steward.tasks), 2)
        self.assertEqual(len(execute.calls), 1)

    def test_steward_blocks_not_in_for_negative_cohort(self) -> None:
        unsafe_sql = """
WITH y2002 AS (SELECT 1 AS customer_id),
y2001 AS (SELECT 1 AS customer_id)
SELECT COUNT(DISTINCT customer_id) AS customers
FROM y2002
WHERE customer_id NOT IN (SELECT customer_id FROM y2001)
"""
        steward = FakeRunner("data_steward_subagent", [_steward("FAIL")])
        execute = FakeTool("execute_sql", {"status": "ok", "columns": ["customers"], "rows": [{"customers": 1}]})
        workflow = _workflow(
            sql_author=FakeRunner("sql_author_subagent", [_sql_ok(unsafe_sql)]),
            steward=steward,
            execute=execute,
            config=TypedWorkflowConfig(max_sql_repairs=0, enable_data_engineering=False),
        )

        result = workflow.run("count customers who bought in 2002 but did not buy in 2001")

        self.assertEqual(result.stop_reason, "blocked")
        self.assertIn("Steward failed", result.output_text)
        self.assertEqual(len(steward.tasks), 1)
        self.assertEqual(execute.calls, [])

    def test_role_disambiguation_reaches_steward_without_code_semantic_block(self) -> None:
        intent_text = """INTENT_STATUS: OK

INTENT_SUMMARY:
Websites ranked by promotional revenue from purchases by billing customers, not shipping recipients.

CLAUSE_BINDINGS:
- clause: "customers"
  action_or_entity: Billing customer, not shipping recipient
  status: resolved

BUSINESS_TERMS:
- term: "promotional revenue"
  status: DEFINED
  definition: online purchase revenue where campaign_ref is present

UNRESOLVED_TERMS:
none
"""
        sql = """
WITH promotional_purchases AS (
  SELECT op.name AS website_name, SUM(opu.extended_sales_price) AS promotional_revenue
  FROM online_purchases opu
  JOIN online_properties op ON opu.online_property_ref = op.online_property_record
  WHERE opu.campaign_ref IS NOT NULL
  GROUP BY op.name
)
SELECT website_name, promotional_revenue
FROM promotional_purchases
ORDER BY promotional_revenue DESC
"""
        sql_author = FakeRunner("sql_author_subagent", [_sql_ok(sql)])
        steward = FakeRunner("data_steward_subagent", [_steward()])
        execute = FakeTool("execute_sql", {"status": "ok", "columns": ["website_name"], "rows": [{"website_name": "site"}]})
        workflow = _workflow(
            intent=FakeRunner("intent_subagent", [intent_text]),
            sql_author=sql_author,
            steward=steward,
            execute=execute,
            config=TypedWorkflowConfig(max_sql_repairs=0, enable_data_engineering=False),
        )

        result = workflow.run("Which web sites generated promotional revenue from billing customers, not shipping recipients?")

        self.assertEqual(result.stop_reason, "final")
        self.assertEqual(len(steward.tasks), 1)
        self.assertEqual(len(execute.calls), 1)

    def test_de_optimized_sql_must_pass_steward_before_execution(self) -> None:
        sql = """
WITH y2002 AS (SELECT 1 AS customer_id),
y2001 AS (SELECT 2 AS customer_id),
eligible AS (
  SELECT y2002.customer_id
  FROM y2002
  WHERE NOT EXISTS (SELECT 1 FROM y2001 WHERE y2001.customer_id = y2002.customer_id)
),
final AS (SELECT customer_id FROM eligible)
SELECT COUNT(DISTINCT customer_id) AS customers FROM final
"""
        sql_author = FakeRunner("sql_author_subagent", [_sql_ok(sql)])
        steward = FakeRunner("data_steward_subagent", [_steward(), _steward()])
        data_engineer = FakeRunner("data_engineer_subagent", [_de_optimized(sql)])
        execute = FakeTool("execute_sql", {"status": "ok", "columns": ["customers"], "rows": [{"customers": 1}]})

        workflow = _workflow(
            sql_author=sql_author,
            steward=steward,
            data_engineer=data_engineer,
            execute=execute,
            config=TypedWorkflowConfig(max_sql_repairs=0, enable_data_engineering=True),
        )

        result = workflow.run("count customers who bought in 2002 but did not buy in 2001")

        self.assertEqual(result.stop_reason, "final")
        self.assertEqual(len(data_engineer.tasks), 1)
        self.assertEqual(len(steward.tasks), 2)
        self.assertIn("OPTIMIZED_SQL_UNDER_REVIEW", steward.tasks[1])
        self.assertEqual(len(execute.calls), 1)


if __name__ == "__main__":
    unittest.main()
