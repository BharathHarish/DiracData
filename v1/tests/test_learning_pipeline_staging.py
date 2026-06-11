import json
from pathlib import Path
import sys
import tempfile
import unittest

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config.settings import DiracDataSettings
from diracdata.learning import BusinessContext, LearningPipeline, LearningStage
from diracdata.llms import ChatModelMessage
from diracdata.query_engines import query_engine_from_settings
from diracdata.storage import LocalObjectStore


class FakeStagedLearningLLMClient:
    model = "fake-staged-model"

    def complete(self, messages: list[ChatModelMessage]) -> str:
        prompt = messages[0].content
        if "join_candidates" in prompt and "successful_queries" in prompt:
            return json.dumps({"join_candidates": []})
        if "TASK: sql_library_learning" in prompt:
            return json.dumps(_library_payload())
        if "TASK: nuance_learning" in prompt:
            return json.dumps(_nuance_payload())
        context = _context_from_prompt(prompt)
        return json.dumps(_description_payload(context))


class LearningPipelineStagingTest(unittest.TestCase):
    def test_pipeline_can_resume_from_join_stage_after_collect_and_describe(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings, engine, store = _harness(Path(tmpdir), artifact_strategy="deterministic")
            pipeline = LearningPipeline(
                settings=settings,
                query_engine=engine,
                object_store=store,
                llm_client=FakeStagedLearningLLMClient(),
            )
            try:
                first = pipeline.run_stages(
                    business_context=_business_context(),
                    run_id="staged_resume_test",
                    tables=["orders"],
                    end_stage=LearningStage.DESCRIPTION_GENERATION,
                )
                resumed = pipeline.run_stages(
                    run_id="staged_resume_test",
                    start_stage=LearningStage.JOIN_DISCOVERY,
                    end_stage=LearningStage.CONTEXT_TRAINING,
                )
            finally:
                engine.close()
            self.assertEqual(
                [stage.value for stage in first.executed_stages],
                [
                    LearningStage.DATA_COLLECTION.value,
                    LearningStage.DESCRIPTION_GENERATION.value,
                ],
            )
            self.assertIsNone(first.context)
            self.assertTrue(store.exists(first.description_artifact_key or ""))
            self.assertEqual(
                [stage.value for stage in resumed.executed_stages],
                [
                    LearningStage.JOIN_DISCOVERY.value,
                    LearningStage.CONTEXT_GRAPH_BUILDING.value,
                    LearningStage.EMBEDDING_GENERATION.value,
                    LearningStage.QUERY_LIBRARY_BUILDING.value,
                    LearningStage.NUANCE_BUILDING.value,
                    LearningStage.CONTEXT_TRAINING.value,
                ],
            )
            self.assertIsNotNone(resumed.context)
            self.assertTrue(store.exists(resumed.context.context_artifact_key if resumed.context else ""))

    def test_agentic_stage_run_publishes_sql_library(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings, engine, store = _harness(Path(tmpdir), artifact_strategy="agentic", context_mode="linear")
            pipeline = LearningPipeline(
                settings=settings,
                query_engine=engine,
                object_store=store,
                llm_client=FakeStagedLearningLLMClient(),
            )
            try:
                result = pipeline.run_stages(
                    business_context=_business_context(),
                    business_grounding=_business_grounding(),
                    run_id="agentic_stage_test",
                    tables=["orders"],
                )
            finally:
                engine.close()
            self.assertEqual(
                [stage.value for stage in result.executed_stages],
                [
                    LearningStage.DATA_COLLECTION.value,
                    LearningStage.DESCRIPTION_GENERATION.value,
                    LearningStage.JOIN_DISCOVERY.value,
                    LearningStage.CONTEXT_GRAPH_BUILDING.value,
                    LearningStage.EMBEDDING_GENERATION.value,
                    LearningStage.AGENTIC_ARTIFACT_GENERATION.value,
                    LearningStage.CONTEXT_TRAINING.value,
                ],
            )
            self.assertTrue(store.exists(result.state.query_libraries_manifest_artifact_key or ""))
            self.assertTrue(store.exists(result.context.context_artifact_key if result.context else ""))


def _harness(
    tmp_path: Path,
    *,
    artifact_strategy: str,
    context_mode: str = "linear",
) -> tuple[DiracDataSettings, object, LocalObjectStore]:
    parquet_path = tmp_path / "orders.parquet"
    catalog_path = tmp_path / "catalog.json"
    artifact_root = tmp_path / "artifacts"

    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE orders AS
        SELECT * FROM (
            VALUES
                (1, 100, 'west', DATE '2026-01-01', 12.50),
                (2, 101, 'east', DATE '2026-01-02', 25.00),
                (3, 100, 'west', DATE '2026-01-03', 30.00)
        ) AS t(order_id, customer_id, region, order_time, revenue)
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
        learning_artifact_strategy=artifact_strategy,
        learning_context_mode=context_mode,
    )
    return settings, query_engine_from_settings(settings), LocalObjectStore(artifact_root)


def _business_context() -> BusinessContext:
    return BusinessContext(
        "Commerce order analytics.",
        table_descriptions={"orders": "Customer purchase facts."},
        column_descriptions={"orders": {"region": "Sales geography."}},
        glossary={"revenue": "Money collected from customer purchases."},
    )


def _business_grounding() -> dict[str, object]:
    return {
        "metrics": [
            {
                "id": "revenue",
                "name": "Revenue",
                "parameterized_sql": {
                    "sql_contract": {
                        "aggregation": "SUM",
                        "column": "orders.revenue",
                        "time_column": "orders.order_time",
                    }
                },
            }
        ]
    }


def _description_payload(context: dict[str, object]) -> dict[str, object]:
    tables = {}
    columns = {}
    for table in context["tables"]:
        table_name = table["table_name"]
        tables[table_name] = {
            "short_description": f"{table_name} business activity.",
            "long_description": f"{table_name} is described by supplied business evidence.",
        }
        columns[table_name] = {
            column["column_name"]: {
                "short_description": f"{column['column_name']} business field.",
                "long_description": f"{column['column_name']} is grounded in the supplied profile evidence.",
            }
            for column in table["columns"]
        }
    return {"tables": tables, "columns": columns}


def _library_payload() -> dict[str, object]:
    return {
        "sql_library": [
            {
                "id": "sql_library:qp_orders_region_revenue",
                "kind": "pattern",
                "name": "orders_region_revenue",
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
                "sql": "SELECT region, SUM(revenue) AS revenue FROM orders WHERE order_time >= {{start_date}} GROUP BY region",
                "rules": ["Use order grain and orders.order_time."],
                "evidence": ["query_history", "business_grounding"],
                "confidence": "high",
                "sql_contract": {
                    "aggregation": "SUM",
                    "column": "orders.revenue",
                    "time_column": "orders.order_time",
                },
            }
        ],
    }


def _nuance_payload() -> dict[str, object]:
    return {
        "confounders": [],
        "invariants": [
            {
                "id": "inv_orders_time",
                "invariant_type": "time_semantics",
                "rule": "Use orders.order_time for order-period reporting.",
                "columns": ["orders.order_time"],
                "required_joins": [],
                "avoid_joins": [],
                "metrics": ["revenue"],
                "source": "business_grounding",
                "evidence": ["business_grounding", "query_history"],
                "confidence": "high",
                "approval_status": "candidate",
            }
        ],
    }


def _context_from_prompt(prompt: str) -> dict[str, object]:
    start = prompt.rfind("```json")
    end = prompt.rfind("```")
    if start < 0 or end <= start:
        raise AssertionError("prompt does not contain JSON context block")
    return json.loads(prompt[start + len("```json") : end].strip())


if __name__ == "__main__":
    unittest.main()
