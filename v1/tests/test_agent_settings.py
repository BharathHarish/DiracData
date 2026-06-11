from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.agents.settings import (
    AgentRuntimeSettings,
    AgentStreaming,
    LangGraphStreamMode,
    parse_stream_modes,
)
from diracdata.config.settings import DiracDataSettings


class AgentSettingsTest(unittest.TestCase):
    def test_parse_stream_modes_accepts_langgraph_modes(self) -> None:
        modes = parse_stream_modes("updates,messages,custom,updates")

        self.assertEqual(
            modes,
            [
                LangGraphStreamMode.UPDATES,
                LangGraphStreamMode.MESSAGES,
                LangGraphStreamMode.CUSTOM,
            ],
        )

    def test_parse_stream_modes_rejects_unknown_modes(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported LangGraph stream mode"):
            parse_stream_modes("messages,not_a_mode")

    def test_agent_runtime_settings_resolve_from_dirac_settings(self) -> None:
        settings = DiracDataSettings(
            agent_streaming="on",
            agent_stream_modes="values,updates",
            agent_stream_version="v2",
            agent_checkpointer="memory",
            agent_store="memory",
            agent_schema_search_limit=7,
            agent_profile_values_limit=8,
            agent_sql_max_rows=9,
            agent_sql_timeout_seconds=10,
        )

        runtime_settings = AgentRuntimeSettings.from_settings(settings)

        self.assertEqual(runtime_settings.streaming, AgentStreaming.ON)
        self.assertEqual(
            runtime_settings.stream_modes,
            [LangGraphStreamMode.VALUES, LangGraphStreamMode.UPDATES],
        )
        self.assertEqual(runtime_settings.sql_max_rows, 9)


if __name__ == "__main__":
    unittest.main()
