import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from diracdata_v2.agent import (
    load_nl_ast_middleware_prompt,
    load_sql_authoring_middleware_prompt,
    load_sql_validation_middleware_prompt,
    load_system_prompt,
    load_todo_planning_prompt,
    load_todo_planning_tool_description,
)
from diracdata_v2.settings import settings_from_env
from diracdata_v2.storage import LocalObjectStore, object_store_from_settings


class DataAgentV2Tests(unittest.TestCase):
    def test_prompt_loads_generic_value_grounding_rules(self) -> None:
        prompt = load_system_prompt()

        self.assertIn("Before using any literal value inferred from user language", prompt)
        self.assertIn("column_values", prompt)
        self.assertIn("Do not guess casing", prompt)
        self.assertNotIn("mobile SDK", prompt)
        self.assertNotIn("low-risk", prompt)
        self.assertNotIn("verified users", prompt)

    def test_todo_planning_prompt_is_generic_sql_planning(self) -> None:
        prompt = load_todo_planning_prompt()

        self.assertIn("write_todos", prompt)
        self.assertIn("CTEs", prompt)
        self.assertIn("row-count probes", prompt)
        self.assertIn("source scope", prompt)
        self.assertIn("schema-agnostic", prompt)
        self.assertNotIn("fintech", prompt.lower())
        self.assertNotIn("retail", prompt.lower())
        self.assertNotIn("jewelry", prompt.lower())
        self.assertNotIn("payment", prompt.lower())

    def test_todo_tool_description_is_generic_sql_planning(self) -> None:
        description = load_todo_planning_tool_description()

        self.assertIn("complex analytics question", description)
        self.assertIn("source-scope", description)
        self.assertIn("row counts", description)
        self.assertNotIn("fintech", description.lower())
        self.assertNotIn("retail", description.lower())
        self.assertNotIn("jewelry", description.lower())
        self.assertNotIn("payment", description.lower())

    def test_stage_prompts_are_generic(self) -> None:
        prompts = [
            load_nl_ast_middleware_prompt(),
            load_sql_authoring_middleware_prompt(),
            load_sql_validation_middleware_prompt(),
        ]

        for prompt in prompts:
            lowered = prompt.lower()
            self.assertNotIn("fintech", lowered)
            self.assertNotIn("retail", lowered)
            self.assertNotIn("jewelry", lowered)
            self.assertNotIn("maine", lowered)
            self.assertNotIn("payment rail", lowered)

    def test_todo_planning_enabled_defaults_to_true_and_can_be_disabled(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(settings_from_env(env_file=None).agent_todo_planning_enabled)

        with patch.dict(os.environ, {"DIRACDATA_AGENT_TODO_PLANNING_ENABLED": "false"}, clear=True):
            self.assertFalse(settings_from_env(env_file=None).agent_todo_planning_enabled)

    def test_primitive_workflow_mode_defaults_to_gated_and_can_use_supervisor(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(settings_from_env(env_file=None).primitive_workflow_mode, "gated")

        with patch.dict(os.environ, {"DIRACDATA_PRIMITIVE_WORKFLOW_MODE": "supervisor"}, clear=True):
            self.assertEqual(settings_from_env(env_file=None).primitive_workflow_mode, "supervisor")

    def test_object_store_defaults_to_local_and_uses_v2_settings(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "DIRACDATA_OBJECT_STORE": "local",
                    "DIRACDATA_LOCAL_ARTIFACT_ROOT": tmp,
                },
                clear=True,
            ):
                settings = settings_from_env(env_file=None)
                store = object_store_from_settings(settings)

            self.assertIsInstance(store, LocalObjectStore)
            store.write_json("runs/example.json", {"ok": True})
            self.assertEqual(
                Path(tmp, "runs", "example.json").read_text(encoding="utf-8").strip(),
                '{\n  "ok": true\n}',
            )


if __name__ == "__main__":
    unittest.main()
