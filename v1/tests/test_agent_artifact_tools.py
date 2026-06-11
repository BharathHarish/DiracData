import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.agents.artifacts import LearnedArtifactRepository
from diracdata.config.settings import DiracDataSettings
from diracdata.query_engines.duckdb_runtime import QueryResult
from diracdata.storage import LocalObjectStore
from diracdata.tools import build_data_analyst_tools


class FakeQueryEngine:
    def list_tables(self) -> list[str]:
        return ["customer", "customer_address"]

    def describe_table(self, table_name: str) -> list[object]:
        return []

    def row_count(self, table_name: str) -> int:
        return 0

    def query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        return QueryResult(columns=["count"], rows=[(2,)])

    def close(self) -> None:
        pass


class AgentArtifactToolsTest(unittest.TestCase):
    def test_tools_read_active_learning_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="commerce_pod",
                database="analytics",
                schema="main",
                agent_schema_search_limit=5,
                agent_profile_values_limit=3,
            )
            store = LocalObjectStore(tmpdir)
            _write_agent_artifacts(settings, store)
            tools = {
                tool.name: tool
                for tool in build_data_analyst_tools(
                    settings=settings,
                    object_store=store,
                    query_engine=FakeQueryEngine(),
                )
            }

            search = tools["schema_info_tool"].invoke({"query": "male california customers"})
            tables = tools["get_table_descriptions"].invoke({})
            columns = tools["get_table_columns_tool"].invoke({"table_name": "customer"})
            column = tools["get_column_description"].invoke(
                {"table_name": "customer", "column_name": "c_current_addr_sk"}
            )
            values = tools["profile_column_values_tool"].invoke(
                {"table_name": "customer_demographics", "column_name": "cd_gender"}
            )
            joins = tools["join_discovery_tool"].invoke(
                {"left_table": "customer", "right_table": "customer_address"}
            )

        self.assertEqual(search["status"], "ok")
        self.assertTrue(search["matches"])
        self.assertIn("customer", tables["table_descriptions"])
        self.assertIn("c_current_addr_sk", columns["columns"])
        self.assertEqual(column["status"], "ok")
        self.assertEqual(values["status"], "ok")
        self.assertEqual(values["distinct_values"], ["F", "M"])
        self.assertEqual(joins["pair_count"], 1)

    def test_preflight_reports_missing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(catalog="commerce_pod", database="analytics", schema="main")
            store = LocalObjectStore(tmpdir)
            repository = LearnedArtifactRepository(settings=settings, object_store=store)

            preflight = repository.preflight()

        self.assertFalse(any(preflight.values()))


def _write_agent_artifacts(settings: DiracDataSettings, store: LocalObjectStore) -> None:
    base = f"artifacts/learning/{settings.catalog}/{settings.database}/{settings.schema}"
    profile_key = f"{base}/test_run/profiles/table_profiles.json"
    join_key = f"{base}/test_run/joins/joinable_pairs.jsonl"
    store.write_json(
        f"{base}/active/descriptions/metadata_descriptions.json",
        {
            "tables": {
                "customer": {
                    "short_description": "People who buy from the business.",
                    "long_description": "Customers represent shoppers and account holders.",
                },
                "customer_address": {
                    "short_description": "Customer mailing and state information.",
                    "long_description": "Addresses describe where customers live.",
                },
                "customer_demographics": {
                    "short_description": "Customer gender and demographic details.",
                    "long_description": "Demographic attributes describe customer segments.",
                },
            },
            "columns": {
                "customer": {
                    "c_current_addr_sk": {
                        "short_description": "Current address link for the customer.",
                        "long_description": "This links customers to their current address.",
                    },
                    "c_current_cdemo_sk": {
                        "short_description": "Current demographic link for the customer.",
                        "long_description": "This links customers to demographic attributes.",
                    },
                },
                "customer_address": {
                    "ca_address_sk": {
                        "short_description": "Address identifier.",
                        "long_description": "This identifies an address row.",
                    },
                    "ca_state": {
                        "short_description": "State where the customer address is located.",
                        "long_description": "State can be used to filter customers by geography.",
                    },
                },
                "customer_demographics": {
                    "cd_demo_sk": {
                        "short_description": "Demographic identifier.",
                        "long_description": "This identifies a demographic row.",
                    },
                    "cd_gender": {
                        "short_description": "Customer gender segment.",
                        "long_description": "Gender can be used to segment customers.",
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
            "joinable_pairs_artifact_key": join_key,
        },
    )
    store.write_json(
        profile_key,
        {
            "tables": [
                {
                    "table_name": "customer_demographics",
                    "columns": [
                        {
                            "column_name": "cd_gender",
                            "data_type": "VARCHAR",
                            "null_rate": 0.0,
                            "distinct_count": 2,
                            "top_values": [
                                {"value": "M", "count": 2},
                                {"value": "F", "count": 1},
                            ],
                            "distinct_values": ["F", "M"],
                        }
                    ],
                }
            ]
        },
    )
    store.write_text(
        f"{base}/active/joins/joinable_pairs.jsonl",
        json.dumps(
            {
                "left_table": "customer",
                "left_column": "c_current_addr_sk",
                "right_table": "customer_address",
                "right_column": "ca_address_sk",
                "join_type": "many_to_one",
                "confidence": "high",
            }
        )
        + "\n",
    )


if __name__ == "__main__":
    unittest.main()
