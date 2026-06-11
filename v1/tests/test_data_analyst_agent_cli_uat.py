import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from diracdata.config import DiracDataSettings
from run_data_analyst_agent_uat import _interactive_enabled, _settings_with_agent_overrides


class DataAnalystAgentCliUatTest(unittest.TestCase):
    def test_interactive_flag_overrides_auto_detection(self) -> None:
        self.assertTrue(_interactive_enabled(True))
        self.assertFalse(_interactive_enabled(False))

    def test_agent_model_overrides_apply_to_settings_for_one_run(self) -> None:
        settings = DiracDataSettings(
            agent_llm_provider="anthropic",
            agent_llm_model="claude-sonnet-4-6",
            agent_llm_max_tokens=8192,
            agent_llm_temperature=0.0,
        )
        updated = _settings_with_agent_overrides(
            settings,
            Namespace(
                catalog=None,
                database=None,
                schema=None,
                catalog_config=None,
                agent_model_profile=None,
                agent_llm_provider="anthropic",
                agent_model="claude-haiku-4-5-20251001",
                agent_max_tokens=2048,
                agent_temperature=0.1,
                bedrock_region=None,
            ),
        )

        self.assertEqual(updated.agent_llm_provider, "anthropic")
        self.assertEqual(updated.agent_llm_model, "claude-haiku-4-5-20251001")
        self.assertEqual(updated.agent_llm_max_tokens, 2048)
        self.assertEqual(updated.agent_llm_temperature, 0.1)
        self.assertEqual(settings.agent_llm_model, "claude-sonnet-4-6")

    def test_agent_model_profile_override_applies_to_settings_for_one_run(self) -> None:
        settings = DiracDataSettings(agent_model_profile=None)

        updated = _settings_with_agent_overrides(
            settings,
            Namespace(
                catalog=None,
                database=None,
                schema=None,
                catalog_config=None,
                agent_model_profile="bedrock_qwen3_next_80b_a3b_ap_south_1",
                agent_llm_provider=None,
                agent_model=None,
                agent_max_tokens=None,
                agent_temperature=None,
                bedrock_region="ap-south-1",
            ),
        )

        self.assertEqual(updated.agent_model_profile, "bedrock_qwen3_next_80b_a3b_ap_south_1")
        self.assertEqual(updated.bedrock_region, "ap-south-1")

    def test_scope_overrides_apply_to_settings_for_one_run(self) -> None:
        settings = DiracDataSettings(
            catalog="commerce_pod",
            database="analytics",
            schema="main",
            catalog_config=Path("conf/catalogs/commerce_pod.minio.json"),
        )

        updated = _settings_with_agent_overrides(
            settings,
            Namespace(
                catalog="retail_pod",
                database="analytics",
                schema="retail_analytics",
                catalog_config=Path("conf/catalogs/retail_analytics.minio.json"),
                agent_model_profile=None,
                agent_llm_provider=None,
                agent_model=None,
                agent_max_tokens=None,
                agent_temperature=None,
                bedrock_region=None,
            ),
        )

        self.assertEqual(updated.catalog, "retail_pod")
        self.assertEqual(updated.database, "analytics")
        self.assertEqual(updated.schema, "retail_analytics")
        self.assertEqual(updated.catalog_config, Path("conf/catalogs/retail_analytics.minio.json"))

    def test_cli_preflight_fails_cleanly_when_active_artifacts_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "DIRACDATA_MODE=dev",
                        "DIRACDATA_QUERY_ENGINE=duckdb",
                        "DIRACDATA_SQL_DIALECT=duckdb",
                        "DIRACDATA_CATALOG=commerce_pod",
                        "DIRACDATA_DATABASE=analytics",
                        "DIRACDATA_SCHEMA=main",
                        "DIRACDATA_OBJECT_STORE=local",
                        f"DIRACDATA_LOCAL_ARTIFACT_ROOT={Path(tmpdir) / 'artifacts'}",
                    ]
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_data_analyst_agent_uat.py",
                    "--env-file",
                    str(env_path),
                    "--question",
                    "count all male customers from california",
                    "--no-interactive",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("active learning artifacts are missing", result.stdout)

    def test_live_cli_answers_tpcds_question(self) -> None:
        if os.environ.get("DIRACDATA_RUN_LIVE_AGENT_UAT") != "1":
            self.skipTest("Set DIRACDATA_RUN_LIVE_AGENT_UAT=1 to run live agent UAT")

        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "agent_trace.jsonl"
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_data_analyst_agent_uat.py",
                    "--question",
                    "count all male customers from california",
                    "--stream",
                    "--stream-modes",
                    "updates,messages",
                    "--no-interactive",
                    "--trace-jsonl",
                    str(trace_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=240,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("UAT Summary", result.stdout)
        self.assertTrue(trace_path.exists())


if __name__ == "__main__":
    unittest.main()
