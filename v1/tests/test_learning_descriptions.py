import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config.settings import DiracDataSettings
from diracdata.llms import ChatModelMessage
from diracdata.learning import (
    BusinessContext,
    LearningCollection,
    LearningScope,
    MetadataDescriptionGenerator,
    TableProfile,
)
from diracdata.learning.models import ColumnProfile
from diracdata.learning.paths import learning_artifact_key
from diracdata.storage import LocalObjectStore


class FakeLearningLLMClient:
    model = "fake-model"

    def __init__(self, *, omit_last_column: bool = False) -> None:
        self.omit_last_column = omit_last_column
        self.contexts: list[dict[str, object]] = []

    def complete(self, messages: list[ChatModelMessage]) -> str:
        context = _context_from_prompt(messages[0].content)
        self.contexts.append(context)
        tables = {}
        columns = {}
        for table_index, table in enumerate(context["tables"]):
            table_name = table["table_name"]
            column_names = [column["column_name"] for column in table["columns"]]
            if self.omit_last_column and table_index == len(context["tables"]) - 1:
                column_names = column_names[:-1]
            tables[table_name] = {
                "short_description": "Business activity described by the supplied evidence.",
                "long_description": "This table description is based only on the supplied business context and profile evidence. It avoids adding unsupported details.",
            }
            columns[table_name] = {
                column_name: {
                    "short_description": "Business field described by the supplied evidence.",
                    "long_description": "This column description is based only on the supplied business context and profile evidence. Its exact meaning should remain cautious when evidence is weak.",
                }
                for column_name in column_names
            }

        return json.dumps(
            {
                "tables": tables,
                "columns": columns,
            }
        )


class LearningDescriptionsTest(unittest.TestCase):
    def test_generates_metadata_descriptions_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(catalog="commerce_pod", database="analytics", schema="main")
            store = LocalObjectStore(tmpdir)
            run_id = "learn_desc"
            llm_context_key = learning_artifact_key(
                settings,
                run_id=run_id,
                relative_path="profiles/llm_context.json",
            )
            store.write_json(
                llm_context_key,
                {
                    "business_context": {
                        "text": "Commerce analytics.",
                        "table_descriptions": {"orders": "Customer purchase facts."},
                        "column_descriptions": {"orders": {"order_id": "Customer order identifier."}},
                        "glossary": {"revenue": "Money from customer purchases."},
                    },
                    "tables": [
                        {
                            "table_name": "orders",
                            "row_count": 3,
                            "columns": [
                                {
                                    "column_name": "order_id",
                                    "data_type": "INTEGER",
                                    "distinct_count": 3,
                                },
                                {
                                    "column_name": "revenue",
                                    "data_type": "DECIMAL",
                                    "distinct_count": 3,
                                }
                            ],
                        }
                    ],
                },
            )
            collection = LearningCollection(
                run_id=run_id,
                scope=LearningScope("commerce_pod", "analytics", "main"),
                table_profiles=[
                    TableProfile(
                        table_name="orders",
                        row_count=3,
                        sample_artifact_key="samples/orders.csv",
                        columns=[
                            ColumnProfile(
                                table_name="orders",
                                column_name="order_id",
                                data_type="INTEGER",
                                null_count=0,
                                null_rate=0.0,
                                distinct_count=3,
                            ),
                            ColumnProfile(
                                table_name="orders",
                                column_name="revenue",
                                data_type="DECIMAL",
                                null_count=0,
                                null_rate=0.0,
                                distinct_count=3,
                            )
                        ],
                    )
                ],
                profile_artifact_key="profiles/table_profiles.json",
                llm_context_artifact_key=llm_context_key,
            )
            generator = MetadataDescriptionGenerator(
                settings=settings,
                object_store=store,
                llm_client=FakeLearningLLMClient(),
            )

            output_key = generator.generate(collection)
            payload = json.loads(store.read_text(output_key))

        self.assertIn("orders", payload["tables"])
        self.assertIn("order_id", payload["columns"]["orders"])
        self.assertIn("revenue", payload["columns"]["orders"])
        self.assertLessEqual(
            len(payload["tables"]["orders"]["short_description"].split()),
            50,
        )

    def test_batches_descriptions_by_table_and_requires_full_column_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(catalog="commerce_pod", database="analytics", schema="main")
            store = LocalObjectStore(tmpdir)
            run_id = "learn_desc_multi"
            llm_context_key = learning_artifact_key(
                settings,
                run_id=run_id,
                relative_path="profiles/llm_context.json",
            )
            store.write_json(
                llm_context_key,
                {
                    "business_context": {"text": "Commerce analytics."},
                    "tables": [
                        {
                            "table_name": "orders",
                            "row_count": 3,
                            "columns": [
                                {"column_name": "order_id", "data_type": "INTEGER"},
                                {"column_name": "customer_id", "data_type": "INTEGER"},
                            ],
                        },
                        {
                            "table_name": "customers",
                            "row_count": 2,
                            "columns": [
                                {"column_name": "customer_id", "data_type": "INTEGER"},
                            ],
                        },
                    ],
                },
            )
            collection = LearningCollection(
                run_id=run_id,
                scope=LearningScope("commerce_pod", "analytics", "main"),
                table_profiles=[
                    TableProfile(
                        table_name="orders",
                        row_count=3,
                        sample_artifact_key="samples/orders.csv",
                        columns=[
                            ColumnProfile("orders", "order_id", "INTEGER", 0, 0.0, 3),
                            ColumnProfile("orders", "customer_id", "INTEGER", 0, 0.0, 2),
                        ],
                    ),
                    TableProfile(
                        table_name="customers",
                        row_count=2,
                        sample_artifact_key="samples/customers.csv",
                        columns=[
                            ColumnProfile("customers", "customer_id", "INTEGER", 0, 0.0, 2),
                        ],
                    ),
                ],
                profile_artifact_key="profiles/table_profiles.json",
                llm_context_artifact_key=llm_context_key,
            )
            fake_client = FakeLearningLLMClient()
            generator = MetadataDescriptionGenerator(
                settings=settings,
                object_store=store,
                llm_client=fake_client,
            )

            output_key = generator.generate(collection)
            payload = json.loads(store.read_text(output_key))

        self.assertEqual(len(fake_client.contexts), 1)
        self.assertEqual(set(payload["tables"]), {"orders", "customers"})
        self.assertEqual(set(payload["columns"]["orders"]), {"order_id", "customer_id"})
        self.assertEqual(set(payload["columns"]["customers"]), {"customer_id"})

    def test_splits_wide_tables_into_column_batches_and_merges_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(catalog="commerce_pod", database="analytics", schema="main")
            store = LocalObjectStore(tmpdir)
            run_id = "learn_desc_column_batches"
            llm_context_key = learning_artifact_key(
                settings,
                run_id=run_id,
                relative_path="profiles/llm_context.json",
            )
            store.write_json(
                llm_context_key,
                {
                    "business_context": {"text": "Commerce analytics."},
                    "tables": [
                        {
                            "table_name": "orders",
                            "row_count": 3,
                            "columns": [
                                {"column_name": "order_id", "data_type": "INTEGER"},
                                {"column_name": "customer_id", "data_type": "INTEGER"},
                            ],
                        }
                    ],
                },
            )
            collection = LearningCollection(
                run_id=run_id,
                scope=LearningScope("commerce_pod", "analytics", "main"),
                table_profiles=[],
                profile_artifact_key="profiles/table_profiles.json",
                llm_context_artifact_key=llm_context_key,
            )
            fake_client = FakeLearningLLMClient()
            generator = MetadataDescriptionGenerator(
                settings=settings,
                object_store=store,
                llm_client=fake_client,
                column_batch_size=1,
            )

            output_key = generator.generate(collection)
            payload = json.loads(store.read_text(output_key))

        self.assertEqual(len(fake_client.contexts), 2)
        self.assertEqual(
            [
                context["description_batch"]["tables"][0]["column_names"]
                for context in fake_client.contexts
            ],
            [["order_id"], ["customer_id"]],
        )
        self.assertEqual(set(payload["columns"]["orders"]), {"order_id", "customer_id"})

    def test_rejects_incomplete_llm_column_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(catalog="commerce_pod", database="analytics", schema="main")
            store = LocalObjectStore(tmpdir)
            run_id = "learn_desc_missing"
            llm_context_key = learning_artifact_key(
                settings,
                run_id=run_id,
                relative_path="profiles/llm_context.json",
            )
            store.write_json(
                llm_context_key,
                {
                    "business_context": {"text": "Commerce analytics."},
                    "tables": [
                        {
                            "table_name": "orders",
                            "row_count": 3,
                            "columns": [
                                {"column_name": "order_id", "data_type": "INTEGER"},
                                {"column_name": "customer_id", "data_type": "INTEGER"},
                            ],
                        }
                    ],
                },
            )
            collection = LearningCollection(
                run_id=run_id,
                scope=LearningScope("commerce_pod", "analytics", "main"),
                table_profiles=[],
                profile_artifact_key="profiles/table_profiles.json",
                llm_context_artifact_key=llm_context_key,
            )
            generator = MetadataDescriptionGenerator(
                settings=settings,
                object_store=store,
                llm_client=FakeLearningLLMClient(omit_last_column=True),
            )

            with self.assertRaises(ValueError):
                generator.generate(collection)


def _context_from_prompt(prompt: str) -> dict[str, object]:
    start = prompt.rfind("```json")
    end = prompt.rfind("```")
    if start < 0 or end <= start:
        raise AssertionError("prompt does not contain JSON context block")
    return json.loads(prompt[start + len("```json"):end].strip())
