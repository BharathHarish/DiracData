import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from diracdata_v2.agent import create_nl_ast_agent, load_nl_ast_agent_system_prompt
from diracdata_v2.settings import V2Settings


class NLASTAgentTests(unittest.TestCase):
    def test_prompt_is_generic_and_requires_compact_retrieval_flow(self) -> None:
        prompt = load_nl_ast_agent_system_prompt()
        lowered = prompt.lower()

        self.assertIn("pattern_search_tool", prompt)
        self.assertIn("candidate_search_tool", prompt)
        self.assertIn("get_table_columns", prompt)
        self.assertIn("compact semantic plan", prompt)
        self.assertIn("unresolved", lowered)
        self.assertIn("ambiguous", lowered)
        self.assertNotIn("keyword_search_schema", prompt)
        self.assertNotIn("retail", lowered)
        self.assertNotIn("jewelry", lowered)
        self.assertNotIn("maine", lowered)
        self.assertNotIn("payment rail", lowered)

    def test_agent_constructs_with_retrieval_and_schema_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            table_doc = root / "table_descriptions.md"
            column_doc = root / "table_column_descriptions.md"
            table_doc.write_text("# tables\n", encoding="utf-8")
            column_doc.write_text("# columns\n", encoding="utf-8")
            with (
                patch("diracdata_v2.agent.NL_AST_agent.agent_chat_model_from_settings") as model_factory,
                patch("langchain.agents.create_agent") as create_agent,
            ):
                model_factory.return_value = object()
                create_agent.return_value = object()

                runtime = create_nl_ast_agent(
                    settings=V2Settings(anthropic_api_key="test-key"),
                    table_descriptions_path=table_doc,
                    table_column_descriptions_path=column_doc,
                )

        self.assertIsNotNone(runtime.graph)
        tool_names = {tool.name for tool in create_agent.call_args.kwargs["tools"]}
        self.assertEqual(
            tool_names,
            {
                "pattern_search_tool",
                "candidate_search_tool",
                "get_tables",
                "get_table_columns",
                "get_column_description",
            },
        )


if __name__ == "__main__":
    unittest.main()
