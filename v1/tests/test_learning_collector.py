import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config.settings import DiracDataSettings
from diracdata.learning import BusinessContext, SchemaLearningCollector
from diracdata.query_engines import query_engine_from_settings
from diracdata.storage import LocalObjectStore


class LearningCollectorTest(unittest.TestCase):
    def test_collects_samples_and_profiles_to_artifact_store(self) -> None:
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
                        (1, 100, 'west', 12.50),
                        (2, 101, 'east', 25.00),
                        (3, 100, 'west', 30.00)
                ) AS t(order_id, customer_id, region, revenue)
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
                learning_distinct_limit=1000,
                learning_top_values_limit=1,
                learning_context_distinct_values_limit=1,
            )
            engine = query_engine_from_settings(settings)
            store = LocalObjectStore(artifact_root)
            collector = SchemaLearningCollector(
                settings=settings,
                query_engine=engine,
                object_store=store,
            )

            try:
                collection = collector.collect(
                    business_context=BusinessContext("Commerce order analytics for regional revenue."),
                    run_id="learn_test",
                )
            finally:
                engine.close()

            self.assertEqual(collection.run_id, "learn_test")
            self.assertEqual(len(collection.table_profiles), 1)
            self.assertTrue(store.exists(collection.profile_artifact_key))
            self.assertTrue(store.exists(collection.llm_context_artifact_key))

            sample_key = collection.table_profiles[0].sample_artifact_key
            sample_rows = list(csv.reader(store.read_text(sample_key).splitlines()))
            self.assertEqual(sample_rows[0], ["order_id", "customer_id", "region", "revenue"])
            self.assertEqual(len(sample_rows), 3)

            profile = store.read_json(collection.profile_artifact_key)
            self.assertEqual(profile["scope"]["catalog"], "commerce_pod")
            self.assertEqual(profile["tables"][0]["table_name"], "orders")
            self.assertEqual(profile["tables"][0]["row_count"], 3)

            region_profile = next(
                column
                for column in profile["tables"][0]["columns"]
                if column["column_name"] == "region"
            )
            self.assertEqual(region_profile["distinct_count"], 2)
            self.assertEqual(region_profile["distinct_values"], ["east", "west"])
            self.assertEqual(len(region_profile["top_values"]), 1)

            llm_context = store.read_json(collection.llm_context_artifact_key)
            region_context = next(
                column
                for column in llm_context["tables"][0]["columns"]
                if column["column_name"] == "region"
            )
            self.assertEqual(region_context["distinct_values"], ["east"])
