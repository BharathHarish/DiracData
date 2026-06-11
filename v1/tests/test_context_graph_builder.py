import json
from pathlib import Path
import tempfile
import unittest

from diracdata.config.settings import DiracDataSettings
from diracdata.learning import (
    ColumnProfile,
    ContextGraphBuilder,
    LearningCollection,
    LearningScope,
    QueryHistoryRecord,
    TableProfile,
)
from diracdata.learning.paths import active_learning_artifact_key, learning_artifact_key
from diracdata.storage import LocalObjectStore


class ContextGraphBuilderTest(unittest.TestCase):
    def test_builds_graph_and_retrieval_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
                learning_embedding_provider="none",
            )
            store = LocalObjectStore(tmpdir)
            collection = _write_collection(settings, store)
            description_key = _write_descriptions(settings, store, collection.run_id)
            join_key = _write_joinable_pairs(settings, store, collection.run_id)
            grounding = _business_grounding()
            builder = ContextGraphBuilder(settings=settings, object_store=store)

            result = builder.build(
                collection=collection,
                description_artifact_key=description_key,
                joinable_pairs_artifact_key=join_key,
                business_grounding=grounding,
                query_history_records=_query_history_records(),
            )

            nodes = _read_jsonl(store.read_text(result.nodes_artifact_key))
            edges = _read_jsonl(store.read_text(result.edges_artifact_key))
            query_patterns = _read_jsonl(store.read_text(result.query_patterns_artifact_key))
            retrieval_docs = _read_jsonl(store.read_text(result.retrieval_documents_artifact_key))
            bm25 = store.read_json(result.bm25_index_artifact_key)
            manifest = store.read_json(result.manifest_artifact_key)
            active_embedding_key = active_learning_artifact_key(
                settings,
                relative_path="embeddings/manifest.json",
            )
            embedding_manifest_exists = store.exists(active_embedding_key)

        node_types = {node["node_type"] for node in nodes}
        edge_types = {edge["edge_type"] for edge in edges}
        retrieval_types = {doc["retrieval_type"] for doc in retrieval_docs}

        self.assertIn("table", node_types)
        self.assertIn("column", node_types)
        self.assertIn("business_term", node_types)
        self.assertIn("metric", node_types)
        self.assertIn("sql_template", node_types)
        self.assertIn("query_pattern", node_types)
        self.assertIn("join_pair", node_types)
        self.assertIn("table_has_column", edge_types)
        self.assertIn("column_joins_column", edge_types)
        self.assertIn("glossary_uses_column", edge_types)
        self.assertIn("sql_templates_uses_join", edge_types)
        self.assertIn("query_pattern_used_table", edge_types)
        self.assertIn("query_pattern_used_join", edge_types)
        self.assertEqual(len(query_patterns), 1)
        self.assertIn("column", retrieval_types)
        self.assertIn("table_container", retrieval_types)
        self.assertIn("metrics", retrieval_types)
        self.assertEqual(bm25["algorithm"], "bm25_plus")
        self.assertGreaterEqual(bm25["document_count"], len(retrieval_docs))
        self.assertFalse(embedding_manifest_exists)
        self.assertEqual(manifest["node_count"], result.node_count)
        self.assertEqual(manifest["graph_cache"]["backend"], "networkx")
        self.assertIsInstance(manifest["graph_cache"]["available"], bool)
        self.assertNotIn(
            "embedding_manifest_artifact_key",
            manifest["canonical_artifacts"],
        )

    def test_build_updates_active_manifest_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
            )
            store = LocalObjectStore(tmpdir)
            collection = _write_collection(settings, store)
            description_key = _write_descriptions(settings, store, collection.run_id)
            join_key = _write_joinable_pairs(settings, store, collection.run_id)
            active_manifest_key = active_learning_artifact_key(settings, relative_path="manifest.json")
            store.write_json(active_manifest_key, {"active_run_id": collection.run_id})
            builder = ContextGraphBuilder(settings=settings, object_store=store)

            result = builder.build(
                collection=collection,
                description_artifact_key=description_key,
                joinable_pairs_artifact_key=join_key,
                business_grounding=_business_grounding(),
            )
            active_manifest = store.read_json(active_manifest_key)

        self.assertEqual(
            active_manifest["immutable_artifacts"]["context_graph_manifest_artifact_key"],
            result.manifest_artifact_key,
        )
        self.assertEqual(active_manifest["context_graph"]["node_count"], result.node_count)


def _write_collection(settings: DiracDataSettings, store: LocalObjectStore) -> LearningCollection:
    run_id = "context_graph_test"
    base = f"artifacts/learning/{settings.catalog}/{settings.database}/{settings.schema}/{run_id}/samples"
    online_sample = "billing_client_ref,sale_calendar_day_ref,merchandise_ref\n100,1,10\n101,2,11\n"
    client_sample = "client_record,current_address_ref\n100,200\n101,201\n"
    calendar_sample = "calendar_day_record,year\n1,2001\n2,2002\n"
    merchandise_sample = "merchandise_record,category\n10,Jewelry\n11,Electronics\n"
    samples = {
        "online_purchases": online_sample,
        "clients": client_sample,
        "calendar_days": calendar_sample,
        "merchandise": merchandise_sample,
    }
    sample_keys = {}
    for table_name, sample in samples.items():
        key = f"{base}/{table_name}.csv"
        store.write_text(key, sample)
        sample_keys[table_name] = key
    return LearningCollection(
        run_id=run_id,
        scope=LearningScope(settings.catalog, settings.database, settings.schema),
        table_profiles=[
            TableProfile(
                "online_purchases",
                2,
                sample_keys["online_purchases"],
                [
                    ColumnProfile("online_purchases", "billing_client_ref", "INTEGER", 0, 0.0, 2),
                    ColumnProfile(
                        "online_purchases",
                        "sale_calendar_day_ref",
                        "INTEGER",
                        0,
                        0.0,
                        2,
                    ),
                    ColumnProfile("online_purchases", "merchandise_ref", "INTEGER", 0, 0.0, 2),
                ],
            ),
            TableProfile(
                "clients",
                2,
                sample_keys["clients"],
                [
                    ColumnProfile("clients", "client_record", "INTEGER", 0, 0.0, 2),
                    ColumnProfile("clients", "current_address_ref", "INTEGER", 0, 0.0, 2),
                ],
            ),
            TableProfile(
                "calendar_days",
                2,
                sample_keys["calendar_days"],
                [
                    ColumnProfile("calendar_days", "calendar_day_record", "INTEGER", 0, 0.0, 2),
                    ColumnProfile("calendar_days", "year", "INTEGER", 0, 0.0, 2),
                ],
            ),
            TableProfile(
                "merchandise",
                2,
                sample_keys["merchandise"],
                [
                    ColumnProfile("merchandise", "merchandise_record", "INTEGER", 0, 0.0, 2),
                    ColumnProfile("merchandise", "category", "VARCHAR", 0, 0.0, 2),
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


def _write_descriptions(settings: DiracDataSettings, store: LocalObjectStore, run_id: str) -> str:
    payload = {
        "tables": {
            "online_purchases": {
                "short_description": "Online shopping activity.",
                "long_description": "Online purchases records website orders, customer identity, products, and sale dates.",
            },
            "clients": {
                "short_description": "Customer accounts.",
                "long_description": "Clients are customer account records used for shopper identity.",
            },
            "calendar_days": {
                "short_description": "Calendar dates.",
                "long_description": "Calendar days support year and date filtering for purchase timing.",
            },
            "merchandise": {
                "short_description": "Products.",
                "long_description": "Merchandise describes products and categories sold by the retailer.",
            },
        },
        "columns": {
            "online_purchases": {
                "billing_client_ref": {
                    "short_description": "Billing customer.",
                    "long_description": "Billing client links online purchases to the customer counted for online shopping metrics.",
                },
                "sale_calendar_day_ref": {
                    "short_description": "Sale day.",
                    "long_description": "Sale day links online purchases to calendar timing.",
                },
                "merchandise_ref": {
                    "short_description": "Product link.",
                    "long_description": "Product link connects online purchases to merchandise categories.",
                },
            },
            "clients": {
                "client_record": {
                    "short_description": "Customer record.",
                    "long_description": "Customer record identifies a shopper account.",
                },
                "current_address_ref": {
                    "short_description": "Current address.",
                    "long_description": "Current address links a shopper to their current location.",
                },
            },
            "calendar_days": {
                "calendar_day_record": {
                    "short_description": "Calendar day.",
                    "long_description": "Calendar day identifies a date row.",
                },
                "year": {
                    "short_description": "Year.",
                    "long_description": "Year supports annual filtering.",
                },
            },
            "merchandise": {
                "merchandise_record": {
                    "short_description": "Product record.",
                    "long_description": "Product record identifies a merchandise item.",
                },
                "category": {
                    "short_description": "Product category.",
                    "long_description": "Product category supports product-family filtering such as Jewelry.",
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


def _write_joinable_pairs(settings: DiracDataSettings, store: LocalObjectStore, run_id: str) -> str:
    rows = [
        {
            "left_table": "online_purchases",
            "left_column": "billing_client_ref",
            "right_table": "clients",
            "right_column": "client_record",
            "join_type": "many_to_one",
            "confidence": "high",
        },
        {
            "left_table": "online_purchases",
            "left_column": "sale_calendar_day_ref",
            "right_table": "calendar_days",
            "right_column": "calendar_day_record",
            "join_type": "many_to_one",
            "confidence": "high",
        },
        {
            "left_table": "online_purchases",
            "left_column": "merchandise_ref",
            "right_table": "merchandise",
            "right_column": "merchandise_record",
            "join_type": "many_to_one",
            "confidence": "high",
        },
    ]
    key = learning_artifact_key(settings, run_id=run_id, relative_path="joins/joinable_pairs.jsonl")
    store.write_text(key, "\n".join(json.dumps(row) for row in rows) + "\n")
    return key


def _business_grounding() -> dict[str, object]:
    return {
        "glossary": [
            {
                "id": "online_customer",
                "term": "Online customer",
                "synonyms": ["shopped online"],
                "definition": "A billing client with an online purchase.",
                "tables": ["online_purchases", "clients"],
                "columns": ["online_purchases.billing_client_ref", "clients.client_record"],
            }
        ],
        "definitions": [],
        "defaults": [
            {
                "id": "online_customer_identity",
                "policy": "Use billing client for online customer identity.",
                "field": "online_purchases.billing_client_ref",
            }
        ],
        "metrics": [
            {
                "id": "online_customers",
                "name": "Online customers",
                "description": "Counts unique online billing clients.",
                "columns": ["online_purchases.billing_client_ref"],
            }
        ],
        "sql_templates": [
            {
                "id": "online_customer_count",
                "name": "Online customer count",
                "description": "Count distinct online billing clients.",
                "required_tables": ["online_purchases", "clients"],
                "join_path": [
                    ["online_purchases.billing_client_ref", "clients.client_record"],
                    ["online_purchases.sale_calendar_day_ref", "calendar_days.calendar_day_record"],
                ],
            }
        ],
        "ground_truth_sql": [],
    }


def _query_history_records() -> list[QueryHistoryRecord]:
    return [
        QueryHistoryRecord(
            {
                "statement_id": "q1",
                "execution_status": "FINISHED",
                "statement_type": "SELECT",
                "statement_text": (
                    "SELECT count(distinct op.billing_client_ref) "
                    "FROM online_purchases op "
                    "JOIN clients c ON op.billing_client_ref = c.client_record "
                    "JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record "
                    "WHERE cd.year = 2002"
                ),
            }
        ),
        QueryHistoryRecord(
            {
                "statement_id": "q2",
                "execution_status": "FAILED",
                "statement_type": "SELECT",
                "statement_text": "SELECT * FROM online_purchases",
            }
        ),
    ]


def _read_jsonl(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
