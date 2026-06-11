from pathlib import Path
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import settings_from_env
from diracdata.learning import BusinessContext, LearningPipeline
from diracdata.query_engines import query_engine_from_settings
from diracdata.storage import LocalObjectStore


class LiveLearningPipelineTest(unittest.TestCase):
    def test_live_learning_pipeline_with_anthropic(self) -> None:
        if os.environ.get("DIRACDATA_RUN_LIVE_LEARNING") != "1":
            raise unittest.SkipTest("set DIRACDATA_RUN_LIVE_LEARNING=1 to run live learning e2e")

        settings = settings_from_env(".env")
        if not settings.anthropic_api_key:
            raise unittest.SkipTest("DIRACDATA_ANTHROPIC_API_KEY is not configured")

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = query_engine_from_settings(settings)
            store = LocalObjectStore(tmpdir)
            pipeline = LearningPipeline(
                settings=settings,
                query_engine=engine,
                object_store=store,
            )

            try:
                result = pipeline.run(
                    business_context=BusinessContext(
                        "Commerce analytics pod for sales, customers, items, dates, and related dimensions.",
                        table_descriptions={
                            "income_band": "Income ranges used for customer or household segmentation."
                        },
                        column_descriptions={
                            "income_band": {
                                "ib_lower_bound": "Lower end of an income range.",
                                "ib_upper_bound": "Upper end of an income range.",
                            }
                        },
                    ),
                    run_id="live_learning_e2e",
                    tables=["income_band"],
                )
            finally:
                engine.close()

            self.assertEqual(result.context.table_names, ["income_band"])
            self.assertTrue(store.exists(result.collection.profile_artifact_key))
            self.assertTrue(store.exists(result.description_artifact_key))
            self.assertTrue(store.exists(result.context.context_artifact_key))
            descriptions = store.read_json(result.description_artifact_key)
            self.assertIn("income_band", descriptions["tables"])
            profiled_columns = {
                column.column_name
                for table in result.collection.table_profiles
                for column in table.columns
            }
            self.assertEqual(profiled_columns, set(descriptions["columns"]["income_band"]))
