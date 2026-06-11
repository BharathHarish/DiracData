import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config.settings import DiracDataSettings
from diracdata.learning import (
    ColumnProfile,
    JoinablePairDiscovery,
    LearningCollection,
    LearningScope,
    QueryHistoryRecord,
    TableProfile,
)
from diracdata.llms import ChatModelMessage
from diracdata.storage import LocalObjectStore


class FakeJoinLLMClient:
    model = "fake-join-model"

    def __init__(self) -> None:
        self.contexts: list[dict[str, object]] = []

    def complete(self, messages: list[ChatModelMessage]) -> str:
        context = _context_from_prompt(messages[0].content)
        self.contexts.append(context)
        return json.dumps(
            {
                "join_candidates": [
                    {
                        "left_table": "orders",
                        "left_column": "customer_id",
                        "right_table": "customers",
                        "right_column": "customer_id",
                    }
                ]
            }
        )


class JoinDiscoveryTest(unittest.TestCase):
    def test_query_history_uses_only_successful_exact_deduped_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="commerce_pod",
                database="analytics",
                schema="main",
                join_history_llm_batch_size=50,
            )
            store = LocalObjectStore(tmpdir)
            collection = _write_collection(settings, store)
            fake_llm = FakeJoinLLMClient()
            discovery = JoinablePairDiscovery(
                settings=settings,
                object_store=store,
                llm_client=fake_llm,
            )
            sql = (
                "SELECT * FROM orders o "
                "JOIN customers c ON o.customer_id = c.customer_id"
            )
            records = [
                QueryHistoryRecord(
                    {
                        "statement_id": "q1",
                        "execution_status": "FINISHED",
                        "statement_text": sql,
                    }
                ),
                QueryHistoryRecord(
                    {
                        "statement_id": "q2",
                        "execution_status": "FINISHED",
                        "statement_text": sql,
                    }
                ),
                QueryHistoryRecord(
                    {
                        "statement_id": "q3",
                        "execution_status": "FAILED",
                        "statement_text": (
                            "SELECT * FROM orders o "
                            "JOIN customers c ON o.order_id = c.customer_id"
                        ),
                    }
                ),
                QueryHistoryRecord(
                    {
                        "statement_id": "q4",
                        "execution_status": "FINISHED",
                        "statement_text": (
                            "SELECT * FROM unrelated_table u "
                            "JOIN another_table a ON u.id = a.id"
                        ),
                    }
                ),
                QueryHistoryRecord(
                    {
                        "statement_id": "q5",
                        "execution_status": "FINISHED",
                        "statement_text": "SELECT count(*) FROM orders",
                    }
                ),
            ]

            result = discovery.discover(collection=collection, query_history_records=records)
            rows = _read_jsonl(store.read_text(result.joinable_pairs_artifact_key))

        self.assertEqual(result.query_history_unique_success_count, 1)
        self.assertEqual(result.query_history_llm_batch_count, 1)
        self.assertEqual(len(fake_llm.contexts), 1)
        self.assertEqual(len(fake_llm.contexts[0]["successful_queries"]), 1)
        self.assertEqual(result.pair_count, 1)
        self.assertEqual(
            set(rows[0]),
            {"left_table", "left_column", "right_table", "right_column", "join_type", "confidence"},
        )
        self.assertEqual(rows[0]["left_table"], "orders")
        self.assertEqual(rows[0]["left_column"], "customer_id")
        self.assertEqual(rows[0]["right_table"], "customers")
        self.assertEqual(rows[0]["right_column"], "customer_id")
        self.assertEqual(rows[0]["join_type"], "many_to_one")

    def test_no_history_discovers_join_from_profiles_and_samples_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="commerce_pod",
                database="analytics",
                schema="main",
                join_name_similarity_min=0.55,
                join_min_score=0.3,
            )
            store = LocalObjectStore(tmpdir)
            collection = _write_collection(settings, store)
            discovery = JoinablePairDiscovery(settings=settings, object_store=store)

            result = discovery.discover(collection=collection)
            rows = _read_jsonl(store.read_text(result.joinable_pairs_artifact_key))

        self.assertEqual(result.query_history_unique_success_count, 0)
        self.assertGreaterEqual(result.profile_sample_candidate_count, 1)
        self.assertEqual(result.pair_count, 1)
        self.assertEqual(rows[0]["left_table"], "orders")
        self.assertEqual(rows[0]["left_column"], "customer_id")
        self.assertEqual(rows[0]["right_table"], "customers")
        self.assertEqual(rows[0]["right_column"], "customer_id")
        self.assertEqual(
            set(rows[0]),
            {"left_table", "left_column", "right_table", "right_column", "join_type", "confidence"},
        )

    def test_profile_only_many_to_many_categorical_overlap_is_not_a_join(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="commerce_pod",
                database="analytics",
                schema="main",
                join_name_similarity_min=0.55,
                join_min_score=0.3,
            )
            store = LocalObjectStore(tmpdir)
            collection = _write_categorical_overlap_collection(settings, store)
            discovery = JoinablePairDiscovery(settings=settings, object_store=store)

            result = discovery.discover(collection=collection)
            rows = _read_jsonl(store.read_text(result.joinable_pairs_artifact_key))

        self.assertGreaterEqual(result.profile_sample_candidate_count, 1)
        self.assertEqual(result.pair_count, 0)
        self.assertEqual(rows, [])


def _write_collection(settings: DiracDataSettings, store: LocalObjectStore) -> LearningCollection:
    run_id = "join_test"
    orders_sample = "order_id,customer_id,revenue\n1,100,12.50\n2,101,25.00\n3,100,30.00\n"
    customers_sample = "customer_id,region\n100,west\n101,east\n102,north\n"
    orders_sample_key = (
        "artifacts/learning/commerce_pod/analytics/main/join_test/samples/orders.csv"
    )
    customers_sample_key = (
        "artifacts/learning/commerce_pod/analytics/main/join_test/samples/customers.csv"
    )
    store.write_text(orders_sample_key, orders_sample)
    store.write_text(customers_sample_key, customers_sample)
    collection = LearningCollection(
        run_id=run_id,
        scope=LearningScope("commerce_pod", "analytics", "main"),
        table_profiles=[
            TableProfile(
                table_name="orders",
                row_count=3,
                sample_artifact_key=orders_sample_key,
                columns=[
                    ColumnProfile("orders", "order_id", "INTEGER", 0, 0.0, 3),
                    ColumnProfile(
                        "orders",
                        "customer_id",
                        "INTEGER",
                        0,
                        0.0,
                        2,
                        distinct_values=[100, 101],
                    ),
                    ColumnProfile("orders", "revenue", "DECIMAL", 0, 0.0, 3),
                ],
            ),
            TableProfile(
                table_name="customers",
                row_count=3,
                sample_artifact_key=customers_sample_key,
                columns=[
                    ColumnProfile(
                        "customers",
                        "customer_id",
                        "INTEGER",
                        0,
                        0.0,
                        3,
                        distinct_values=[100, 101, 102],
                    ),
                    ColumnProfile("customers", "region", "VARCHAR", 0, 0.0, 3),
                ],
            ),
        ],
        profile_artifact_key=(
            "artifacts/learning/commerce_pod/analytics/main/join_test/profiles/"
            "table_profiles.json"
        ),
        llm_context_artifact_key=(
            "artifacts/learning/commerce_pod/analytics/main/join_test/profiles/"
            "llm_context.json"
        ),
    )
    return collection


def _write_categorical_overlap_collection(
    settings: DiracDataSettings,
    store: LocalObjectStore,
) -> LearningCollection:
    run_id = "join_categorical_test"
    payments_sample = (
        "payment_ref,risk_band\n"
        "pay_1,low\n"
        "pay_2,medium\n"
        "pay_3,high\n"
        "pay_4,low\n"
    )
    users_sample = (
        "user_ref,risk_band\n"
        "user_1,low\n"
        "user_2,medium\n"
        "user_3,high\n"
        "user_4,low\n"
    )
    payments_sample_key = (
        "artifacts/learning/commerce_pod/analytics/main/join_categorical_test/"
        "samples/payments.csv"
    )
    users_sample_key = (
        "artifacts/learning/commerce_pod/analytics/main/join_categorical_test/"
        "samples/user_attributes.csv"
    )
    store.write_text(payments_sample_key, payments_sample)
    store.write_text(users_sample_key, users_sample)
    return LearningCollection(
        run_id=run_id,
        scope=LearningScope(settings.catalog, settings.database, settings.schema),
        table_profiles=[
            TableProfile(
                table_name="payments",
                row_count=4,
                sample_artifact_key=payments_sample_key,
                columns=[
                    ColumnProfile("payments", "payment_ref", "VARCHAR", 0, 0.0, 4),
                    ColumnProfile(
                        "payments",
                        "risk_band",
                        "VARCHAR",
                        0,
                        0.0,
                        3,
                        distinct_values=["low", "medium", "high"],
                    ),
                ],
            ),
            TableProfile(
                table_name="user_attributes",
                row_count=4,
                sample_artifact_key=users_sample_key,
                columns=[
                    ColumnProfile("user_attributes", "user_ref", "VARCHAR", 0, 0.0, 4),
                    ColumnProfile(
                        "user_attributes",
                        "risk_band",
                        "VARCHAR",
                        0,
                        0.0,
                        3,
                        distinct_values=["low", "medium", "high"],
                    ),
                ],
            ),
        ],
        profile_artifact_key=(
            "artifacts/learning/commerce_pod/analytics/main/join_categorical_test/"
            "profiles/table_profiles.json"
        ),
        llm_context_artifact_key=(
            "artifacts/learning/commerce_pod/analytics/main/join_categorical_test/"
            "profiles/llm_context.json"
        ),
    )


def _context_from_prompt(prompt: str) -> dict[str, object]:
    start = prompt.rfind("```json")
    end = prompt.rfind("```")
    if start < 0 or end <= start:
        raise AssertionError("prompt does not contain JSON context block")
    return json.loads(prompt[start + len("```json"):end].strip())


def _read_jsonl(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
