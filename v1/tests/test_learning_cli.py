from argparse import Namespace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from diracdata.config import DiracDataSettings
from run_learning_pipeline import _settings_with_learning_overrides


class LearningCliTest(unittest.TestCase):
    def test_scope_and_learning_overrides_apply_to_settings_for_one_run(self) -> None:
        settings = DiracDataSettings(
            catalog="commerce_pod",
            database="analytics",
            schema="main",
            catalog_config=Path("conf/catalogs/commerce_pod.minio.json"),
            learning_artifact_strategy="deterministic",
            learning_context_mode="linear",
        )

        updated = _settings_with_learning_overrides(
            settings,
            Namespace(
                catalog="fintech_pod",
                database="analytics",
                schema="fintech_schema",
                catalog_config=Path("conf/catalogs/fintech_schema.minio.json"),
                run_id="fintech_stage_test",
                artifact_strategy="agentic",
                context_mode="schema_ast",
            ),
        )

        self.assertEqual(updated.catalog, "fintech_pod")
        self.assertEqual(updated.schema, "fintech_schema")
        self.assertEqual(updated.catalog_config, Path("conf/catalogs/fintech_schema.minio.json"))
        self.assertEqual(updated.learning_run_id, "fintech_stage_test")
        self.assertEqual(updated.learning_artifact_strategy, "agentic")
        self.assertEqual(updated.learning_context_mode, "schema_ast")
        self.assertEqual(settings.catalog, "commerce_pod")
        self.assertEqual(settings.learning_artifact_strategy, "deterministic")


if __name__ == "__main__":
    unittest.main()
