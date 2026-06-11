import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.agents.artifacts import LearnedArtifactRepository
from diracdata.config.settings import DiracDataSettings
from diracdata.query_engines.duckdb_runtime import ColumnSchema, QueryResult
from diracdata.storage import LocalObjectStore
from diracdata.tools.join_tools import build_join_tools


class FakeJoinRecoveryQueryEngine:
    def __init__(self, matches: set[tuple[str, str, str, str]]) -> None:
        self.matches = matches
        self.sql: list[str] = []

    def list_tables(self) -> list[str]:
        return ["clients", "client_profiles", "online_purchases"]

    def describe_table(self, table_name: str) -> list[ColumnSchema]:
        return []

    def row_count(self, table_name: str) -> int:
        return 0

    def query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        self.sql.append(sql)
        normalized = sql.replace('"', "")
        for left_table, left_column, right_table, right_column in self.matches:
            left = f"FROM {left_table} AS left_table"
            right = f"JOIN {right_table} AS right_table"
            condition = f"left_table.{left_column} = right_table.{right_column}"
            if left in normalized and right in normalized and condition in normalized:
                return QueryResult(columns=["join_match"], rows=[(1,)])
        return QueryResult(columns=["join_match"], rows=[])

    def close(self) -> None:
        pass


class AgentJoinRecoveryTest(unittest.TestCase):
    def test_join_tool_returns_query_library_path_contract_for_table_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
            )
            store = LocalObjectStore(tmpdir)
            _write_join_contract_artifacts(settings, store, with_library=True)
            repository = LearnedArtifactRepository(settings=settings, object_store=store)
            engine = FakeJoinRecoveryQueryEngine(set())
            tool = build_join_tools(
                settings=settings,
                repository=repository,
                query_engine=engine,
            )[0]

            result = tool.invoke(
                {
                    "fact_table": "payments",
                    "tables": [
                        "payments",
                        "orders",
                        "user_attributes",
                        "users",
                        "payment_attributes",
                    ],
                }
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"], "query_history_library")
        self.assertEqual(result["fact_table"], "payments")
        self.assertIn(
            "payments.order_ref = orders.order_ref",
            [item["join_clause"] for item in result["join_path"]],
        )
        self.assertIn(
            "payments.user_ref = orders.user_ref",
            [item["join_clause"] for item in result["risky_alternatives"]],
        )

    def test_join_tool_falls_back_to_graph_path_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
            )
            store = LocalObjectStore(tmpdir)
            _write_join_contract_artifacts(settings, store, with_library=False)
            repository = LearnedArtifactRepository(settings=settings, object_store=store)
            engine = FakeJoinRecoveryQueryEngine(set())
            tool = build_join_tools(
                settings=settings,
                repository=repository,
                query_engine=engine,
            )[0]

            result = tool.invoke(
                {
                    "fact_table": "payments",
                    "tables": ["payments", "orders", "payment_attributes"],
                }
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"], "learned_graph")
        self.assertEqual(result["join_count"], 2)
        self.assertEqual(
            {
                item["join_clause"]
                for item in result["join_path"]
            },
            {
                "payments.order_ref = orders.order_ref",
                "payments.rail_ref = payment_attributes.rail_ref",
            },
        )
        self.assertIn(
            "payments.user_ref = orders.user_ref",
            [item["join_clause"] for item in result["risky_alternatives"]],
        )

    def test_join_tool_recovers_and_persists_structural_join(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
                agent_join_recovery_enabled=True,
            )
            store = LocalObjectStore(tmpdir)
            _write_join_recovery_artifacts(settings, store)
            repository = LearnedArtifactRepository(settings=settings, object_store=store)
            engine = FakeJoinRecoveryQueryEngine(
                {
                    (
                        "clients",
                        "current_client_profile_ref",
                        "client_profiles",
                        "client_profile_record",
                    )
                }
            )
            tool = build_join_tools(
                settings=settings,
                repository=repository,
                query_engine=engine,
            )[0]

            recovered = tool.invoke(
                {"left_table": "clients", "right_table": "client_profiles"}
            )
            learned = tool.invoke(
                {"left_table": "clients", "right_table": "client_profiles"}
            )
            rows = repository.load_joinable_pairs()

        self.assertEqual(recovered["status"], "ok")
        self.assertEqual(recovered["source"], "runtime_recovery")
        self.assertTrue(recovered["persisted"])
        self.assertEqual(recovered["pair_count"], 1)
        self.assertEqual(learned["source"], "learned")
        self.assertEqual(rows, recovered["joinable_pairs"])
        self.assertEqual(
            set(rows[0]),
            {"left_table", "left_column", "right_table", "right_column", "join_type", "confidence"},
        )
        self.assertEqual(rows[0]["left_column"], "current_client_profile_ref")
        self.assertEqual(rows[0]["right_column"], "client_profile_record")
        self.assertEqual(rows[0]["join_type"], "many_to_one")
        self.assertEqual(rows[0]["confidence"], "high")

    def test_join_tool_validates_explicit_candidate_and_filters_failed_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
                agent_join_recovery_enabled=True,
            )
            store = LocalObjectStore(tmpdir)
            _write_join_recovery_artifacts(settings, store)
            repository = LearnedArtifactRepository(settings=settings, object_store=store)
            engine = FakeJoinRecoveryQueryEngine(
                {
                    (
                        "online_purchases",
                        "billing_client_profile_ref",
                        "clients",
                        "client_record",
                    ),
                    (
                        "online_purchases",
                        "billing_client_ref",
                        "clients",
                        "client_record",
                    )
                }
            )
            tool = build_join_tools(
                settings=settings,
                repository=repository,
                query_engine=engine,
            )[0]

            missing = tool.invoke(
                {
                    "left_table": "online_purchases",
                    "left_column": "shipping_client_ref",
                    "right_table": "clients",
                    "right_column": "client_record",
                }
            )
            semantic_mismatch = tool.invoke(
                {
                    "left_table": "online_purchases",
                    "left_column": "billing_client_profile_ref",
                    "right_table": "clients",
                    "right_column": "client_record",
                }
            )
            recovered = tool.invoke(
                {
                    "left_table": "online_purchases",
                    "left_column": "billing_client_ref",
                    "right_table": "clients",
                    "right_column": "client_record",
                }
            )

        self.assertEqual(missing["status"], "not_found")
        self.assertIn("schema", missing)
        self.assertEqual(semantic_mismatch["status"], "not_found")
        self.assertIn("schema", semantic_mismatch)
        self.assertEqual(recovered["status"], "ok")
        self.assertEqual(recovered["source"], "runtime_recovery")
        self.assertEqual(recovered["joinable_pairs"][0]["left_column"], "billing_client_ref")
        self.assertEqual(recovered["joinable_pairs"][0]["right_column"], "client_record")


def _write_join_recovery_artifacts(
    settings: DiracDataSettings,
    store: LocalObjectStore,
) -> None:
    base = f"artifacts/learning/{settings.catalog}/{settings.database}/{settings.schema}"
    profile_key = f"{base}/test_run/profiles/table_profiles.json"
    join_key = f"{base}/test_run/joins/joinable_pairs.jsonl"
    store.write_json(
        f"{base}/active/contexts/learned_context.json",
        {
            "run_id": "test_run",
            "profile_artifact_key": profile_key,
            "joinable_pairs_artifact_key": join_key,
        },
    )
    store.write_text(f"{base}/active/joins/joinable_pairs.jsonl", "")
    store.write_json(
        profile_key,
        {
            "tables": [
                {
                    "table_name": "clients",
                    "row_count": 100,
                    "columns": [
                        _column("clients", "client_record", "INTEGER", 100),
                        _column("clients", "current_client_profile_ref", "INTEGER", 20),
                    ],
                },
                {
                    "table_name": "client_profiles",
                    "row_count": 20,
                    "columns": [
                        _column("client_profiles", "client_profile_record", "INTEGER", 20),
                    ],
                },
                {
                    "table_name": "online_purchases",
                    "row_count": 1000,
                    "columns": [
                        _column("online_purchases", "billing_client_ref", "INTEGER", 90),
                        _column("online_purchases", "billing_client_profile_ref", "INTEGER", 20),
                        _column("online_purchases", "shipping_client_ref", "INTEGER", 90),
                    ],
                },
            ]
        },
    )


def _write_join_contract_artifacts(
    settings: DiracDataSettings,
    store: LocalObjectStore,
    *,
    with_library: bool,
) -> None:
    base = f"artifacts/learning/{settings.catalog}/{settings.database}/{settings.schema}"
    store.write_json(
        f"{base}/active/contexts/learned_context.json",
        {
            "run_id": "join_contract_test",
            "joinable_pairs_artifact_key": f"{base}/join_contract_test/joins/joinable_pairs.jsonl",
            "query_libraries_manifest_artifact_key": (
                f"{base}/join_contract_test/libraries/manifest.json"
            ),
        },
    )
    join_rows = [
        {
            "left_table": "payments",
            "left_column": "order_ref",
            "right_table": "orders",
            "right_column": "order_ref",
            "join_type": "many_to_one",
            "confidence": "high",
        },
        {
            "left_table": "payments",
            "left_column": "user_ref",
            "right_table": "orders",
            "right_column": "user_ref",
            "join_type": "many_to_many",
            "confidence": "low",
        },
        {
            "left_table": "payments",
            "left_column": "user_ref",
            "right_table": "user_attributes",
            "right_column": "user_ref",
            "join_type": "many_to_one",
            "confidence": "high",
        },
        {
            "left_table": "payments",
            "left_column": "user_ref",
            "right_table": "users",
            "right_column": "user_ref",
            "join_type": "many_to_one",
            "confidence": "high",
        },
        {
            "left_table": "payments",
            "left_column": "rail_ref",
            "right_table": "payment_attributes",
            "right_column": "rail_ref",
            "join_type": "many_to_one",
            "confidence": "high",
        },
    ]
    join_payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in join_rows)
    store.write_text(f"{base}/join_contract_test/joins/joinable_pairs.jsonl", join_payload)
    store.write_text(f"{base}/active/joins/joinable_pairs.jsonl", join_payload)
    if with_library:
        pattern = {
            "id": "library_pattern:test",
            "query_count": 12,
            "fact_table": "payments",
            "tables": ["payments", "orders", "user_attributes", "users", "payment_attributes"],
            "compact_contract": {
                "fact_table": "payments",
                "metrics": ["tpv", "psr"],
                "tables": ["payments", "orders", "user_attributes", "users", "payment_attributes"],
                "required_joins": [
                    "payments.order_ref = orders.order_ref",
                    "payments.user_ref = user_attributes.user_ref",
                    "payments.user_ref = users.user_ref",
                    "payments.rail_ref = payment_attributes.rail_ref",
                ],
                "avoid_joins": ["payments.user_ref = orders.user_ref"],
            },
        }
        store.write_text(
            f"{base}/active/libraries/query_patterns.jsonl",
            json.dumps(pattern, sort_keys=True) + "\n",
        )


def _column(
    table_name: str,
    column_name: str,
    data_type: str,
    distinct_count: int,
) -> dict[str, object]:
    return {
        "table_name": table_name,
        "column_name": column_name,
        "data_type": data_type,
        "null_rate": 0.0,
        "distinct_count": distinct_count,
    }


if __name__ == "__main__":
    unittest.main()
