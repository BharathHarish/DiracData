import json
from pathlib import Path
import sys
import tempfile
import unittest

import duckdb
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config.settings import DiracDataSettings
from diracdata.learning import (
    AgenticLearningArtifactBuilder,
    BusinessContext,
    LearningCollection,
    LearningPipeline,
    LearningScope,
    QueryHistoryRecord,
    TableProfile,
)
from diracdata.learning.models import ColumnProfile
from diracdata.learning.paths import active_learning_artifact_key, learning_artifact_key
from diracdata.llms import ChatModelMessage
from diracdata.query_engines import query_engine_from_settings
from diracdata.storage import LocalObjectStore


class FakeAgenticLearningLLMClient:
    model = "fake-agentic-model"

    def __init__(
        self,
        *,
        broken_agentic_once: bool = False,
        sparse_library_payload: bool = False,
    ) -> None:
        self.broken_agentic_once = broken_agentic_once
        self.sparse_library_payload = sparse_library_payload
        self._broken_sent = False

    def complete(self, messages: list[ChatModelMessage]) -> str:
        prompt = messages[0].content
        if "join_candidates" in prompt and "successful_queries" in prompt:
            return json.dumps({"join_candidates": []})
        if "repairing malformed JSON" in prompt:
            if self.broken_agentic_once and not self._broken_sent:
                self._broken_sent = True
                return json.dumps(_library_payload())[:-10]
        if "TASK: sql_library_learning" in prompt:
            return json.dumps(
                _sparse_library_payload() if self.sparse_library_payload else _library_payload()
            )
        if "TASK: nuance_learning" in prompt:
            return json.dumps({} if self.sparse_library_payload else _nuance_payload())
        context = _context_from_prompt(prompt)
        return json.dumps(_description_payload(context))


class AgenticLearningTest(unittest.TestCase):
    def test_agentic_builder_writes_sql_library_nuance_and_ast_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="commerce_pod",
                database="analytics",
                schema="main",
                learning_artifact_strategy="agentic",
                learning_context_mode="schema_ast",
            )
            store = LocalObjectStore(tmpdir)
            collection = _collection(settings)
            description_key = _write_descriptions(settings, store, collection.run_id)
            active_manifest_key = active_learning_artifact_key(settings, relative_path="manifest.json")
            store.write_json(active_manifest_key, {"active_run_id": collection.run_id})
            builder = AgenticLearningArtifactBuilder(
                settings=settings,
                object_store=store,
                llm_client=FakeAgenticLearningLLMClient(),
            )

            result = builder.build(
                collection=collection,
                description_artifact_key=description_key,
                business_grounding=_business_grounding(),
                query_history_records=_query_history_records(),
            )

            query_manifest = store.read_json(result.query_library_result.manifest_artifact_key)
            nuance_manifest = store.read_json(result.nuance_result.manifest_artifact_key)
            sql_library = yaml.safe_load(
                store.read_text(result.query_library_result.sql_library_artifact_key or "")
            )
            schema_ast_manifest = store.read_json(result.schema_ast_manifest_artifact_key or "")
            active_manifest = store.read_json(active_manifest_key)

        self.assertEqual(query_manifest["producer"], "agentic_learning")
        self.assertEqual(query_manifest["artifact_type"], "sql_library")
        self.assertEqual(sql_library["artifact_type"], "sql_library")
        self.assertGreaterEqual(len(sql_library["entries"]), 1)
        self.assertGreaterEqual(query_manifest["query_pattern_count"], 1)
        self.assertEqual(nuance_manifest["producer"], "agentic_learning")
        self.assertGreaterEqual(nuance_manifest["invariant_count"], 2)
        self.assertEqual(schema_ast_manifest["artifact_type"], "schema_ast")
        self.assertGreater(schema_ast_manifest["node_count"], 0)
        self.assertIn("schema_ast_manifest_artifact_key", active_manifest["active_artifacts"])
        self.assertEqual(active_manifest["agentic_learning"]["context_mode"], "schema_ast")

    def test_learning_pipeline_agentic_mode_publishes_ast_and_sql_library_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            parquet_path = tmp_path / "orders.parquet"
            catalog_path = tmp_path / "catalog.json"
            artifact_root = tmp_path / "artifacts"

            con = duckdb.connect(":memory:")
            con.execute(
                """
                CREATE TABLE orders AS
                SELECT * FROM (
                    VALUES
                        (1, 'west', DATE '2026-01-01', 12.50),
                        (2, 'east', DATE '2026-01-02', 25.00),
                        (3, 'west', DATE '2026-01-03', 30.00)
                ) AS t(order_id, region, order_time, revenue)
                """
            )
            con.execute(f"COPY orders TO '{parquet_path}' (FORMAT parquet)")
            con.close()

            catalog_path.write_text(
                json.dumps(
                    {
                        "catalog": "commerce_pod",
                        "database": "analytics",
                        "schema": "main",
                        "tables": [
                            {
                                "name": "orders",
                                "path": str(parquet_path),
                                "format": "parquet",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            settings = DiracDataSettings(
                catalog="commerce_pod",
                database="analytics",
                schema="main",
                catalog_config=catalog_path,
                learning_sample_limit=2,
                learning_distinct_limit=10,
                learning_embedding_provider="none",
                learning_artifact_strategy="agentic",
                learning_context_mode="schema_ast",
            )
            engine = query_engine_from_settings(settings)
            store = LocalObjectStore(artifact_root)
            pipeline = LearningPipeline(
                settings=settings,
                query_engine=engine,
                object_store=store,
                llm_client=FakeAgenticLearningLLMClient(),
            )

            try:
                result = pipeline.run(
                    business_context=BusinessContext(
                        "Commerce order analytics.",
                        table_descriptions={"orders": "Customer purchase facts."},
                        column_descriptions={"orders": {"region": "Sales geography."}},
                        glossary={"revenue": "Money collected from customer purchases."},
                    ),
                    run_id="agentic_pipeline_test",
                    tables=["orders"],
                    query_history_records=_query_history_records(),
                    business_grounding=_business_grounding(),
                )
            finally:
                engine.close()

            active_manifest = store.read_json(
                active_learning_artifact_key(settings, relative_path="manifest.json")
            )

        self.assertEqual(
            result.context.metadata["learning_artifact_strategy"],
            "agentic",
        )
        self.assertEqual(result.context.metadata["learning_context_mode"], "schema_ast")
        self.assertTrue(result.context.schema_ast_manifest_artifact_key)
        self.assertIn("schema_ast_manifest_artifact_key", active_manifest["active_artifacts"])
        self.assertIn("agentic_learning", active_manifest)

    def test_agentic_builder_repairs_malformed_json_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="commerce_pod",
                database="analytics",
                schema="main",
                learning_artifact_strategy="agentic",
                learning_context_mode="linear",
                learning_agentic_repair_attempts=1,
            )
            store = LocalObjectStore(tmpdir)
            collection = _collection(settings)
            description_key = _write_descriptions(settings, store, collection.run_id)
            active_manifest_key = active_learning_artifact_key(settings, relative_path="manifest.json")
            store.write_json(active_manifest_key, {"active_run_id": collection.run_id})
            builder = AgenticLearningArtifactBuilder(
                settings=settings,
                object_store=store,
                llm_client=FakeAgenticLearningLLMClient(broken_agentic_once=True),
            )

            result = builder.build(
                collection=collection,
                description_artifact_key=description_key,
                business_grounding=_business_grounding(),
                query_history_records=_query_history_records(),
            )

            sql_library = yaml.safe_load(
                store.read_text(result.query_library_result.sql_library_artifact_key or "")
            )

        self.assertEqual(sql_library["artifact_type"], "sql_library")

    def test_agentic_builder_seeds_metric_templates_and_invariants_from_business_grounding(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = DiracDataSettings(
                catalog="commerce_pod",
                database="analytics",
                schema="main",
                learning_artifact_strategy="agentic",
                learning_context_mode="linear",
            )
            store = LocalObjectStore(tmpdir)
            collection = _collection(settings)
            description_key = _write_descriptions(settings, store, collection.run_id)
            active_manifest_key = active_learning_artifact_key(settings, relative_path="manifest.json")
            store.write_json(active_manifest_key, {"active_run_id": collection.run_id})
            builder = AgenticLearningArtifactBuilder(
                settings=settings,
                object_store=store,
                llm_client=FakeAgenticLearningLLMClient(sparse_library_payload=True),
            )

            result = builder.build(
                collection=collection,
                description_artifact_key=description_key,
                business_grounding=_business_grounding(),
                query_history_records=_query_history_records(),
            )

            query_manifest = store.read_json(result.query_library_result.manifest_artifact_key)
            nuance_manifest = store.read_json(result.nuance_result.manifest_artifact_key)
            sql_library = yaml.safe_load(
                store.read_text(result.query_library_result.sql_library_artifact_key or "")
            )
            invariants = yaml.safe_load(store.read_text(result.nuance_result.invariants_artifact_key))

        self.assertGreaterEqual(query_manifest["sql_template_count"], 1)
        self.assertGreaterEqual(query_manifest["metric_usage_count"], 1)
        self.assertGreaterEqual(nuance_manifest["invariant_count"], 1)
        self.assertTrue(
            any(row["id"] == "sql_library:metric_revenue" for row in sql_library["entries"])
        )
        self.assertTrue(
            any(row["id"] == "invariant:metric_contract:revenue" for row in invariants["invariants"])
        )


def _collection(settings: DiracDataSettings) -> LearningCollection:
    run_id = "agentic_learning_test"
    return LearningCollection(
        run_id=run_id,
        scope=LearningScope(settings.catalog, settings.database, settings.schema),
        table_profiles=[
            TableProfile(
                "orders",
                3,
                "samples/orders.csv",
                [
                    ColumnProfile("orders", "order_id", "INTEGER", 0, 0.0, 3),
                    ColumnProfile(
                        "orders",
                        "region",
                        "VARCHAR",
                        0,
                        0.0,
                        2,
                        distinct_values=["west", "east"],
                    ),
                    ColumnProfile("orders", "order_time", "DATE", 0, 0.0, 3),
                    ColumnProfile("orders", "revenue", "DOUBLE", 0, 0.0, 3),
                ],
            )
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
            "orders": {
                "short_description": "Customer purchase records.",
                "long_description": "Orders stores one row per purchase with order date, geography, and revenue.",
            }
        },
        "columns": {
            "orders": {
                "order_id": {
                    "short_description": "Order record identifier.",
                    "long_description": "Order identifier for one purchase row.",
                },
                "region": {
                    "short_description": "Sales geography for the order.",
                    "long_description": "Region used for geographic reporting and slicing.",
                },
                "order_time": {
                    "short_description": "Order date.",
                    "long_description": "Date when the order happened.",
                },
                "revenue": {
                    "short_description": "Revenue collected from the order.",
                    "long_description": "Order revenue used for sales reporting.",
                },
            }
        },
    }
    key = learning_artifact_key(
        settings,
        run_id=run_id,
        relative_path="descriptions/metadata_descriptions.json",
    )
    store.write_json(key, payload)
    return key


def _business_grounding() -> dict[str, object]:
    return {
        "metrics": [
            {
                "id": "revenue",
                "name": "Revenue",
                "parameterized_sql": {
                    "description": "Canonical revenue SQL template.",
                    "parameters": [
                        {
                            "name": "start_date",
                            "type": "date",
                            "required": True,
                        }
                    ],
                    "sql": (
                        "SELECT SUM(revenue) AS revenue FROM orders "
                        "WHERE order_time >= {{ start_date }}"
                    ),
                    "required_tables": ["orders"],
                    "required_columns": ["orders.revenue", "orders.order_time"],
                    "sql_contract": {
                        "aggregation": "SUM",
                        "column": "orders.revenue",
                        "time_column": "orders.order_time",
                    }
                },
            }
        ],
        "defaults": [
            {
                "id": "order_time_default",
                "policy": "Use orders.order_time for order-period reporting.",
                "field": "orders.order_time",
            }
        ],
    }


def _query_history_records() -> list[QueryHistoryRecord]:
    return [
        QueryHistoryRecord(
            {
                "statement_id": "stmt_1",
                "statement_text": (
                    "SELECT region, SUM(revenue) AS revenue "
                    "FROM orders WHERE order_time >= DATE '2026-01-01' "
                    "GROUP BY region"
                ),
                "execution_status": "SUCCESS",
                "statement_type": "SELECT",
            }
        )
    ]


def _description_payload(context: dict[str, object]) -> dict[str, object]:
    tables = {}
    columns = {}
    for table in context["tables"]:
        table_name = table["table_name"]
        tables[table_name] = {
            "short_description": f"{table_name} business activity.",
            "long_description": f"{table_name} is described by the supplied business evidence.",
        }
        columns[table_name] = {
            column["column_name"]: {
                "short_description": f"{column['column_name']} business field.",
                "long_description": (
                    f"{column['column_name']} is described from the supplied business and profile evidence."
                ),
            }
            for column in table["columns"]
        }
    return {"tables": tables, "columns": columns}


def _library_payload() -> dict[str, object]:
    return {
        "sql_library": [
            {
                "id": "sql_library:revenue_by_region",
                "kind": "pattern",
                "name": "revenue_by_region",
                "query_count": 1,
                "fact_table": "orders",
                "tables": ["orders"],
                "metrics": ["revenue"],
                "dimension_columns": ["orders.region"],
                "filter_columns": ["orders.order_time"],
                "required_joins": [],
                "avoid_joins": [],
                "compact_contract": {
                    "fact_table": "orders",
                    "tables": ["orders"],
                    "metrics": ["revenue"],
                    "dimension_columns": ["orders.region"],
                    "filter_columns": ["orders.order_time"],
                    "required_joins": [],
                    "avoid_joins": [],
                },
                "parameters": ["start_date"],
                "sql": (
                    "SELECT region, SUM(revenue) AS revenue "
                    "FROM orders WHERE order_time >= {{ start_date }} GROUP BY region"
                ),
                "rules": ["Use order grain and filter on orders.order_time."],
                "evidence": ["query_history", "business_grounding"],
                "confidence": "high",
                "sql_contract": {
                    "aggregation": "SUM",
                    "column": "orders.revenue",
                    "time_column": "orders.order_time",
                },
            }
        ]
    }


def _sparse_library_payload() -> dict[str, object]:
    return {
        "sql_library": [
            {
                "id": "sql_library:revenue_by_region",
                "kind": "pattern",
                "name": "revenue_by_region",
                "query_count": 1,
                "fact_table": "orders",
                "tables": ["orders"],
                "metrics": ["revenue"],
                "dimension_columns": ["orders.region"],
                "filter_columns": ["orders.order_time"],
                "required_joins": [],
                "avoid_joins": [],
                "compact_contract": {
                    "fact_table": "orders",
                    "tables": ["orders"],
                    "metrics": ["revenue"],
                    "dimension_columns": ["orders.region"],
                    "filter_columns": ["orders.order_time"],
                    "required_joins": [],
                    "avoid_joins": [],
                },
                "parameters": [],
                "sql": "",
                "rules": [],
                "evidence": ["query_history", "business_grounding"],
                "confidence": "high",
            }
        ]
    }


def _nuance_payload() -> dict[str, object]:
    return {
        "confounders": [],
        "invariants": [
            {
                "id": "invariant:metric_contract:revenue",
                "invariant_type": "metric_contract",
                "rule": "Use SUM(orders.revenue) when revenue is requested.",
                "columns": ["orders.revenue", "orders.order_time"],
                "required_joins": [],
                "avoid_joins": [],
                "metrics": ["revenue"],
                "source": "business_grounding",
                "evidence": ["business_grounding"],
                "confidence": "high",
                "approval_status": "candidate",
            },
            {
                "id": "invariant:time_semantics:orders",
                "invariant_type": "time_semantics",
                "rule": "Use orders.order_time for order-period filters.",
                "columns": ["orders.order_time"],
                "required_joins": [],
                "avoid_joins": [],
                "metrics": ["revenue"],
                "source": "agentic_learning",
                "evidence": ["business_grounding", "query_history"],
                "confidence": "high",
                "approval_status": "candidate",
            },
        ],
    }


def _context_from_prompt(prompt: str) -> dict[str, object]:
    start = prompt.rfind("```json")
    end = prompt.rfind("```")
    if start < 0 or end <= start:
        raise AssertionError("prompt does not contain JSON context block")
    return json.loads(prompt[start + len("```json") : end].strip())


def _read_jsonl(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
