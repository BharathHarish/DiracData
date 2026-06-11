import json
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config.settings import DiracDataSettings
from diracdata.learning import (
    ColumnProfile,
    LearningCollection,
    LearningScope,
    QueryLibraryBuilder,
    QueryHistoryRecord,
    TableProfile,
)
from diracdata.learning.paths import active_learning_artifact_key, learning_artifact_key
from diracdata.storage import LocalObjectStore
from generate_fintech_query_history import generate_records  # noqa: E402


class QueryHistoryLibraryBuilderTest(unittest.TestCase):
    def test_mines_compact_fintech_pattern_library_from_query_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
            )
            store = LocalObjectStore(tmpdir)
            collection = _collection(settings)
            join_key = _write_joinable_pairs(settings, store, collection.run_id)
            builder = QueryLibraryBuilder(settings=settings, object_store=store)

            result = builder.build(
                collection=collection,
                query_history_records=[
                    QueryHistoryRecord(row)
                    for row in generate_records(count=160, unique_success_sql=35, seed=41)
                ],
                joinable_pairs_artifact_key=join_key,
                business_grounding=_business_grounding(),
            )

            patterns = _read_jsonl(store.read_text(result.query_patterns_artifact_key))
            entity_bindings = _read_jsonl(store.read_text(result.entity_binding_patterns_artifact_key))
            metric_usage = _read_jsonl(store.read_text(result.metric_usage_patterns_artifact_key))
            templates = yaml.safe_load(store.read_text(result.sql_template_library_artifact_key))
            active_manifest = store.read_json(
                active_learning_artifact_key(settings, relative_path="libraries/manifest.json")
            )

        self.assertGreater(result.query_pattern_count, 0)
        self.assertEqual(active_manifest["artifact_type"], "query_history_libraries")
        self.assertEqual(templates["artifact_type"], "sql_template_library")
        self.assertGreater(len(templates["patterns"]), 0)
        self.assertTrue(any(row["metric_id"] == "tpv" for row in metric_usage))
        self.assertTrue(any(row["metric_id"] == "psr" for row in metric_usage))
        self.assertTrue(
            any(row["column_ref"] == "user_attributes.risk_band" for row in entity_bindings)
        )

        target = _target_confounding_pattern(patterns)
        self.assertIsNotNone(target)
        assert target is not None
        compact = target["compact_contract"]
        self.assertIn("tpv", compact["metrics"])
        self.assertIn("psr", compact["metrics"])
        self.assertIn("user_attributes.risk_band", compact["filter_columns"])
        self.assertIn("user_attributes.state", compact["filter_columns"])
        self.assertIn("users.account_state", compact["filter_columns"])
        self.assertIn("orders.checkout_surface", compact["dimension_columns"])
        self.assertIn("payment_attributes.authentication_mode", compact["dimension_columns"])
        self.assertIn("orders.order_ref = payments.order_ref", compact["required_joins"])
        self.assertIn("orders.user_ref = payments.user_ref", compact["avoid_joins"])
        self.assertLess(len(json.dumps(compact, sort_keys=True)), 1200)


def _target_confounding_pattern(patterns: list[dict[str, object]]) -> dict[str, object] | None:
    required_tables = {"orders", "payments", "payment_attributes", "user_attributes", "users"}
    for pattern in patterns:
        if not required_tables <= set(pattern.get("tables") or []):
            continue
        metrics = set(pattern.get("metrics") or [])
        filters = set(pattern.get("filter_columns") or [])
        dimensions = set(pattern.get("dimension_columns") or [])
        if {"tpv", "psr"} <= metrics and {
            "user_attributes.risk_band",
            "user_attributes.state",
            "users.account_state",
        } <= filters and {
            "orders.checkout_surface",
            "payment_attributes.authentication_mode",
        } <= dimensions:
            return pattern
    return None


def _collection(settings: DiracDataSettings) -> LearningCollection:
    run_id = "query_library_test"
    return LearningCollection(
        run_id=run_id,
        scope=LearningScope(settings.catalog, settings.database, settings.schema),
        table_profiles=[
            TableProfile(
                "users",
                1000,
                "samples/users.csv",
                [
                    ColumnProfile("users", "user_ref", "VARCHAR", 0, 0.0, 1000),
                    ColumnProfile("users", "signup_time", "TIMESTAMP", 0, 0.0, 1000),
                    ColumnProfile("users", "merchant_type", "VARCHAR", 0, 0.0, 5),
                    ColumnProfile("users", "acquisition_channel", "VARCHAR", 0, 0.0, 5),
                    ColumnProfile("users", "platform_plan", "VARCHAR", 0, 0.0, 4),
                    ColumnProfile("users", "account_state", "VARCHAR", 0, 0.0, 4),
                    ColumnProfile("users", "country", "VARCHAR", 0, 0.0, 1),
                ],
            ),
            TableProfile(
                "user_attributes",
                1000,
                "samples/user_attributes.csv",
                [
                    ColumnProfile("user_attributes", "user_ref", "VARCHAR", 0, 0.0, 1000),
                    ColumnProfile("user_attributes", "age", "INTEGER", 0, 0.0, 50),
                    ColumnProfile("user_attributes", "gender", "VARCHAR", 0, 0.0, 3),
                    ColumnProfile("user_attributes", "city", "VARCHAR", 0, 0.0, 21),
                    ColumnProfile("user_attributes", "state", "VARCHAR", 0, 0.0, 7),
                    ColumnProfile("user_attributes", "risk_band", "VARCHAR", 0, 0.0, 3),
                    ColumnProfile("user_attributes", "kyc_status", "VARCHAR", 0, 0.0, 3),
                ],
            ),
            TableProfile(
                "orders",
                15000,
                "samples/orders.csv",
                [
                    ColumnProfile("orders", "order_ref", "VARCHAR", 0, 0.0, 15000),
                    ColumnProfile("orders", "user_ref", "VARCHAR", 0, 0.0, 1000),
                    ColumnProfile("orders", "order_time", "TIMESTAMP", 0, 0.0, 10000),
                    ColumnProfile("orders", "order_amount", "DOUBLE", 0, 0.0, 12000),
                    ColumnProfile("orders", "order_state", "VARCHAR", 0, 0.0, 4),
                    ColumnProfile("orders", "checkout_surface", "VARCHAR", 0, 0.0, 5),
                    ColumnProfile("orders", "product_area", "VARCHAR", 0, 0.0, 5),
                ],
            ),
            TableProfile(
                "payments",
                18000,
                "samples/payments.csv",
                [
                    ColumnProfile("payments", "payment_ref", "VARCHAR", 0, 0.0, 18000),
                    ColumnProfile("payments", "order_ref", "VARCHAR", 0, 0.0, 15000),
                    ColumnProfile("payments", "user_ref", "VARCHAR", 0, 0.0, 1000),
                    ColumnProfile("payments", "rail_ref", "VARCHAR", 0, 0.0, 1000),
                    ColumnProfile("payments", "payment_time", "TIMESTAMP", 0, 0.0, 10000),
                    ColumnProfile("payments", "amount", "DOUBLE", 0, 0.0, 12000),
                    ColumnProfile("payments", "payment_status", "VARCHAR", 0, 0.0, 4),
                ],
            ),
            TableProfile(
                "payment_attributes",
                1000,
                "samples/payment_attributes.csv",
                [
                    ColumnProfile("payment_attributes", "rail_ref", "VARCHAR", 0, 0.0, 1000),
                    ColumnProfile("payment_attributes", "rail_type", "VARCHAR", 0, 0.0, 7),
                    ColumnProfile("payment_attributes", "issuer_name", "VARCHAR", 0, 0.0, 8),
                    ColumnProfile("payment_attributes", "route_partner", "VARCHAR", 0, 0.0, 4),
                    ColumnProfile("payment_attributes", "settlement_speed", "VARCHAR", 0, 0.0, 3),
                    ColumnProfile("payment_attributes", "authentication_mode", "VARCHAR", 0, 0.0, 5),
                    ColumnProfile("payment_attributes", "risk_band", "VARCHAR", 0, 0.0, 3),
                ],
            ),
        ],
        profile_artifact_key=learning_artifact_key(
            settings,
            run_id=run_id,
            relative_path="profiles/table_profiles.json",
        ),
        llm_context_artifact_key=learning_artifact_key(
            settings,
            run_id=run_id,
            relative_path="profiles/llm_context.json",
        ),
    )


def _write_joinable_pairs(settings: DiracDataSettings, store: LocalObjectStore, run_id: str) -> str:
    rows = [
        ("payments", "order_ref", "orders", "order_ref", "high"),
        ("payments", "user_ref", "orders", "user_ref", "low"),
        ("payments", "user_ref", "users", "user_ref", "high"),
        ("users", "user_ref", "user_attributes", "user_ref", "high"),
        ("payments", "user_ref", "user_attributes", "user_ref", "high"),
        ("payments", "rail_ref", "payment_attributes", "rail_ref", "high"),
    ]
    key = learning_artifact_key(settings, run_id=run_id, relative_path="joins/joinable_pairs.jsonl")
    store.write_text(
        key,
        "".join(
            json.dumps(
                {
                    "left_table": left_table,
                    "left_column": left_column,
                    "right_table": right_table,
                    "right_column": right_column,
                    "join_type": "many_to_one",
                    "confidence": confidence,
                },
                sort_keys=True,
            )
            + "\n"
            for left_table, left_column, right_table, right_column, confidence in rows
        ),
        content_type="application/jsonl",
    )
    return key


def _business_grounding() -> dict[str, object]:
    return {
        "metrics": [
            {
                "id": "tpv",
                "name": "TPV",
                "synonyms": ["total payment volume"],
                "columns": ["payments.amount", "payments.payment_status", "payments.payment_time"],
                "tables": ["payments"],
            },
            {
                "id": "psr",
                "name": "PSR",
                "synonyms": ["payment success rate"],
                "columns": ["payments.payment_status", "payments.payment_time"],
                "tables": ["payments"],
            },
        ]
    }


def _read_jsonl(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
