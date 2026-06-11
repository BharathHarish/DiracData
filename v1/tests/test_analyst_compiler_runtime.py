from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.agents import create_analyst_compiler
from diracdata.agents.analyst_compiler import (
    CompilerRoute,
    FilterIntent,
    IntentFrame,
    SQLPlan,
    SelectedColumn,
    TimeRangeIntent,
    TruthReport,
    VerificationStatus,
)
from diracdata.config.settings import DiracDataSettings
from diracdata.query_engines.duckdb_runtime import ColumnSchema, QueryResult
from diracdata.storage import LocalObjectStore


class FakeStructuredRunner:
    def __init__(self, *, clarify: bool = False, invalid_first_plan: bool = False) -> None:
        self.clarify = clarify
        self.invalid_first_plan = invalid_first_plan
        self.calls: list[str] = []

    def invoke(self, *, schema: type, system_prompt: str, payload: dict) -> object:
        self.calls.append(schema.__name__)
        if schema is IntentFrame:
            if self.clarify:
                return IntentFrame(
                    normalized_task="count active customers",
                    metrics=["active users"],
                    needs_clarification=True,
                    clarification_questions=["Which calendar month should I use for active customers?"],
                    analyst_notes=["The question lacks a time period."],
                )
            return IntentFrame(
                normalized_task="count female active customers in California",
                metrics=["active users"],
                dimensions=[],
                filters=[
                    FilterIntent(term="gender", value="female", confidence=0.95),
                    FilterIntent(term="state", value="California", confidence=0.95),
                ],
                time_range=TimeRangeIntent(text="current calendar month", grain="month"),
                business_entities=["customers", "female", "California"],
                needs_clarification=False,
            )
        if schema is SQLPlan:
            if self.invalid_first_plan and self.calls.count("SQLPlan") == 1:
                return SQLPlan(
                    route=str(payload.get("route", CompilerRoute.KNOWN_METRIC.value)),
                    base_table="payments",
                    base_grain="payment_attempt",
                    selected_columns=[
                        SelectedColumn(table="payments", column="created_at", purpose="time filter")
                    ],
                    probe_sql=["SELECT COUNT(*) AS base_rows FROM payments WHERE created_at IS NOT NULL"],
                    final_sql="SELECT COUNT(*) AS count FROM payments WHERE created_at IS NOT NULL",
                    risk_notes=["Initial bad plan for dry-run repair regression."],
                    confidence=0.4,
                )
            return SQLPlan(
                route=str(payload.get("route", CompilerRoute.KNOWN_METRIC.value)),
                base_table="payments",
                base_grain="payment_attempt",
                selected_columns=[
                    SelectedColumn(table="payments", column="user_ref", purpose="count active users"),
                    SelectedColumn(table="payments", column="payment_status", purpose="successful payment filter"),
                    SelectedColumn(table="user_attributes", column="gender", purpose="female filter"),
                    SelectedColumn(table="user_attributes", column="state", purpose="California filter"),
                ],
                joins=[
                    {
                        "left_table": "payments",
                        "left_column": "user_ref",
                        "right_table": "user_attributes",
                        "right_column": "user_ref",
                        "join_type": "inner",
                        "reason": "Segment payment users by demographic attributes.",
                    }
                ],
                probe_sql=[
                    "SELECT COUNT(*) AS base_rows FROM payments",
                    (
                        "SELECT COUNT(*) AS joined_rows FROM payments p "
                        "JOIN user_attributes ua ON p.user_ref = ua.user_ref"
                    ),
                ],
                final_sql=(
                    "WITH active_users AS ("
                    "SELECT p.user_ref FROM payments p "
                    "JOIN user_attributes ua ON p.user_ref = ua.user_ref "
                    "WHERE p.payment_status = 'SUCCESS' "
                    "AND ua.gender = 'female' "
                    "AND ua.state = 'California' "
                    "GROUP BY p.user_ref"
                    ") SELECT COUNT(*) AS female_active_customers FROM active_users"
                ),
                assumptions=["Using successful payments for active users."],
                confidence=0.88,
            )
        if schema is TruthReport:
            result = payload["sql_result"]["rows"][0]["female_active_customers"]
            return TruthReport(
                answer=f"There are {result} female active customers in California.",
                verification_status=VerificationStatus.PASSED,
                checks_performed=["validated SQL", "ran row-count probes", "executed final query"],
                caveats=[],
                confidence=0.9,
            )
        raise AssertionError(f"Unexpected schema: {schema}")


class FakeQueryEngine:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def list_tables(self) -> list[str]:
        return ["payments", "user_attributes"]

    def describe_table(self, table_name: str) -> list[ColumnSchema]:
        if table_name == "payments":
            return [
                ColumnSchema("user_ref", "VARCHAR"),
                ColumnSchema("payment_status", "VARCHAR"),
                ColumnSchema("payment_time", "TIMESTAMP"),
            ]
        if table_name == "user_attributes":
            return [
                ColumnSchema("user_ref", "VARCHAR"),
                ColumnSchema("gender", "VARCHAR"),
                ColumnSchema("state", "VARCHAR"),
            ]
        return []

    def row_count(self, table_name: str) -> int:
        return 0

    def query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        if "created_at" in sql:
            raise RuntimeError('Binder Error: table "payments" has no column named "created_at"')
        if max_rows == 0:
            return QueryResult(columns=[], rows=[])
        self.queries.append(sql)
        if "base_rows" in sql:
            return QueryResult(columns=["base_rows"], rows=[(18000,)])
        if "joined_rows" in sql:
            return QueryResult(columns=["joined_rows"], rows=[(18000,)])
        return QueryResult(columns=["female_active_customers"], rows=[(42,)])

    def close(self) -> None:
        pass


class AnalystCompilerRuntimeTest(unittest.TestCase):
    def test_known_metric_route_runs_probes_and_final_sql(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _settings()
            store = LocalObjectStore(tmpdir)
            _write_artifacts(settings, store)
            query_engine = FakeQueryEngine()
            runner = FakeStructuredRunner()
            runtime = create_analyst_compiler(
                settings=settings,
                object_store=store,
                query_engine=query_engine,
                model_runner=runner,
            )

            state = runtime.invoke(
                "How many female active customers are from California?",
                thread_id="compiler-known-metric",
            )

        self.assertEqual(state["route"], CompilerRoute.KNOWN_METRIC.value)
        self.assertEqual(len(query_engine.queries), 3)
        self.assertIn("female_active_customers", state["final_answer"])
        self.assertIn("42", state["final_answer"])
        self.assertEqual(state["truth_report"]["verification_status"], "passed")
        self.assertEqual(runner.calls, ["IntentFrame", "SQLPlan", "TruthReport"])

    def test_clarification_route_skips_sql_planning_and_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _settings()
            store = LocalObjectStore(tmpdir)
            _write_artifacts(settings, store)
            query_engine = FakeQueryEngine()
            runner = FakeStructuredRunner(clarify=True)
            runtime = create_analyst_compiler(
                settings=settings,
                object_store=store,
                query_engine=query_engine,
                model_runner=runner,
            )

            state = runtime.invoke("How many active customers?", thread_id="compiler-clarify")

        self.assertEqual(state["route"], CompilerRoute.CLARIFY.value)
        self.assertEqual(query_engine.queries, [])
        self.assertIn("Which calendar month", state["final_answer"])
        self.assertEqual(runner.calls, ["IntentFrame"])

    def test_dry_run_validation_repairs_unknown_columns_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _settings()
            store = LocalObjectStore(tmpdir)
            _write_artifacts(settings, store)
            query_engine = FakeQueryEngine()
            runner = FakeStructuredRunner(invalid_first_plan=True)
            runtime = create_analyst_compiler(
                settings=settings,
                object_store=store,
                query_engine=query_engine,
                model_runner=runner,
            )

            state = runtime.invoke(
                "How many female active customers are from California?",
                thread_id="compiler-dry-run-repair",
            )

        self.assertEqual(state["repair_attempts"], 1)
        self.assertEqual(state["sql_validations"][-1]["status"], "ok")
        self.assertEqual(runner.calls, ["IntentFrame", "SQLPlan", "SQLPlan", "TruthReport"])
        self.assertFalse(any("created_at" in sql for sql in query_engine.queries))


def _settings() -> DiracDataSettings:
    return DiracDataSettings(
        catalog="fintech_pod",
        database="analytics",
        schema="fintech_schema",
        agent_checkpointer="memory",
        agent_store="memory",
        agent_schema_search_limit=10,
        agent_business_search_limit=10,
        agent_profile_values_limit=5,
        agent_compiler_max_probes=4,
        agent_compiler_probe_max_rows=10,
        agent_compiler_max_repairs=1,
    )


def _write_artifacts(settings: DiracDataSettings, store: LocalObjectStore) -> None:
    base = f"artifacts/learning/{settings.catalog}/{settings.database}/{settings.schema}"
    profile_key = f"{base}/test_run/profiles/table_profiles.json"
    join_pair = {
        "left_table": "payments",
        "left_column": "user_ref",
        "right_table": "user_attributes",
        "right_column": "user_ref",
        "join_type": "many_to_one",
        "confidence": "high",
    }
    store.write_json(
        f"{base}/active/descriptions/metadata_descriptions.json",
        {
            "tables": {
                "payments": {
                    "short_description": "Payment transaction attempts.",
                    "long_description": "Payments store successful and failed payment attempts.",
                },
                "user_attributes": {
                    "short_description": "User demographic and location attributes.",
                    "long_description": "Attributes support segmentation by gender and state.",
                },
            },
            "columns": {
                "payments": {
                    "user_ref": {
                        "short_description": "User involved in the payment.",
                        "long_description": "Connects payments to users and their attributes.",
                    },
                    "payment_status": {
                        "short_description": "Payment outcome.",
                        "long_description": "SUCCESS indicates completed payments.",
                    },
                },
                "user_attributes": {
                    "user_ref": {
                        "short_description": "User represented by these attributes.",
                        "long_description": "Connects attributes to user activity.",
                    },
                    "gender": {
                        "short_description": "User gender segment.",
                        "long_description": "Used for male/female segmentation.",
                    },
                    "state": {
                        "short_description": "User state or region.",
                        "long_description": "Used for geographic segmentation such as California.",
                    },
                },
            },
        },
    )
    store.write_json(
        f"{base}/active/contexts/learned_context.json",
        {
            "run_id": "test_run",
            "profile_artifact_key": profile_key,
            "joinable_pairs_artifact_key": f"{base}/active/joins/joinable_pairs.jsonl",
        },
    )
    store.write_json(
        profile_key,
        {
            "tables": [
                {
                    "table_name": "user_attributes",
                    "columns": [
                        {
                            "column_name": "gender",
                            "data_type": "VARCHAR",
                            "null_rate": 0.0,
                            "distinct_count": 2,
                            "top_values": [{"value": "female", "count": 500}],
                            "distinct_values": ["female", "male"],
                        },
                        {
                            "column_name": "state",
                            "data_type": "VARCHAR",
                            "null_rate": 0.0,
                            "distinct_count": 2,
                            "top_values": [{"value": "California", "count": 100}],
                            "distinct_values": ["California", "Arizona"],
                        },
                    ],
                }
            ]
        },
    )
    store.write_text(
        f"{base}/active/joins/joinable_pairs.jsonl",
        json.dumps(join_pair) + "\n",
    )
    store.write_json(
        f"{base}/active/grounding/business_grounding.json",
        {
            "glossary": [
                {
                    "id": "active_user",
                    "term": "Active user",
                    "definition": "A user with at least one successful payment in the calendar month.",
                }
            ],
            "definitions": [
                {
                    "id": "successful_payment",
                    "name": "Successful payment",
                    "definition": "payment_status = 'SUCCESS'",
                }
            ],
            "defaults": [],
            "metrics": [
                {
                    "id": "active_users",
                    "name": "Active users",
                    "description": "Count users with successful payments.",
                    "calculation": "COUNT(DISTINCT payments.user_ref)",
                }
            ],
            "sql_templates": [],
            "ground_truth_sql": [],
        },
    )


if __name__ == "__main__":
    unittest.main()
