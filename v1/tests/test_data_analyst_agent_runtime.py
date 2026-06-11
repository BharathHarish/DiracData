from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import dataclass

from langchain_core.messages import AIMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import ToolMessage
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langgraph.prebuilt.tool_node import ToolCallRequest

from diracdata.agents import create_data_analyst_agent
from diracdata.agents.artifacts import LearnedArtifactRepository
from diracdata.agents.middleware import (
    DataAnalystMiddlewareConfig,
    SQLReflectionMiddleware,
    answer_shape_violations,
    build_dynamic_system_prompt,
    latest_user_question,
    probe_quality_violations,
    semantic_sql_violations,
    sql_craft_violations,
)
from diracdata.config.settings import DiracDataSettings
from diracdata.learning.paths import active_learning_artifact_key
from diracdata.query_engines.duckdb_runtime import QueryResult
from diracdata.storage import LocalObjectStore


class FakeQueryEngine:
    def list_tables(self) -> list[str]:
        return []

    def describe_table(self, table_name: str) -> list[object]:
        return []

    def row_count(self, table_name: str) -> int:
        return 0

    def query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        return QueryResult(columns=[], rows=[])

    def close(self) -> None:
        pass


@dataclass(frozen=True)
class FakeColumn:
    name: str
    data_type: str


class AmbiguousColumnQueryEngine:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def list_tables(self) -> list[str]:
        return ["payments", "user_attributes"]

    def describe_table(self, table_name: str) -> list[FakeColumn]:
        columns = {
            "payments": [
                FakeColumn("payment_ref", "VARCHAR"),
                FakeColumn("user_ref", "INTEGER"),
            ],
            "user_attributes": [
                FakeColumn("user_ref", "INTEGER"),
                FakeColumn("state", "VARCHAR"),
            ],
        }
        return columns.get(table_name, [])

    def row_count(self, table_name: str) -> int:
        return 10

    def query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        self.queries.append(sql)
        normalized = sql.lower()
        if "COUNT(DISTINCT user_ref)" in sql:
            raise RuntimeError(
                'Binder Error: Ambiguous reference to column name "user_ref" '
                '(use: "p.user_ref" or "ua.user_ref")'
            )
        if "total_attempts" in normalized and "successful_attempts" in normalized:
            return QueryResult(
                columns=["total_attempts", "successful_attempts", "tpv", "psr"],
                rows=[(5, 4, 100.0, 0.8)],
            )
        if "joined_rows" in normalized and "distinct" in normalized:
            return QueryResult(
                columns=["joined_rows", "distinct_payments"],
                rows=[(12, 12)],
            )
        if "base_count" in normalized and "filtered_count" in normalized:
            return QueryResult(
                columns=["base_count", "filtered_count"],
                rows=[(20, 12)],
            )
        if "base_count" in normalized:
            return QueryResult(columns=["base_count"], rows=[(20,)])
        if "max_payment_time" in normalized:
            return QueryResult(columns=["max_payment_time"], rows=[("2026-05-31 12:00:00",)])
        if "group by" in normalized:
            return QueryResult(columns=["payment_status", "rows"], rows=[("SUCCESS", 12)])
        return QueryResult(columns=["user_count"], rows=[(12,)])

    def close(self) -> None:
        pass


class FintechQueryEngine:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def list_tables(self) -> list[str]:
        return ["orders", "payment_attributes", "payments", "user_attributes", "users"]

    def describe_table(self, table_name: str) -> list[FakeColumn]:
        columns = {
            "orders": [
                FakeColumn("order_ref", "VARCHAR"),
                FakeColumn("user_ref", "VARCHAR"),
                FakeColumn("product_area", "VARCHAR"),
                FakeColumn("order_time", "TIMESTAMP"),
            ],
            "payments": [
                FakeColumn("payment_ref", "VARCHAR"),
                FakeColumn("order_ref", "VARCHAR"),
                FakeColumn("user_ref", "VARCHAR"),
                FakeColumn("rail_ref", "VARCHAR"),
                FakeColumn("amount", "DOUBLE"),
                FakeColumn("payment_status", "VARCHAR"),
                FakeColumn("payment_time", "TIMESTAMP"),
            ],
            "users": [
                FakeColumn("user_ref", "VARCHAR"),
                FakeColumn("merchant_type", "VARCHAR"),
            ],
            "user_attributes": [
                FakeColumn("user_ref", "VARCHAR"),
                FakeColumn("state", "VARCHAR"),
                FakeColumn("kyc_status", "VARCHAR"),
            ],
            "payment_attributes": [
                FakeColumn("rail_ref", "VARCHAR"),
                FakeColumn("rail_type", "VARCHAR"),
            ],
        }
        return columns.get(table_name, [])

    def row_count(self, table_name: str) -> int:
        return 10

    def query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        self.queries.append(sql)
        normalized = sql.lower()
        if "joined_rows" in normalized and "distinct" in normalized:
            return QueryResult(
                columns=["joined_rows", "distinct_payments"],
                rows=[(12, 12)],
            )
        if "base_count" in normalized and "filtered_count" in normalized:
            return QueryResult(
                columns=["base_count", "filtered_count"],
                rows=[(20, 12)],
            )
        if "base_count" in normalized:
            return QueryResult(columns=["base_count"], rows=[(20,)])
        if "max_payment_time" in normalized:
            return QueryResult(columns=["max_payment_time"], rows=[("2026-05-31 12:00:00",)])
        if "total_attempts" in normalized and "successful_attempts" in normalized:
            return QueryResult(
                columns=["rail_type", "total_attempts", "successful_attempts", "tpv", "psr_percent"],
                rows=[("UPI", 5, 4, 100.0, 80.0)],
            )
        if "sum(case when" in normalized and "as tpv" in normalized:
            return QueryResult(
                columns=["rail_type", "total_attempts", "successful_attempts", "tpv", "psr_percent"],
                rows=[("UPI", 5, 4, 100.0, 80.0)],
            )
        if "group by" in normalized and "payment_status" in normalized:
            return QueryResult(columns=["payment_status", "rows"], rows=[("SUCCESS", 12)])
        if "group by" in normalized and "rail_type" in normalized:
            return QueryResult(columns=["rail_type", "rows"], rows=[("UPI", 12)])
        if "sum(p.amount)" in normalized and "as tpv" in normalized:
            return QueryResult(columns=["rail_type", "tpv"], rows=[("UPI", 999.0)])
        return QueryResult(columns=["check_value"], rows=[(1,)])

    def close(self) -> None:
        pass


class ToolCallingFakeMessagesListChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: object, **kwargs: object) -> "ToolCallingFakeMessagesListChatModel":
        return self


class FakeReflectionModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.requests: list[object] = []

    def invoke(self, messages: object) -> AIMessage:
        self.requests.append(messages)
        return AIMessage(content=self.responses.pop(0))


class FakeCandidateSearchService:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.queries: list[str] = []

    def search(self, nl_query: str) -> dict[str, object]:
        self.queries.append(nl_query)
        return self.result


class DataAnalystAgentRuntimeTest(unittest.TestCase):
    def test_create_agent_runtime_invokes_langgraph_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="commerce_pod",
                database="analytics",
                schema="main",
                agent_checkpointer="memory",
                agent_store="memory",
            )
            runtime = create_data_analyst_agent(
                settings=settings,
                object_store=LocalObjectStore(tmpdir),
                query_engine=FakeQueryEngine(),
                model=FakeListChatModel(responses=["hello from agent"]),
                tools=[],
                system_prompt="You are a test agent.",
            )

            result = runtime.invoke("hello", thread_id="runtime-test")

        self.assertTrue(hasattr(result, "value"))
        self.assertEqual(result.value["messages"][-1].content, "hello from agent")

    def test_same_thread_preserves_prior_messages_with_checkpointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="commerce_pod",
                database="analytics",
                schema="main",
                agent_checkpointer="memory",
                agent_store="memory",
            )
            runtime = create_data_analyst_agent(
                settings=settings,
                object_store=LocalObjectStore(tmpdir),
                query_engine=FakeQueryEngine(),
                model=FakeListChatModel(responses=["first answer", "second answer"]),
                tools=[],
                system_prompt="You are a test agent.",
            )

            runtime.invoke("first question", thread_id="checkpoint-test")
            result = runtime.invoke("second question", thread_id="checkpoint-test")

        messages = result.value["messages"]
        self.assertEqual([message.content for message in messages], [
            "first question",
            "first answer",
            "second question",
            "second answer",
        ])

    def test_agent_repairs_sql_after_query_engine_error_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = AmbiguousColumnQueryEngine()
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
                agent_checkpointer="memory",
                agent_store="memory",
            )
            bad_sql = (
                "SELECT COUNT(DISTINCT user_ref) AS user_count "
                "FROM payments p JOIN user_attributes ua ON p.user_ref = ua.user_ref"
            )
            fixed_sql = (
                "SELECT COUNT(DISTINCT p.user_ref) AS user_count "
                "FROM payments p JOIN user_attributes ua ON p.user_ref = ua.user_ref"
            )
            model = ToolCallingFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "bad-sql",
                                "name": "run_sql_tool",
                                "args": {"sql": bad_sql},
                            }
                        ],
                    ),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "fixed-sql",
                                "name": "run_sql_tool",
                                "args": {"sql": fixed_sql},
                            }
                        ],
                    ),
                    AIMessage(content="There are 12 matching users."),
                ]
            )
            runtime = create_data_analyst_agent(
                settings=settings,
                object_store=LocalObjectStore(tmpdir),
                query_engine=engine,
                model=model,
                system_prompt="You are a test analyst.",
            )

            result = runtime.invoke("How many users match?", thread_id="sql-repair-test")

        messages = result.value["messages"]
        tool_messages = [message for message in messages if getattr(message, "type", "") == "tool"]
        self.assertEqual(len(engine.queries), 2)
        self.assertIn("COUNT(DISTINCT user_ref)", engine.queries[0])
        self.assertIn("COUNT(DISTINCT p.user_ref)", engine.queries[1])
        self.assertIn("ambiguous_column", tool_messages[0].content)
        self.assertIn("There are 12 matching users.", messages[-1].content)

    def test_sql_guard_forces_tool_use_before_numeric_final_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = AmbiguousColumnQueryEngine()
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
                agent_checkpointer="memory",
                agent_store="memory",
            )
            sql = "SELECT COUNT(DISTINCT p.user_ref) AS user_count FROM payments p"
            model = ToolCallingFakeMessagesListChatModel(
                responses=[
                    AIMessage(content="There are probably 12 users."),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "guarded-sql",
                                "name": "run_sql_tool",
                                "args": {"sql": sql},
                            }
                        ],
                    ),
                    AIMessage(content="There are 12 users."),
                ]
            )
            runtime = create_data_analyst_agent(
                settings=settings,
                object_store=LocalObjectStore(tmpdir),
                query_engine=engine,
                model=model,
                system_prompt="You are a test analyst.",
            )

            result = runtime.invoke("How many users match?", thread_id="sql-guard-test")

        messages = result.value["messages"]
        guard_messages = [
            message
            for message in messages
            if getattr(message, "type", "") == "human"
            and "Runtime guard" in str(getattr(message, "content", ""))
        ]
        self.assertEqual(len(engine.queries), 1)
        self.assertTrue(guard_messages)
        self.assertIn("There are 12 users.", messages[-1].content)

    def test_analyst_protocol_guard_requires_probes_for_complex_metric_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = AmbiguousColumnQueryEngine()
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
                agent_checkpointer="memory",
                agent_store="memory",
            )
            final_sql = (
                "WITH metric_base AS ("
                "SELECT payment_ref, amount, payment_status FROM payments"
                "), final AS ("
                "SELECT COUNT(*) AS total_attempts, "
                "SUM(CASE WHEN payment_status = 'SUCCESS' THEN 1 ELSE 0 END) AS successful_attempts, "
                "SUM(CASE WHEN payment_status = 'SUCCESS' THEN amount ELSE 0 END) AS tpv, "
                "SUM(CASE WHEN payment_status = 'SUCCESS' THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS psr "
                "FROM metric_base"
                ") SELECT * FROM final"
            )
            probe_calls = [
                {
                    "id": "probe-base",
                    "name": "run_sql_tool",
                    "args": {
                        "sql": "SELECT COUNT(*) AS base_count FROM payments",
                        "purpose": "probe",
                        "check_name": "base_population",
                    },
                },
                {
                    "id": "probe-filter",
                    "name": "run_sql_tool",
                    "args": {
                        "sql": (
                            "SELECT COUNT(*) AS base_count, "
                            "COUNT(CASE WHEN payment_status = 'SUCCESS' THEN 1 END) AS filtered_count "
                            "FROM payments"
                        ),
                        "purpose": "probe",
                        "check_name": "filter_selectivity",
                    },
                },
                {
                    "id": "probe-fanout",
                    "name": "run_sql_tool",
                    "args": {
                        "sql": "SELECT COUNT(*) AS joined_rows, COUNT(DISTINCT p.payment_ref) AS distinct_payments FROM payments p JOIN user_attributes ua ON p.user_ref = ua.user_ref",
                        "purpose": "probe",
                        "check_name": "join_fanout",
                    },
                },
                {
                    "id": "probe-freshness",
                    "name": "run_sql_tool",
                    "args": {
                        "sql": "SELECT MAX(payment_time) AS max_payment_time FROM payments",
                        "purpose": "probe",
                        "check_name": "freshness",
                    },
                },
                {
                    "id": "probe-dimension",
                    "name": "run_sql_tool",
                    "args": {
                        "sql": "SELECT payment_status, COUNT(*) AS rows FROM payments GROUP BY payment_status",
                        "purpose": "probe",
                        "check_name": "dimension_quality",
                    },
                },
            ]
            model = ToolCallingFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "final-first",
                                "name": "run_sql_tool",
                                "args": {
                                    "sql": final_sql,
                                    "purpose": "final",
                                    "check_name": "final_result",
                                },
                            }
                        ],
                    ),
                    AIMessage(content="Premature final answer without probes."),
                    AIMessage(content="", tool_calls=probe_calls),
                    AIMessage(content="Verified final answer after probes."),
                ]
            )
            runtime = create_data_analyst_agent(
                settings=settings,
                object_store=LocalObjectStore(tmpdir),
                query_engine=engine,
                model=model,
                system_prompt="You are a test analyst.",
            )

            result = runtime.invoke(
                "Compare May 2026 TPV and PSR by payment rail for verified users.",
                thread_id="analyst-protocol-test",
            )

        messages = result.value["messages"]
        protocol_guard_messages = [
            message
            for message in messages
            if getattr(message, "type", "") == "human"
            and "Runtime analyst protocol guard" in str(getattr(message, "content", ""))
        ]
        self.assertTrue(protocol_guard_messages)
        self.assertIn("Verified final answer after probes.", messages[-1].content)
        self.assertGreaterEqual(len(engine.queries), 6)

    def test_dynamic_prompt_injects_profiled_value_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
                agent_checkpointer="memory",
                agent_store="memory",
            )
            store = LocalObjectStore(tmpdir)
            engine = FintechQueryEngine()
            _write_fintech_profile_artifacts(settings=settings, store=store)

            prompt = build_dynamic_system_prompt(
                config=DataAnalystMiddlewareConfig(
                    settings=settings,
                    repository=LearnedArtifactRepository(settings=settings, object_store=store),
                    object_store=store,
                    query_engine=engine,
                    base_system_prompt="You are a test analyst.",
                ),
                messages=[
                    HumanMessage(
                        "For May 2026 checkout orders from verified users in Maharashtra, compare TPV by rail."
                    )
                ],
            )

        self.assertIn("orders.product_area = 'checkout'", prompt)
        self.assertIn("user_attributes.kyc_status = 'verified'", prompt)
        self.assertIn("user_attributes.state = 'Maharashtra'", prompt)

    def test_dynamic_prompt_injects_candidate_binding_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
            )
            candidate_service = FakeCandidateSearchService(
                {
                    "status": "ok",
                    "predicate_bindings": [
                        {
                            "user_phrase": "low-risk users",
                            "selected_column": "user_attributes.risk_band",
                            "value": "low",
                            "confidence": "high",
                        }
                    ],
                    "rejected_confounders": [
                        {
                            "user_phrase": "low-risk users",
                            "column_ref": "payment_attributes.risk_band",
                            "table_name": "payment_attributes",
                            "column_name": "risk_band",
                            "value": "low",
                        }
                    ],
                    "candidate_columns": [
                        {
                            "column_ref": "user_attributes.risk_band",
                            "confidence_score": 0.03,
                        }
                    ],
                    "search_queries": [{"query": "low-risk users"}],
                }
            )

            prompt = build_dynamic_system_prompt(
                config=DataAnalystMiddlewareConfig(
                    settings=settings,
                    repository=LearnedArtifactRepository(
                        settings=settings,
                        object_store=LocalObjectStore(tmpdir),
                    ),
                    object_store=LocalObjectStore(tmpdir),
                    query_engine=FintechQueryEngine(),
                    base_system_prompt="You are a test analyst.",
                    candidate_search_service=candidate_service,  # type: ignore[arg-type]
                ),
                messages=[
                    HumanMessage(
                        "Compare TPV for verified low-risk users by authentication mode."
                    )
                ],
            )

        self.assertIn("<candidate_binding_context>", prompt)
        self.assertIn("user_attributes.risk_band", prompt)
        self.assertIn("payment_attributes.risk_band", prompt)
        self.assertIn("Do not use `rejected_confounders`", prompt)
        self.assertEqual(candidate_service.queries[-1], "Compare TPV for verified low-risk users by authentication mode.")

    def test_dynamic_prompt_injects_compiled_context_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
                agent_candidate_search_enabled=False,
                agent_context_contract_pattern_limit=1,
                agent_context_contract_invariant_limit=2,
            )
            store = LocalObjectStore(tmpdir)
            _write_compiled_context_artifacts(settings=settings, store=store)

            prompt = build_dynamic_system_prompt(
                config=DataAnalystMiddlewareConfig(
                    settings=settings,
                    repository=LearnedArtifactRepository(settings=settings, object_store=store),
                    object_store=store,
                    query_engine=FintechQueryEngine(),
                    base_system_prompt="You are a test analyst.",
                ),
                messages=[
                    HumanMessage(
                        "Compare TPV and PSR by checkout surface and authentication mode for low-risk users."
                    )
                ],
            )

        self.assertIn("<compiled_context_contract>", prompt)
        self.assertIn("library_pattern:payment_segment", prompt)
        self.assertIn("payments.order_ref = orders.order_ref", prompt)
        self.assertIn("payments.user_ref = orders.user_ref", prompt)
        self.assertIn("Do not choose among confounded columns by name alone.", prompt)
        self.assertNotIn("library_pattern:unrelated", prompt)

    def test_dynamic_prompt_does_not_inline_schema_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="test_pod",
                database="analytics",
                schema="main",
            )
            prompt = build_dynamic_system_prompt(
                config=DataAnalystMiddlewareConfig(
                    settings=settings,
                    repository=LearnedArtifactRepository(
                        settings=settings,
                        object_store=LocalObjectStore(tmpdir),
                    ),
                    object_store=LocalObjectStore(tmpdir),
                    query_engine=FintechQueryEngine(),
                    base_system_prompt="You are a test analyst.",
                ),
                messages=[HumanMessage("Which columns should I use?")],
            )

        self.assertIn("Full schema is not inlined by default", prompt)
        self.assertNotIn("<available_schema>", prompt)
        self.assertNotIn("payment_status", prompt)
        self.assertNotIn("checkout_surface", prompt)

    def test_dynamic_prompt_inlines_schema_only_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="test_pod",
                database="analytics",
                schema="main",
                agent_inline_schema_context=True,
            )
            prompt = build_dynamic_system_prompt(
                config=DataAnalystMiddlewareConfig(
                    settings=settings,
                    repository=LearnedArtifactRepository(
                        settings=settings,
                        object_store=LocalObjectStore(tmpdir),
                    ),
                    object_store=LocalObjectStore(tmpdir),
                    query_engine=FintechQueryEngine(),
                    base_system_prompt="You are a test analyst.",
                ),
                messages=[HumanMessage("Which columns should I use?")],
            )

        self.assertIn("<available_schema>", prompt)
        self.assertIn("payment_status", prompt)
        self.assertIn("product_area", prompt)

    def test_reflection_middleware_blocks_final_sql_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
                agent_reflection_enabled=True,
            )
            store = LocalObjectStore(tmpdir)
            _write_fintech_profile_artifacts(settings=settings, store=store)
            model = FakeReflectionModel(
                [
                    (
                        '{"decision":"revise","confidence":"high","issues":['
                        '{"severity":"blocking","message":"low-risk users are filtered with a payment rail risk predicate",'
                        '"evidence":"user phrase low-risk users",'
                        '"suggested_fix":"Remove payment_attributes.risk_band unless the user asks for rail risk."}'
                        "]}"
                    )
                ]
            )
            middleware = SQLReflectionMiddleware(
                config=DataAnalystMiddlewareConfig(
                    settings=settings,
                    repository=LearnedArtifactRepository(settings=settings, object_store=store),
                    object_store=store,
                    query_engine=FintechQueryEngine(),
                    base_system_prompt="You are a test analyst.",
                    reflection_model=model,
                )
            )
            request = ToolCallRequest(
                tool_call={
                    "id": "final-call",
                    "name": "run_sql_tool",
                    "args": {
                        "sql": (
                            "SELECT COUNT(*) AS total_payments FROM payments p "
                            "JOIN payment_attributes pa ON p.rail_ref = pa.rail_ref "
                            "JOIN user_attributes ua ON p.user_ref = ua.user_ref "
                            "WHERE ua.risk_band = 'low' AND pa.risk_band = 'low'"
                        ),
                        "purpose": "final",
                        "check_name": "final_result",
                    },
                },
                tool=None,
                state={
                    "messages": [
                        HumanMessage(
                            "Compare TPV for verified low-risk users by authentication mode."
                        )
                    ]
                },
                runtime=None,
            )
            executed = False

            def handler(_: object) -> ToolMessage:
                nonlocal executed
                executed = True
                return ToolMessage(
                    content='{"status":"ok","sql":"SELECT 1"}',
                    name="run_sql_tool",
                    tool_call_id="final-call",
                )

            result = middleware.wrap_tool_call(request, handler)

        self.assertFalse(executed)
        self.assertEqual(result.status, "error")
        payload = json.loads(str(result.content))
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error_type"], "semantic_reflection")
        self.assertIn("Remove payment_attributes.risk_band", payload["repair_instruction"])
        self.assertEqual(len(model.requests), 1)

    def test_reflection_middleware_allows_final_sql_when_review_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
                agent_reflection_enabled=True,
            )
            store = LocalObjectStore(tmpdir)
            model = FakeReflectionModel(
                ['{"decision":"allow","confidence":"medium","issues":[]}']
            )
            middleware = SQLReflectionMiddleware(
                config=DataAnalystMiddlewareConfig(
                    settings=settings,
                    repository=LearnedArtifactRepository(settings=settings, object_store=store),
                    object_store=store,
                    query_engine=FintechQueryEngine(),
                    base_system_prompt="You are a test analyst.",
                    reflection_model=model,
                )
            )
            request = ToolCallRequest(
                tool_call={
                    "id": "final-call",
                    "name": "run_sql_tool",
                    "args": {
                        "sql": "SELECT COUNT(*) AS total_payments FROM payments",
                        "purpose": "final",
                        "check_name": "final_result",
                    },
                },
                tool=None,
                state={"messages": [HumanMessage("How many payments?")]},
                runtime=None,
            )
            executed = False

            def handler(_: object) -> ToolMessage:
                nonlocal executed
                executed = True
                return ToolMessage(
                    content='{"status":"ok","sql":"SELECT COUNT(*) AS total_payments FROM payments"}',
                    name="run_sql_tool",
                    tool_call_id="final-call",
                )

            result = middleware.wrap_tool_call(request, handler)

        self.assertTrue(executed)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(model.requests), 1)

    def test_reflection_middleware_bypasses_probe_sql(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(agent_reflection_enabled=True)
            store = LocalObjectStore(tmpdir)
            model = FakeReflectionModel(
                ['{"decision":"revise","confidence":"high","issues":[]}']
            )
            middleware = SQLReflectionMiddleware(
                config=DataAnalystMiddlewareConfig(
                    settings=settings,
                    repository=LearnedArtifactRepository(settings=settings, object_store=store),
                    object_store=store,
                    query_engine=FintechQueryEngine(),
                    base_system_prompt="You are a test analyst.",
                    reflection_model=model,
                )
            )
            request = ToolCallRequest(
                tool_call={
                    "id": "probe-call",
                    "name": "run_sql_tool",
                    "args": {
                        "sql": "SELECT COUNT(*) AS base_count FROM payments",
                        "purpose": "probe",
                        "check_name": "base_population",
                    },
                },
                tool=None,
                state={"messages": [HumanMessage("Compare TPV by rail.")]},
                runtime=None,
            )
            executed = False

            def handler(_: object) -> ToolMessage:
                nonlocal executed
                executed = True
                return ToolMessage(
                    content='{"status":"ok","sql":"SELECT COUNT(*) AS base_count FROM payments"}',
                    name="run_sql_tool",
                    tool_call_id="probe-call",
                )

            result = middleware.wrap_tool_call(request, handler)

        self.assertTrue(executed)
        self.assertEqual(result.status, "success")
        self.assertEqual(model.requests, [])

    def test_semantic_guard_rejects_bad_final_sql_that_drops_metric_and_value_grounding(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
                agent_checkpointer="memory",
                agent_store="memory",
            )
            store = LocalObjectStore(tmpdir)
            _write_fintech_profile_artifacts(settings=settings, store=store)
            engine = FintechQueryEngine()
            bad_final_sql = (
                "SELECT pa.rail_type, SUM(p.amount) AS tpv, "
                "SUM(CASE WHEN p.payment_status = 'SUCCESS' THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS psr "
                "FROM payments p "
                "JOIN orders o ON p.order_ref = o.order_ref "
                "JOIN user_attributes ua ON p.user_ref = ua.user_ref "
                "JOIN payment_attributes pa ON p.rail_ref = pa.rail_ref "
                "WHERE p.payment_time >= '2026-05-01' AND p.payment_time < '2026-06-01' "
                "AND ua.state = 'Maharashtra' AND ua.kyc_status = 'verified' "
                "GROUP BY pa.rail_type"
            )
            good_final_sql = (
                "WITH payment_base AS ("
                "SELECT p.payment_ref, p.order_ref, p.user_ref, p.rail_ref, p.amount, p.payment_status "
                "FROM payments p "
                "WHERE p.payment_time >= '2026-05-01' AND p.payment_time < '2026-06-01'"
                "), joined_context AS ("
                "SELECT pb.payment_ref, pb.amount, pb.payment_status, pa.rail_type "
                "FROM payment_base pb "
                "JOIN orders o ON pb.order_ref = o.order_ref "
                "JOIN user_attributes ua ON pb.user_ref = ua.user_ref "
                "JOIN payment_attributes pa ON pb.rail_ref = pa.rail_ref "
                "WHERE o.product_area = 'checkout' "
                "AND ua.state = 'Maharashtra' AND ua.kyc_status = 'verified'"
                "), final AS ("
                "SELECT rail_type, COUNT(*) AS total_attempts, "
                "SUM(CASE WHEN payment_status = 'SUCCESS' THEN 1 ELSE 0 END) AS successful_attempts, "
                "SUM(CASE WHEN payment_status = 'SUCCESS' THEN amount ELSE 0 END) AS tpv, "
                "SUM(CASE WHEN payment_status = 'SUCCESS' THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS psr "
                "FROM joined_context GROUP BY rail_type"
                ") SELECT * FROM final"
            )
            probe_calls = [
                {
                    "id": "probe-base",
                    "name": "run_sql_tool",
                    "args": {
                        "sql": "SELECT COUNT(*) AS base_count FROM payments WHERE payment_time >= '2026-05-01' AND payment_time < '2026-06-01'",
                        "purpose": "probe",
                        "check_name": "base_population",
                    },
                },
                {
                    "id": "probe-filter",
                    "name": "run_sql_tool",
                    "args": {
                        "sql": (
                            "SELECT COUNT(*) AS base_count, "
                            "COUNT(CASE WHEN o.product_area = 'checkout' THEN 1 END) AS filtered_count "
                            "FROM payments p JOIN orders o ON p.order_ref = o.order_ref "
                            "WHERE o.product_area = 'checkout'"
                        ),
                        "purpose": "probe",
                        "check_name": "filter_selectivity",
                    },
                },
                {
                    "id": "probe-fanout",
                    "name": "run_sql_tool",
                    "args": {
                        "sql": "SELECT COUNT(*) AS joined_rows, COUNT(DISTINCT p.payment_ref) AS distinct_payments FROM payments p JOIN orders o ON p.order_ref = o.order_ref",
                        "purpose": "probe",
                        "check_name": "join_fanout",
                    },
                },
                {
                    "id": "probe-freshness",
                    "name": "run_sql_tool",
                    "args": {
                        "sql": "SELECT MAX(payment_time) AS max_payment_time FROM payments",
                        "purpose": "probe",
                        "check_name": "freshness",
                    },
                },
                {
                    "id": "probe-dimension",
                    "name": "run_sql_tool",
                    "args": {
                        "sql": "SELECT rail_type, COUNT(*) AS rows FROM payment_attributes GROUP BY rail_type",
                        "purpose": "probe",
                        "check_name": "dimension_quality",
                    },
                },
            ]
            model = ToolCallingFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "bad-final",
                                "name": "run_sql_tool",
                                "args": {
                                    "sql": bad_final_sql,
                                    "purpose": "final",
                                    "check_name": "final_result",
                                },
                            }
                        ],
                    ),
                    AIMessage(content="Premature final answer from bad SQL."),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "good-final",
                                "name": "run_sql_tool",
                                "args": {
                                    "sql": good_final_sql,
                                    "purpose": "final",
                                    "check_name": "final_result",
                                },
                            },
                        ],
                    ),
                    AIMessage(content="Repaired final SQL, awaiting probe verification."),
                    AIMessage(content="", tool_calls=probe_calls),
                    AIMessage(content="Verified final answer after semantic repair."),
                ]
            )
            runtime = create_data_analyst_agent(
                settings=settings,
                object_store=store,
                query_engine=engine,
                model=model,
                system_prompt="You are a test analyst.",
            )

            result = runtime.invoke(
                "For May 2026 checkout orders from verified users in Maharashtra, compare TPV and PSR by payment rail.",
                thread_id="semantic-guard-test",
            )

        messages = result.value["messages"]
        semantic_guard_messages = [
            message
            for message in messages
            if getattr(message, "type", "") == "human"
            and "Runtime semantic SQL guard" in str(getattr(message, "content", ""))
        ]
        self.assertTrue(semantic_guard_messages)
        self.assertIn("conditional aggregate", semantic_guard_messages[0].content)
        self.assertIn("'checkout'", semantic_guard_messages[0].content)
        self.assertIn("Verified final answer after semantic repair.", messages[-1].content)
        self.assertIn("SUM(p.amount) AS tpv", engine.queries[0])
        self.assertIn("o.product_area = 'checkout'", "\n".join(engine.queries))

    def test_answer_shape_guard_detects_collapsed_two_dimension_result(self) -> None:
        evidence = _two_dimension_final_result_evidence()

        violations = answer_shape_violations(
            question="Compare TPV by merchant type and payment rail.",
            evidence=evidence,
            answer=(
                "By merchant type: enterprise has 3 attempts. "
                "By rail: UPI has 4 attempts."
            ),
        )

        self.assertTrue(violations)
        self.assertIn("enterprise x CC", violations[0])

    def test_answer_shape_guard_allows_row_preserving_two_dimension_result(self) -> None:
        evidence = _two_dimension_final_result_evidence()

        violations = answer_shape_violations(
            question="Compare TPV by merchant type and payment rail.",
            evidence=evidence,
            answer=(
                "| Merchant Type | Rail | TPV |\n"
                "| enterprise | CC | 15504.00 |\n"
                "| enterprise | UPI | 30412.81 |\n"
                "| platform | NEFT | 0.00 |"
            ),
        )

        self.assertEqual(violations, [])

    def test_answer_shape_guard_rejects_unsupported_summary_numbers(self) -> None:
        evidence = {
            "latest_final_sql": {
                "status": "ok",
                "purpose": "final",
                "check_name": "final_result",
                "columns": [
                    "segment",
                    "channel",
                    "qualified_count",
                    "total_count",
                    "conversion_rate",
                ],
                "rows": [
                    {
                        "segment": "enterprise",
                        "channel": "api",
                        "qualified_count": 3,
                        "total_count": 5,
                        "conversion_rate": 0.6,
                    },
                    {
                        "segment": "startup",
                        "channel": "web",
                        "qualified_count": 2,
                        "total_count": 5,
                        "conversion_rate": 0.4,
                    },
                ],
                "row_count": 2,
            }
        }

        violations = answer_shape_violations(
            question="Compare conversion rate by segment and channel.",
            evidence=evidence,
            answer=(
                "| segment | channel | qualified_count | total_count | conversion_rate |\n"
                "| enterprise | api | 3 | 5 | 60.0% |\n"
                "| startup | web | 2 | 5 | 40.0% |\n\n"
                "Data scope: 4 qualified records across 2 grouped rows from 10 total records."
            ),
        )

        self.assertTrue(violations)
        self.assertTrue(any("4" in violation for violation in violations))

    def test_semantic_guard_enforces_grounding_required_time_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
                agent_checkpointer="memory",
                agent_store="memory",
            )
            store = LocalObjectStore(tmpdir)
            _write_fintech_profile_artifacts(settings=settings, store=store)
            config = DataAnalystMiddlewareConfig(
                settings=settings,
                repository=LearnedArtifactRepository(settings=settings, object_store=store),
                object_store=store,
                query_engine=FintechQueryEngine(),
                base_system_prompt="You are a test analyst.",
            )
            evidence = {
                "latest_final_sql": {
                    "sql": (
                        "SELECT SUM(CASE WHEN p.payment_status = 'SUCCESS' THEN p.amount ELSE 0 END) AS tpv "
                        "FROM payments p JOIN orders o ON p.order_ref = o.order_ref "
                        "WHERE o.order_time >= '2026-05-01' AND o.order_time < '2026-06-01' "
                        "AND o.product_area = 'checkout'"
                    )
                }
            }

            violations = semantic_sql_violations(
                config=config,
                question="Compare TPV and PSR for May 2026 checkout orders.",
                evidence=evidence,
            )

        self.assertTrue(any("payments.payment_time" in violation for violation in violations))

    def test_typed_metric_contract_blocks_bad_tpv_without_noisy_month_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
            )
            store = LocalObjectStore(tmpdir)
            _write_fintech_profile_artifacts(settings=settings, store=store)
            config = DataAnalystMiddlewareConfig(
                settings=settings,
                repository=LearnedArtifactRepository(settings=settings, object_store=store),
                object_store=store,
                query_engine=FintechQueryEngine(),
                base_system_prompt="You are a test analyst.",
            )
            question = (
                "For April 2026 checkout orders from verified low-risk users in Karnataka "
                "on active merchant accounts, compare TPV and payment success rate by "
                "checkout surface and authentication mode. Only include segments with at "
                "least 5 payment attempts."
            )
            bad_tpv_evidence = {
                "latest_final_sql": {
                    "status": "ok",
                    "purpose": "final",
                    "check_name": "final_result",
                    "sql": (
                        "WITH final AS ("
                        "SELECT o.checkout_surface, pa.authentication_mode, p.amount, p.payment_status "
                        "FROM payments p "
                        "JOIN orders o ON p.order_ref = o.order_ref "
                        "JOIN user_attributes ua ON p.user_ref = ua.user_ref "
                        "JOIN users u ON p.user_ref = u.user_ref "
                        "JOIN payment_attributes pa ON p.rail_ref = pa.rail_ref "
                        "WHERE p.payment_time >= '2026-04-01' AND p.payment_time < '2026-05-01' "
                        "AND o.product_area = 'checkout' "
                        "AND ua.kyc_status = 'verified' "
                        "AND ua.state = 'Karnataka' "
                        "AND u.account_state = 'active'"
                        ") SELECT checkout_surface, authentication_mode, "
                        "SUM(amount) AS tpv, "
                        "SUM(CASE WHEN payment_status = 'SUCCESS' THEN 1 ELSE 0 END) AS successful_payments, "
                        "COUNT(*) AS total_payments, "
                        "SUM(CASE WHEN payment_status = 'SUCCESS' THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS psr "
                        "FROM final GROUP BY checkout_surface, authentication_mode HAVING COUNT(*) >= 5"
                    ),
                }
            }

            violations = semantic_sql_violations(
                config=config,
                question=question,
                evidence=bad_tpv_evidence,
            )

        self.assertTrue(any("canonical metric SQL" in violation for violation in violations))
        self.assertFalse(any("mau_calendar_month" in violation for violation in violations))
        self.assertFalse(any("retained_users_past_3_months" in violation for violation in violations))
        self.assertFalse(any("literal value 'month'" in violation for violation in violations))

    def test_metric_denominator_contract_blocks_base_status_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
            )
            store = LocalObjectStore(tmpdir)
            _write_fintech_profile_artifacts(settings=settings, store=store)
            config = DataAnalystMiddlewareConfig(
                settings=settings,
                repository=LearnedArtifactRepository(settings=settings, object_store=store),
                object_store=store,
                query_engine=FintechQueryEngine(),
                base_system_prompt="You are a test analyst.",
            )
            question = (
                "For April 2026 checkout orders from verified low-risk users in Karnataka "
                "on active merchant accounts, compare TPV and payment success rate by "
                "checkout surface and authentication mode. Only include segments with at "
                "least 5 payment attempts."
            )
            evidence = {
                "latest_final_sql": {
                    "status": "ok",
                    "purpose": "final",
                    "check_name": "final_result",
                    "sql": (
                        "WITH filtered_payments AS ("
                        "SELECT p.payment_ref, p.order_ref, p.user_ref, p.rail_ref, p.amount, p.payment_status "
                        "FROM payments p "
                        "WHERE p.payment_time >= '2026-04-01' AND p.payment_time < '2026-05-01' "
                        "AND p.payment_status IN ('SUCCESS', 'FAILED')"
                        "), joined AS ("
                        "SELECT o.checkout_surface, pa.authentication_mode, fp.payment_status, fp.amount "
                        "FROM filtered_payments fp "
                        "JOIN orders o ON fp.order_ref = o.order_ref "
                        "JOIN payment_attributes pa ON fp.rail_ref = pa.rail_ref "
                        "JOIN user_attributes ua ON fp.user_ref = ua.user_ref "
                        "JOIN users u ON fp.user_ref = u.user_ref "
                        "WHERE o.product_area = 'checkout' "
                        "AND ua.kyc_status = 'verified' "
                        "AND ua.state = 'Karnataka' "
                        "AND u.account_state = 'active'"
                        ") SELECT checkout_surface, authentication_mode, "
                        "SUM(CASE WHEN payment_status = 'SUCCESS' THEN amount ELSE 0 END) AS tpv, "
                        "SUM(CASE WHEN payment_status = 'SUCCESS' THEN 1 ELSE 0 END) AS successful_payments, "
                        "COUNT(*) AS total_attempts, "
                        "SUM(CASE WHEN payment_status = 'SUCCESS' THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS psr "
                        "FROM joined GROUP BY checkout_surface, authentication_mode HAVING COUNT(*) >= 5"
                    ),
                }
            }

            violations = semantic_sql_violations(
                config=config,
                question=question,
                evidence=evidence,
            )

        self.assertTrue(any("denominator counts all rows" in violation for violation in violations))
        self.assertTrue(any("payments.payment_status" in violation for violation in violations))

    def test_semantic_guard_blocks_rejected_candidate_confounder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
            )
            candidate_service = FakeCandidateSearchService(
                {
                    "status": "ok",
                    "predicate_bindings": [
                        {
                            "user_phrase": "low-risk users",
                            "selected_column": "user_attributes.risk_band",
                            "value": "low",
                            "confidence": "high",
                        }
                    ],
                    "rejected_confounders": [
                        {
                            "user_phrase": "low-risk users",
                            "column_ref": "payment_attributes.risk_band",
                            "table_name": "payment_attributes",
                            "column_name": "risk_band",
                            "value": "low",
                            "reason": "Payment rail risk is not user risk.",
                        }
                    ],
                }
            )
            config = DataAnalystMiddlewareConfig(
                settings=settings,
                repository=LearnedArtifactRepository(
                    settings=settings,
                    object_store=LocalObjectStore(tmpdir),
                ),
                object_store=LocalObjectStore(tmpdir),
                query_engine=FintechQueryEngine(),
                base_system_prompt="You are a test analyst.",
                candidate_search_service=candidate_service,  # type: ignore[arg-type]
            )
            evidence = {
                "latest_final_sql": {
                    "status": "ok",
                    "purpose": "final",
                    "check_name": "final_result",
                    "sql": (
                        "SELECT pa.authentication_mode, COUNT(*) AS total_attempts "
                        "FROM payments p "
                        "JOIN payment_attributes pa ON p.rail_ref = pa.rail_ref "
                        "JOIN user_attributes ua ON p.user_ref = ua.user_ref "
                        "WHERE ua.risk_band = 'low' AND pa.risk_band = 'low' "
                        "GROUP BY pa.authentication_mode"
                    ),
                }
            }

            violations = semantic_sql_violations(
                config=config,
                question="Compare TPV for low-risk users by authentication mode.",
                evidence=evidence,
            )

        self.assertTrue(any("Candidate binding rejected payment_attributes.risk_band" in item for item in violations))

    def test_semantic_guard_allows_selected_candidate_with_same_column_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
            )
            candidate_service = FakeCandidateSearchService(
                {
                    "status": "ok",
                    "predicate_bindings": [
                        {
                            "user_phrase": "low-risk users",
                            "selected_column": "user_attributes.risk_band",
                            "value": "low",
                            "confidence": "high",
                        }
                    ],
                    "rejected_confounders": [
                        {
                            "user_phrase": "low-risk users",
                            "column_ref": "payment_attributes.risk_band",
                            "table_name": "payment_attributes",
                            "column_name": "risk_band",
                            "value": "low",
                            "reason": "Payment rail risk is not user risk.",
                        }
                    ],
                }
            )
            config = DataAnalystMiddlewareConfig(
                settings=settings,
                repository=LearnedArtifactRepository(
                    settings=settings,
                    object_store=LocalObjectStore(tmpdir),
                ),
                object_store=LocalObjectStore(tmpdir),
                query_engine=FintechQueryEngine(),
                base_system_prompt="You are a test analyst.",
                candidate_search_service=candidate_service,  # type: ignore[arg-type]
            )
            evidence = {
                "latest_final_sql": {
                    "status": "ok",
                    "purpose": "final",
                    "check_name": "final_result",
                    "sql": (
                        "SELECT pa.authentication_mode, COUNT(*) AS total_attempts "
                        "FROM payments p "
                        "JOIN payment_attributes pa ON p.rail_ref = pa.rail_ref "
                        "JOIN user_attributes ua ON p.user_ref = ua.user_ref "
                        "WHERE ua.risk_band = 'low' "
                        "GROUP BY pa.authentication_mode"
                    ),
                }
            }

            violations = semantic_sql_violations(
                config=config,
                question="Compare TPV for low-risk users by authentication mode.",
                evidence=evidence,
            )

        self.assertFalse(any("Candidate binding rejected payment_attributes.risk_band" in item for item in violations))

    def test_probe_quality_accepts_base_population_column_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
            )
            store = LocalObjectStore(tmpdir)
            _write_fintech_profile_artifacts(settings=settings, store=store)
            config = DataAnalystMiddlewareConfig(
                settings=settings,
                repository=LearnedArtifactRepository(settings=settings, object_store=store),
                object_store=store,
                query_engine=FintechQueryEngine(),
                base_system_prompt="You are a test analyst.",
            )
            evidence = {
                "sql_observations": [
                    {
                        "status": "ok",
                        "purpose": "probe",
                        "check_name": "base_population",
                        "sql": "SELECT COUNT(*) AS base_population FROM payments",
                        "columns": ["base_population"],
                        "rows": [{"base_population": 2880}],
                    },
                ],
            }

            violations = probe_quality_violations(
                config=config,
                question="Compare TPV and PSR by payment rail.",
                evidence=evidence,
            )

        self.assertFalse(any("base_population" in violation for violation in violations))

    def test_probe_quality_rejects_shallow_filter_and_join_probes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
            )
            store = LocalObjectStore(tmpdir)
            _write_fintech_profile_artifacts(settings=settings, store=store)
            config = DataAnalystMiddlewareConfig(
                settings=settings,
                repository=LearnedArtifactRepository(settings=settings, object_store=store),
                object_store=store,
                query_engine=FintechQueryEngine(),
                base_system_prompt="You are a test analyst.",
            )
            evidence = {
                "sql_observations": [
                    {
                        "status": "ok",
                        "purpose": "probe",
                        "check_name": "base_population",
                        "sql": "SELECT COUNT(*) AS base_count FROM payments",
                        "columns": ["base_count"],
                        "rows": [{"base_count": 20}],
                    },
                    {
                        "status": "ok",
                        "purpose": "probe",
                        "check_name": "filter_selectivity",
                        "sql": "SELECT COUNT(*) AS filtered_count FROM payments",
                        "columns": ["filtered_count"],
                        "rows": [{"filtered_count": 12}],
                    },
                    {
                        "status": "ok",
                        "purpose": "probe",
                        "check_name": "join_fanout",
                        "sql": "SELECT COUNT(*) AS joined_rows FROM payments p JOIN orders o ON p.order_ref = o.order_ref",
                        "columns": ["joined_rows"],
                        "rows": [{"joined_rows": 12}],
                    },
                ],
                "completed_probe_checks": [
                    "base_population",
                    "filter_selectivity",
                    "join_fanout",
                ],
            }

            violations = probe_quality_violations(
                config=config,
                question="Compare May 2026 checkout TPV and PSR by rail for verified users.",
                evidence=evidence,
            )

        self.assertTrue(any("filter_selectivity" in violation for violation in violations))
        self.assertTrue(any("join_fanout" in violation for violation in violations))

    def test_sql_craft_requires_ctes_and_ratio_evidence_columns(self) -> None:
        violations = sql_craft_violations(
            question="Compare revenue and conversion rate by region and channel.",
            evidence={
                "latest_final_sql": {
                    "status": "ok",
                    "purpose": "final",
                    "check_name": "final_result",
                    "sql": (
                        "SELECT region, channel, "
                        "SUM(revenue) AS revenue, AVG(converted) AS conversion_rate "
                        "FROM funnel_events GROUP BY region, channel"
                    ),
                    "columns": [
                        "region",
                        "channel",
                        "revenue",
                        "conversion_rate",
                    ],
                    "rows": [],
                }
            },
        )

        self.assertTrue(any("named CTEs" in violation for violation in violations))
        self.assertTrue(any("denominator" in violation for violation in violations))
        self.assertTrue(any("numerator" in violation for violation in violations))

    def test_runtime_guard_messages_do_not_replace_latest_user_question(self) -> None:
        question = "Compare May 2026 checkout TPV and PSR by rail."

        latest = latest_user_question(
            [
                HumanMessage(question),
                HumanMessage("Runtime semantic SQL guard: final answer blocked."),
                HumanMessage("Runtime SQL craft guard: final answer blocked."),
            ]
        )

        self.assertEqual(latest, question)


def _write_fintech_profile_artifacts(
    *,
    settings: DiracDataSettings,
    store: LocalObjectStore,
) -> None:
    profile_key = active_learning_artifact_key(
        settings,
        relative_path="profiles/table_profiles.json",
    )
    context_key = active_learning_artifact_key(
        settings,
        relative_path="contexts/learned_context.json",
    )
    store.write_json(
        profile_key,
        {
            "tables": [
                {
                    "table_name": "orders",
                    "columns": [
                        {
                            "column_name": "product_area",
                            "top_values": [{"value": "checkout", "count": 10}],
                            "distinct_values": ["checkout", "payments"],
                        }
                    ],
                },
                {
                    "table_name": "user_attributes",
                    "columns": [
                        {
                            "column_name": "kyc_status",
                            "top_values": [{"value": "verified", "count": 8}],
                            "distinct_values": ["verified", "pending"],
                        },
                        {
                            "column_name": "state",
                            "top_values": [{"value": "Maharashtra", "count": 7}],
                            "distinct_values": ["Maharashtra", "Karnataka"],
                        },
                    ],
                },
            ]
        },
    )
    store.write_json(context_key, {"profile_artifact_key": profile_key})
    store.write_json(
        active_learning_artifact_key(
            settings,
            relative_path="grounding/business_grounding.json",
        ),
        {
            "version": 1,
            "scope": {
                "catalog": settings.catalog,
                "database": settings.database,
                "schema": settings.schema,
            },
            "glossary": [],
            "definitions": [
                {
                    "id": "successful_payment",
                    "name": "Successful payment",
                    "definition": "A payment attempt whose payment_status is SUCCESS.",
                    "tables": ["payments"],
                    "columns": ["payments.payment_status"],
                }
            ],
            "defaults": [
                {
                    "id": "calendar_month_from_payment_time",
                    "applies_to": ["calendar month", "monthly"],
                    "policy": "Use date_trunc('month', payments.payment_time) for calendar-month grouping.",
                    "field": "payments.payment_time",
                },
                {
                    "id": "product_filter_uses_order_area",
                    "applies_to": ["checkout"],
                    "policy": "Use orders.product_area = 'checkout' when the user asks for checkout orders.",
                    "field": "orders.product_area",
                },
            ],
            "metrics": [
                {
                    "id": "tpv",
                    "name": "TPV",
                    "synonyms": ["total payment volume", "payment volume"],
                    "description": "Total payment volume for the requested period or slice.",
                    "calculation": "SUM(payments.amount) where payments.payment_status = 'SUCCESS'",
                    "parameterized_sql": {
                        "sql": (
                            "SELECT SUM(CASE WHEN p.payment_status = 'SUCCESS' THEN p.amount ELSE 0 END) AS tpv "
                            "FROM payments p "
                            "WHERE p.payment_time >= {{ start_time }} AND p.payment_time < {{ end_time }}"
                        ),
                        "required_tables": ["payments"],
                        "required_columns": [
                            "payments.amount",
                            "payments.payment_status",
                            "payments.payment_time",
                        ],
                        "sql_contract": {
                            "aggregate": "sum",
                            "measure": "payments.amount",
                            "condition": {
                                "column": "payments.payment_status",
                                "operator": "=",
                                "value": "SUCCESS",
                            },
                            "time_column": "payments.payment_time",
                        },
                    },
                    "tables": ["payments"],
                    "columns": [
                        "payments.amount",
                        "payments.payment_status",
                        "payments.payment_time",
                    ],
                },
                {
                    "id": "psr",
                    "name": "PSR",
                    "synonyms": ["payment success rate", "success rate"],
                    "description": "Payment success rate for a requested period or slice.",
                    "calculation": "COUNT_IF(payments.payment_status = 'SUCCESS') / COUNT(*)",
                    "parameterized_sql": {
                        "sql": (
                            "SELECT SUM(CASE WHEN p.payment_status = 'SUCCESS' THEN 1 ELSE 0 END) AS successful_payments, "
                            "COUNT(*) AS total_payments, "
                            "SUM(CASE WHEN p.payment_status = 'SUCCESS' THEN 1 ELSE 0 END)::DOUBLE / NULLIF(COUNT(*), 0) AS psr "
                            "FROM payments p "
                            "WHERE p.payment_time >= {{ start_time }} AND p.payment_time < {{ end_time }}"
                        ),
                        "required_tables": ["payments"],
                        "required_columns": [
                            "payments.payment_status",
                            "payments.payment_time",
                        ],
                        "sql_contract": {
                            "numerator": {
                                "aggregate": "count",
                                "condition": {
                                    "column": "payments.payment_status",
                                    "operator": "=",
                                    "value": "SUCCESS",
                                },
                            },
                        "denominator": {
                            "aggregate": "count",
                            "grain": "payments.payment_ref",
                            "forbidden_base_filters": [
                                {
                                    "column": "payments.payment_status",
                                    "reason": (
                                        "PSR denominator is all payment attempts after user filters; "
                                        "do not filter payment_status in WHERE or base CTEs. "
                                        "Apply SUCCESS only inside numerator aggregates."
                                    ),
                                }
                            ],
                        },
                        "time_column": "payments.payment_time",
                    },
                    },
                    "tables": ["payments"],
                    "columns": [
                        "payments.payment_status",
                        "payments.payment_time",
                    ],
                },
            ],
            "sql_templates": [
                {
                    "id": "mau_calendar_month",
                    "name": "MAU calendar month",
                    "description": "Use only for monthly active users.",
                    "required_tables": ["payments"],
                    "sql": "SELECT date_trunc('month', payment_time) AS month FROM payments",
                },
                {
                    "id": "retained_users_past_3_months",
                    "name": "Retained users past 3 months",
                    "description": "Use only for retained users.",
                    "required_tables": ["payments"],
                    "sql": "SELECT date_trunc('month', payment_time) AS month FROM payments",
                },
            ],
            "ground_truth_sql": [],
        },
    )


def _write_compiled_context_artifacts(
    *,
    settings: DiracDataSettings,
    store: LocalObjectStore,
) -> None:
    base_context_key = active_learning_artifact_key(
        settings,
        relative_path="contexts/learned_context.json",
    )
    store.write_json(base_context_key, {"run_id": "compiled_context_test"})
    pattern_rows = [
        {
            "id": "library_pattern:payment_segment",
            "query_count": 12,
            "compact_contract": {
                "fact_table": "payments",
                "metrics": ["tpv", "psr"],
                "tables": ["payments", "orders", "user_attributes", "payment_attributes"],
                "dimension_columns": [
                    "orders.checkout_surface",
                    "payment_attributes.authentication_mode",
                ],
                "filter_columns": ["user_attributes.risk_band"],
                "required_joins": [
                    "payments.order_ref = orders.order_ref",
                    "payments.user_ref = user_attributes.user_ref",
                    "payments.rail_ref = payment_attributes.rail_ref",
                ],
                "avoid_joins": ["payments.user_ref = orders.user_ref"],
            },
        },
        {
            "id": "library_pattern:unrelated",
            "query_count": 99,
            "compact_contract": {
                "fact_table": "shipments",
                "metrics": ["late_shipments"],
                "tables": ["shipments"],
                "dimension_columns": ["shipments.carrier"],
                "filter_columns": [],
                "required_joins": [],
                "avoid_joins": [],
            },
        },
    ]
    store.write_text(
        active_learning_artifact_key(settings, relative_path="libraries/query_patterns.jsonl"),
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in pattern_rows),
        content_type="application/jsonl",
    )
    store.write_text(
        active_learning_artifact_key(settings, relative_path="nuance/invariants.yaml"),
        (
            "version: 1\n"
            "artifact_type: candidate_invariants\n"
            "invariants:\n"
            "- id: invariant:library_join:library_pattern:payment_segment\n"
            "  invariant_type: join_pattern\n"
            "  rule: Preserve the mined join pattern unless the user intent changes grain.\n"
            "  required_joins:\n"
            "  - payments.order_ref = orders.order_ref\n"
            "  avoid_joins:\n"
            "  - payments.user_ref = orders.user_ref\n"
            "  source: query_history_library\n"
            "  confidence: medium\n"
            "- id: invariant:confounder:risk_band\n"
            "  invariant_type: confounder_resolution\n"
            "  rule: Do not choose among confounded columns by name alone.\n"
            "  columns:\n"
            "  - user_attributes.risk_band\n"
            "  - payment_attributes.risk_band\n"
            "  source: schema_profile\n"
            "  confidence: medium\n"
        ),
        content_type="application/yaml",
    )


def _two_dimension_final_result_evidence() -> dict[str, object]:
    return {
        "latest_final_sql": {
            "status": "ok",
            "purpose": "final",
            "check_name": "final_result",
            "columns": ["merchant_type", "rail_type", "tpv"],
            "rows": [
                {"merchant_type": "enterprise", "rail_type": "CC", "tpv": 15504.0},
                {"merchant_type": "enterprise", "rail_type": "UPI", "tpv": 30412.81},
                {"merchant_type": "platform", "rail_type": "NEFT", "tpv": 0.0},
            ],
            "row_count": 3,
        }
    }


if __name__ == "__main__":
    unittest.main()
