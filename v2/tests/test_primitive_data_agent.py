import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import AIMessage

from diracdata_v2.agent import (
    create_primitive_data_agent,
    load_primitive_analyst_prompt,
    load_primitive_data_engineering_prompt,
    load_primitive_data_steward_prompt,
    load_primitive_intent_prompt,
    load_primitive_outer_prompt,
    load_primitive_supervisor_prompt,
    load_primitive_sql_author_prompt,
    load_primitive_sql_validator_prompt,
)
from diracdata_v2.settings import V2Settings


class FakeFinalModel:
    def bind_tools(self, tools):
        self.tool_names = [tool.name for tool in tools]
        return self

    def invoke(self, messages):
        return AIMessage(content="done")


class FakeEngine:
    def list_tables(self):
        return []

    def query(self, sql, max_rows):
        raise AssertionError("query should not run in construction test")


class PrimitiveDataAgentTests(unittest.TestCase):
    def test_primitive_data_agent_constructs_outer_and_subagents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = root / "metadata.json"
            metadata.write_text(json.dumps({"tables": {}, "columns": {}}), encoding="utf-8")
            sql_library = root / "sql_library.json"
            sql_library.write_text(json.dumps({"entries": {}, "patterns": {}}), encoding="utf-8")
            schema_ast = root / "schema_ast.json"
            schema_ast.write_text(json.dumps({"domains": []}), encoding="utf-8")
            settings = V2Settings(
                anthropic_api_key="test",
                metadata_descriptions_path=metadata,
                sql_library_path=sql_library,
                schema_ast_path=schema_ast,
            )

            runtime = create_primitive_data_agent(
                settings=settings,
                model=FakeFinalModel(),
                engine=FakeEngine(),
            )

        self.assertEqual(
            set(runtime.outer_agent._tools_by_name),
            {"analyst_subagent", "data_steward_subagent", "data_engineer_subagent"},
        )
        self.assertEqual(
            set(runtime.supervisor_agent._tools_by_name),
            {
                "intent_subagent",
                "sql_author_subagent",
                "data_steward_subagent",
                "data_engineer_subagent",
                "execute_sql",
            },
        )
        self.assertEqual(
            set(runtime.subagents),
            {
                "intent_subagent",
                "sql_author_subagent",
                "analyst_subagent",
                "data_steward_subagent",
                "data_engineer_subagent",
            },
        )
        self.assertIn("candidate_search_tool", runtime.subagents["intent_subagent"]._tools_by_name)
        self.assertNotIn("sql_dry_run", runtime.subagents["intent_subagent"]._tools_by_name)
        self.assertNotIn("execute_sql", runtime.subagents["intent_subagent"]._tools_by_name)
        self.assertIn("sql_dry_run", runtime.subagents["sql_author_subagent"]._tools_by_name)
        self.assertIn("column_values", runtime.subagents["sql_author_subagent"]._tools_by_name)
        self.assertNotIn("execute_sql", runtime.subagents["sql_author_subagent"]._tools_by_name)
        self.assertIn("sql_dry_run", runtime.subagents["data_steward_subagent"]._tools_by_name)
        self.assertNotIn("execute_sql", runtime.subagents["data_steward_subagent"]._tools_by_name)

    def test_primitive_prompts_are_generic(self) -> None:
        combined = "\n".join(
            [
                load_primitive_outer_prompt(),
                load_primitive_intent_prompt(),
                load_primitive_analyst_prompt(),
                load_primitive_data_steward_prompt(),
                load_primitive_data_engineering_prompt(),
                load_primitive_supervisor_prompt(),
                load_primitive_sql_author_prompt(),
                load_primitive_sql_validator_prompt(),
            ]
        ).lower()
        self.assertIn("subagent", combined)
        self.assertIn("probe", combined)
        self.assertIn("explain", combined)
        self.assertIn("analyst_status: ok", combined)
        self.assertIn("steward_status: pass", combined)
        self.assertIn("pass_with_assumptions", combined)
        self.assertIn("de_status: optimized", combined)
        self.assertIn("natural-language categorical values", combined)
        self.assertIn("select distinct", combined)
        self.assertIn("do not ask the user to confirm", combined)
        self.assertIn("value grounding", combined)
        self.assertIn("semantic unit test", combined)
        self.assertIn("exact `final_sql`", combined)
        self.assertIn("partial output as evidence", combined)
        self.assertIn("how i interpreted this", combined)
        self.assertIn("intent_status: ok", combined)
        self.assertIn("sql_author_status: ok", combined)
        self.assertIn("sql_dry_run", combined)
        self.assertIn("clause_bindings", combined)
        self.assertIn("approved intent packet is the executable contract", combined)
        self.assertIn("mcq_options", combined)
        self.assertIn("not exists", combined)
        self.assertIn("predicate pushdown", combined)
        self.assertIn("steward fails", combined)
        self.assertIn("call data engineering", combined)
        self.assertNotIn("retail", combined)
        self.assertNotIn("arizona", combined)
        self.assertNotIn("electronics", combined)


if __name__ == "__main__":
    unittest.main()
