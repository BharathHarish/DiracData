from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.agents.artifacts import LearnedArtifactRepository
from diracdata.config import DiracDataSettings
from diracdata.grounding import (
    BusinessGroundingError,
    BusinessGroundingRepository,
    publish_business_grounding,
)
from diracdata.query_engines import ColumnSchema, QueryResult
from diracdata.storage import LocalObjectStore
from diracdata.tools import build_data_analyst_tools


class GroundingFakeQueryEngine:
    def list_tables(self) -> list[str]:
        return ["clients", "addresses", "online_purchases"]

    def describe_table(self, table_name: str) -> list[ColumnSchema]:
        return {
            "clients": [
                ColumnSchema("client_record", "BIGINT"),
                ColumnSchema("current_address_ref", "BIGINT"),
            ],
            "addresses": [
                ColumnSchema("address_record", "BIGINT"),
                ColumnSchema("state", "VARCHAR"),
            ],
            "online_purchases": [
                ColumnSchema("billing_client_ref", "BIGINT"),
                ColumnSchema("order_number", "BIGINT"),
            ],
        }[table_name]

    def row_count(self, table_name: str) -> int:
        return 0

    def query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        return QueryResult(columns=["customer_count"], rows=[(18,)])

    def close(self) -> None:
        pass


class BusinessGroundingTest(unittest.TestCase):
    def test_publish_load_and_search_business_grounding(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
            )
            store = LocalObjectStore(tmpdir)
            _write_learned_profile(settings, store)
            source = Path(tmpdir) / "grounding.yaml"
            source.write_text(_valid_grounding_yaml(), encoding="utf-8")
            repository = LearnedArtifactRepository(settings=settings, object_store=store)

            validation = publish_business_grounding(
                settings=settings,
                object_store=store,
                source_path=source,
                learned_repository=repository,
                query_engine=GroundingFakeQueryEngine(),
            )
            grounding = BusinessGroundingRepository(settings=settings, object_store=store)

            self.assertTrue(store.exists(validation.yaml_key))
            self.assertTrue(store.exists(validation.json_key))
            self.assertTrue(grounding.exists())
            self.assertEqual(grounding.get_default_policy("from state")["id"], "state_default")
            self.assertEqual(grounding.get_metric("distinct_customers")["id"], "distinct_customers")
            self.assertEqual(grounding.get_sql_template("online_customer_count")["id"], "online_customer_count")
            self.assertTrue(grounding.search("online customers from Arizona", limit=5))

    def test_publish_rejects_unknown_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
            )
            store = LocalObjectStore(tmpdir)
            _write_learned_profile(settings, store)
            source = Path(tmpdir) / "grounding.yaml"
            source.write_text(
                _valid_grounding_yaml().replace("addresses.state", "addresses.missing_state"),
                encoding="utf-8",
            )
            repository = LearnedArtifactRepository(settings=settings, object_store=store)

            with self.assertRaises(BusinessGroundingError):
                publish_business_grounding(
                    settings=settings,
                    object_store=store,
                    source_path=source,
                    learned_repository=repository,
                    query_engine=GroundingFakeQueryEngine(),
                )

    def test_grounding_tools_return_not_found_when_artifact_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
            )
            store = LocalObjectStore(tmpdir)
            _write_learned_profile(settings, store)
            tools = {
                tool.name: tool
                for tool in build_data_analyst_tools(
                    settings=settings,
                    object_store=store,
                    query_engine=GroundingFakeQueryEngine(),
                )
            }

            result = tools["business_term_search_tool"].invoke({"query": "active customer"})

        self.assertEqual(result["status"], "not_found")

    def test_typed_resolution_activates_exact_metrics_without_noisy_templates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
            )
            store = LocalObjectStore(tmpdir)
            base = f"artifacts/learning/{settings.catalog}/{settings.database}/{settings.schema}"
            store.write_json(
                f"{base}/active/grounding/business_grounding.json",
                {
                    "version": 1,
                    "scope": {
                        "catalog": settings.catalog,
                        "database": settings.database,
                        "schema": settings.schema,
                    },
                    "glossary": [],
                    "definitions": [],
                    "defaults": [],
                    "metrics": [
                        {
                            "id": "tpv",
                            "name": "TPV",
                            "synonyms": ["total payment volume"],
                        },
                        {
                            "id": "psr",
                            "name": "PSR",
                            "synonyms": ["payment success rate", "success rate"],
                        },
                    ],
                    "sql_templates": [
                        {
                            "id": "mau_calendar_month",
                            "name": "MAU calendar month",
                            "sql": "SELECT date_trunc('month', payment_time) AS month FROM payments",
                        },
                        {
                            "id": "retained_users_past_3_months",
                            "name": "Retained users past 3 months",
                            "sql": "SELECT date_trunc('month', payment_time) AS month FROM payments",
                        },
                    ],
                    "ground_truth_sql": [],
                },
            )
            grounding = BusinessGroundingRepository(settings=settings, object_store=store)

            resolution = grounding.resolve_business_intent(
                "Compare TPV and payment success rate for active merchant accounts with at least 5 attempts.",
                limit=10,
            )

        activated = resolution["activated"]
        self.assertEqual(
            [item["id"] for item in activated["metrics"]],
            ["tpv", "psr"],
        )
        self.assertEqual(activated["sql_templates"], [])


def _write_learned_profile(settings: DiracDataSettings, store: LocalObjectStore) -> None:
    base = f"artifacts/learning/{settings.catalog}/{settings.database}/{settings.schema}"
    profile_key = f"{base}/test_run/profiles/table_profiles.json"
    store.write_json(
        f"{base}/active/contexts/learned_context.json",
        {
            "run_id": "test_run",
            "profile_artifact_key": profile_key,
            "joinable_pairs_artifact_key": f"{base}/active/joins/joinable_pairs.jsonl",
        },
    )
    store.write_json(
        f"{base}/active/descriptions/metadata_descriptions.json",
        {"tables": {}, "columns": {}},
    )
    store.write_text(f"{base}/active/joins/joinable_pairs.jsonl", "")
    store.write_json(
        profile_key,
        {
            "tables": [
                {
                    "table_name": "clients",
                    "columns": [
                        {"column_name": "client_record", "data_type": "BIGINT"},
                        {"column_name": "current_address_ref", "data_type": "BIGINT"},
                    ],
                },
                {
                    "table_name": "addresses",
                    "columns": [
                        {"column_name": "address_record", "data_type": "BIGINT"},
                        {"column_name": "state", "data_type": "VARCHAR"},
                    ],
                },
                {
                    "table_name": "online_purchases",
                    "columns": [
                        {"column_name": "billing_client_ref", "data_type": "BIGINT"},
                        {"column_name": "order_number", "data_type": "BIGINT"},
                    ],
                },
            ]
        },
    )


def _valid_grounding_yaml() -> str:
    return """
version: 1
scope:
  catalog: retail_pod
  database: analytics
  schema: retail_analytics
glossary:
  - id: online_customer
    term: Online customer
    synonyms:
      - shopped online
    definition: A client with an online purchase.
    tables:
      - online_purchases
      - clients
    columns:
      - online_purchases.billing_client_ref
      - clients.client_record
definitions: []
defaults:
  - id: state_default
    applies_to:
      - from state
    policy: Use current address state.
    field: addresses.state
metrics:
  - id: distinct_customers
    name: Distinct customers
    description: Count unique clients.
    calculation: COUNT(DISTINCT clients.client_record)
    columns:
      - clients.client_record
sql_templates:
  - id: online_customer_count
    name: Online customer count
    description: Count distinct online billing clients.
    required_tables:
      - online_purchases
      - clients
    join_path:
      - - online_purchases.billing_client_ref
        - clients.client_record
    sql: |
      SELECT COUNT(DISTINCT c.client_record) AS customer_count
      FROM online_purchases op
      JOIN clients c ON op.billing_client_ref = c.client_record
ground_truth_sql:
  - id: online_customer_count_gold
    question: How many customers shopped online?
    expected_answer:
      type: number
      value: 18
    tables:
      - online_purchases
      - clients
    sql: |
      SELECT COUNT(DISTINCT c.client_record) AS customer_count
      FROM online_purchases op
      JOIN clients c ON op.billing_client_ref = c.client_record
"""


if __name__ == "__main__":
    unittest.main()
