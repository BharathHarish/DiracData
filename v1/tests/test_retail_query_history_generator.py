import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from generate_retail_analytics_query_history import (  # noqa: E402
    QUERY_HISTORY_COLUMNS,
    TPCDS_TECHNICAL_NAMES,
    generate_records,
    write_records,
)
from run_join_discovery_uat import _default_expected_pairs  # noqa: E402
from diracdata.config.settings import DiracDataSettings  # noqa: E402
from diracdata.learning import (  # noqa: E402
    ColumnProfile,
    ContextGraphBuilder,
    JoinablePairDiscovery,
    LearningCollection,
    LearningScope,
    TableProfile,
    load_query_history_csv,
)
from diracdata.learning.paths import learning_artifact_key  # noqa: E402
from diracdata.llms import ChatModelMessage  # noqa: E402
from diracdata.storage import LocalObjectStore  # noqa: E402


class FakeRetailJoinLLMClient:
    model = "fake-retail-join-model"

    def __init__(self) -> None:
        self.messages: list[list[ChatModelMessage]] = []

    def complete(self, messages: list[ChatModelMessage]) -> str:
        self.messages.append(messages)
        return """
        {
          "join_candidates": [
            {
              "left_table": "online_purchases",
              "left_column": "billing_client_ref",
              "right_table": "clients",
              "right_column": "client_record"
            },
            {
              "left_table": "online_purchases",
              "left_column": "sale_calendar_day_ref",
              "right_table": "calendar_days",
              "right_column": "calendar_day_record"
            },
            {
              "left_table": "online_purchases",
              "left_column": "merchandise_ref",
              "right_table": "merchandise",
              "right_column": "merchandise_record"
            }
          ]
        }
        """


class RetailQueryHistoryGeneratorTest(unittest.TestCase):
    def test_generates_databricks_style_retail_records(self) -> None:
        records = generate_records(count=150, seed=7)

        self.assertEqual(len(records), 150)
        self.assertGreaterEqual(
            sum(record["execution_status"] == "FINISHED" for record in records),
            100,
        )
        self.assertTrue(any(record["execution_status"] == "FAILED" for record in records))
        self.assertTrue(any(record["execution_status"] == "CANCELED" for record in records))
        self.assertEqual(set(records[0]), set(QUERY_HISTORY_COLUMNS))

        joined_sql = "\n".join(str(record["statement_text"]) for record in records)
        self.assertIn("JOIN clients", joined_sql)
        self.assertIn("JOIN calendar_days", joined_sql)
        self.assertIn("JOIN merchandise", joined_sql)
        self.assertIn("JOIN marketing_campaigns", joined_sql)
        self.assertIn("JOIN fulfillment_centers", joined_sql)
        self.assertIn("online_purchases", joined_sql)
        for forbidden in TPCDS_TECHNICAL_NAMES:
            self.assertNotIn(forbidden, joined_sql)

    def test_written_csv_round_trips_through_query_history_loader(self) -> None:
        records = generate_records(count=30, seed=11)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "retail_history.csv"
            write_records(records, path)
            loaded = load_query_history_csv(path)

        self.assertEqual(len(loaded), 30)
        self.assertEqual(loaded[0].statement_id, records[0]["statement_id"])
        self.assertEqual(loaded[0].statement_type, "SELECT")
        self.assertIsInstance(loaded[0].values["compute"], dict)
        self.assertEqual(loaded[0].values["query_tags"]["pod"], "retail_analytics")

    def test_generated_history_feeds_retail_join_discovery(self) -> None:
        records = generate_records(count=30, seed=13)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "retail_history.csv"
            write_records(records, path)
            history_records = load_query_history_csv(path)
            settings = DiracDataSettings(
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
                join_history_llm_batch_size=200,
            )
            store = LocalObjectStore(tmpdir)
            collection = _write_retail_collection(store)
            fake_llm = FakeRetailJoinLLMClient()
            discovery = JoinablePairDiscovery(
                settings=settings,
                object_store=store,
                llm_client=fake_llm,
            )

            result = discovery.discover(
                collection=collection,
                query_history_records=history_records,
            )
            pairs = _read_jsonl(store.read_text(result.joinable_pairs_artifact_key))

        self.assertGreaterEqual(result.query_history_unique_success_count, 1)
        self.assertEqual(result.query_history_llm_batch_count, 1)
        self.assertEqual(len(fake_llm.messages), 1)
        self.assertEqual(
            {
                _pair_key(pair)
                for pair in pairs
            },
            {
                "online_purchases.billing_client_ref=clients.client_record",
                "online_purchases.merchandise_ref=merchandise.merchandise_record",
                "online_purchases.sale_calendar_day_ref=calendar_days.calendar_day_record",
            },
        )

    def test_generated_history_builds_context_graph_query_patterns(self) -> None:
        records = generate_records(count=60, seed=23)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "retail_history.csv"
            write_records(records, path)
            history_records = load_query_history_csv(path)
            settings = DiracDataSettings(
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
                join_history_llm_batch_size=200,
            )
            store = LocalObjectStore(tmpdir)
            collection = _write_retail_collection(store)
            description_key = _write_retail_descriptions(settings, store, collection.run_id)
            discovery = JoinablePairDiscovery(
                settings=settings,
                object_store=store,
                llm_client=FakeRetailJoinLLMClient(),
            )
            join_result = discovery.discover(
                collection=collection,
                query_history_records=history_records,
            )
            builder = ContextGraphBuilder(settings=settings, object_store=store)

            graph_result = builder.build(
                collection=collection,
                description_artifact_key=description_key,
                joinable_pairs_artifact_key=join_result.joinable_pairs_artifact_key,
                business_grounding={},
                query_history_records=history_records,
            )
            query_patterns = _read_jsonl(store.read_text(graph_result.query_patterns_artifact_key))
            edges = _read_jsonl(store.read_text(graph_result.edges_artifact_key))
            retrieval_docs = _read_jsonl(store.read_text(graph_result.retrieval_documents_artifact_key))

        edge_types = {str(edge["edge_type"]) for edge in edges}
        query_pattern_tables = [set(pattern["tables"]) for pattern in query_patterns]
        retrieval_types = {str(document["retrieval_type"]) for document in retrieval_docs}

        self.assertGreater(graph_result.query_pattern_count, 0)
        self.assertTrue(
            any({"online_purchases", "clients", "calendar_days"} <= tables for tables in query_pattern_tables)
        )
        self.assertTrue(any(pattern["join_ids"] for pattern in query_patterns))
        self.assertIn("query_pattern_used_table", edge_types)
        self.assertIn("query_pattern_used_join", edge_types)
        self.assertIn("query_pattern", retrieval_types)

    def test_join_discovery_uat_defaults_include_retail_pairs(self) -> None:
        pairs = _default_expected_pairs("retail_analytics")

        self.assertIn(
            "online_purchases.billing_client_ref=clients.client_record",
            pairs,
        )
        self.assertIn(
            "stock_levels.fulfillment_center_ref=fulfillment_centers.fulfillment_center_record",
            pairs,
        )
        self.assertNotIn("store_sales.ss_item_sk=item.i_item_sk", pairs)


def _write_retail_collection(store: LocalObjectStore) -> LearningCollection:
    run_id = "retail_history_join_test"
    base = f"artifacts/learning/retail_pod/analytics/retail_analytics/{run_id}/samples"
    samples = {
        "online_purchases": (
            "billing_client_ref,sale_calendar_day_ref,merchandise_ref,net_paid\n"
            "100,1,10,12.50\n"
            "101,2,11,25.00\n"
            "100,2,10,30.00\n"
        ),
        "clients": (
            "client_record,current_address_ref,current_client_profile_ref\n"
            "100,200,300\n"
            "101,201,301\n"
            "102,202,302\n"
        ),
        "calendar_days": "calendar_day_record,year\n1,2001\n2,2002\n3,2003\n",
        "merchandise": "merchandise_record,category\n10,Jewelry\n11,Electronics\n12,Home\n",
    }
    sample_keys = {}
    for table_name, text in samples.items():
        key = f"{base}/{table_name}.csv"
        store.write_text(key, text)
        sample_keys[table_name] = key

    return LearningCollection(
        run_id=run_id,
        scope=LearningScope("retail_pod", "analytics", "retail_analytics"),
        table_profiles=[
            TableProfile(
                table_name="online_purchases",
                row_count=3,
                sample_artifact_key=sample_keys["online_purchases"],
                columns=[
                    ColumnProfile(
                        "online_purchases",
                        "billing_client_ref",
                        "INTEGER",
                        0,
                        0.0,
                        2,
                        distinct_values=[100, 101],
                    ),
                    ColumnProfile(
                        "online_purchases",
                        "sale_calendar_day_ref",
                        "INTEGER",
                        0,
                        0.0,
                        2,
                        distinct_values=[1, 2],
                    ),
                    ColumnProfile(
                        "online_purchases",
                        "merchandise_ref",
                        "INTEGER",
                        0,
                        0.0,
                        2,
                        distinct_values=[10, 11],
                    ),
                    ColumnProfile("online_purchases", "net_paid", "DECIMAL", 0, 0.0, 3),
                ],
            ),
            TableProfile(
                table_name="clients",
                row_count=3,
                sample_artifact_key=sample_keys["clients"],
                columns=[
                    ColumnProfile(
                        "clients",
                        "client_record",
                        "INTEGER",
                        0,
                        0.0,
                        3,
                        distinct_values=[100, 101, 102],
                    ),
                    ColumnProfile("clients", "current_address_ref", "INTEGER", 0, 0.0, 3),
                    ColumnProfile(
                        "clients",
                        "current_client_profile_ref",
                        "INTEGER",
                        0,
                        0.0,
                        3,
                    ),
                ],
            ),
            TableProfile(
                table_name="calendar_days",
                row_count=3,
                sample_artifact_key=sample_keys["calendar_days"],
                columns=[
                    ColumnProfile(
                        "calendar_days",
                        "calendar_day_record",
                        "INTEGER",
                        0,
                        0.0,
                        3,
                        distinct_values=[1, 2, 3],
                    ),
                    ColumnProfile("calendar_days", "year", "INTEGER", 0, 0.0, 3),
                ],
            ),
            TableProfile(
                table_name="merchandise",
                row_count=3,
                sample_artifact_key=sample_keys["merchandise"],
                columns=[
                    ColumnProfile(
                        "merchandise",
                        "merchandise_record",
                        "INTEGER",
                        0,
                        0.0,
                        3,
                        distinct_values=[10, 11, 12],
                    ),
                    ColumnProfile("merchandise", "category", "VARCHAR", 0, 0.0, 3),
                ],
            ),
        ],
        profile_artifact_key=(
            "artifacts/learning/retail_pod/analytics/retail_analytics/"
            f"{run_id}/profiles/table_profiles.json"
        ),
        llm_context_artifact_key=(
            "artifacts/learning/retail_pod/analytics/retail_analytics/"
            f"{run_id}/profiles/llm_context.json"
        ),
    )


def _write_retail_descriptions(
    settings: DiracDataSettings,
    store: LocalObjectStore,
    run_id: str,
) -> str:
    payload = {
        "tables": {
            "online_purchases": {
                "short_description": "Online channel purchase activity.",
                "long_description": "Online purchases capture buyer identity, sale timing, merchandise, and paid amount for ecommerce analysis.",
            },
            "clients": {
                "short_description": "Shopper account identities.",
                "long_description": "Clients represent shopper accounts used to count customers and connect to current profile and address details.",
            },
            "calendar_days": {
                "short_description": "Business calendar dates.",
                "long_description": "Calendar days provide year and month fields for sale and activity period filtering.",
            },
            "merchandise": {
                "short_description": "Retail product catalog.",
                "long_description": "Merchandise describes products and categories used for product-family analysis.",
            },
        },
        "columns": {
            "online_purchases": {
                "billing_client_ref": {
                    "short_description": "Buyer identity.",
                    "long_description": "Billing client is the shopper identity used for online buyer and active-user counts.",
                },
                "sale_calendar_day_ref": {
                    "short_description": "Sale date link.",
                    "long_description": "Sale calendar day links online purchases to the business calendar period.",
                },
                "merchandise_ref": {
                    "short_description": "Purchased product link.",
                    "long_description": "Merchandise reference connects online purchases to product categories.",
                },
                "net_paid": {
                    "short_description": "Revenue amount.",
                    "long_description": "Net paid is the amount collected for the purchase after item-level adjustments.",
                },
            },
            "clients": {
                "client_record": {
                    "short_description": "Shopper identity.",
                    "long_description": "Client record uniquely identifies a shopper account.",
                },
                "current_address_ref": {
                    "short_description": "Current address link.",
                    "long_description": "Current address links a shopper to their current location.",
                },
                "current_client_profile_ref": {
                    "short_description": "Current profile link.",
                    "long_description": "Current client profile links a shopper to current demographic attributes.",
                },
            },
            "calendar_days": {
                "calendar_day_record": {
                    "short_description": "Calendar day identity.",
                    "long_description": "Calendar day record identifies a calendar date row.",
                },
                "year": {
                    "short_description": "Calendar year.",
                    "long_description": "Year supports annual activity and revenue filtering.",
                },
            },
            "merchandise": {
                "merchandise_record": {
                    "short_description": "Product identity.",
                    "long_description": "Merchandise record identifies a product.",
                },
                "category": {
                    "short_description": "Product category.",
                    "long_description": "Category groups products into retail families such as Jewelry or Electronics.",
                },
            },
        },
    }
    key = learning_artifact_key(
        settings,
        run_id=run_id,
        relative_path="descriptions/metadata_descriptions.json",
    )
    store.write_json(key, payload)
    return key


def _read_jsonl(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _pair_key(pair: dict[str, object]) -> str:
    return (
        f"{pair['left_table']}.{pair['left_column']}="
        f"{pair['right_table']}.{pair['right_column']}"
    )


if __name__ == "__main__":
    unittest.main()
