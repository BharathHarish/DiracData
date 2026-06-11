import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from diracdata_v2.agent.middleware import NLASTMiddleware, SQLValidationMiddleware


class AgentMiddlewareTests(unittest.TestCase):
    def test_nl_ast_middleware_blocks_complex_tool_use_before_nl_ast(self) -> None:
        middleware = NLASTMiddleware(prompt="prompt")
        request = ToolCallRequest(
            tool_call={"name": "schema_search_ast", "args": {"query": "x"}, "id": "call-1"},
            tool=None,
            state={
                "messages": [
                    HumanMessage(
                        content=(
                            "How many customers bought at least 1 product and at most 3 other "
                            "products, and which locations served them most?"
                        )
                    )
                ]
            },
            runtime=None,
        )

        response = middleware.wrap_tool_call(request, lambda _: self.fail("tool should be blocked"))

        self.assertIsInstance(response, ToolMessage)
        self.assertEqual(response.status, "error")
        self.assertIn("NL AST required", response.content)

    def test_validation_middleware_allows_probe_sql(self) -> None:
        middleware = SQLValidationMiddleware(prompt="prompt")
        request = ToolCallRequest(
            tool_call={
                "name": "execute_sql",
                "args": {"sql": "-- probe: count base rows\nSELECT COUNT(*) FROM orders"},
                "id": "call-2",
            },
            tool=None,
            state={
                "messages": [
                    HumanMessage(
                        content="Compare active customers by segment and show the top regions."
                    ),
                    AIMessage(content="NL_AST\n{}"),
                ]
            },
            runtime=None,
        )

        response = middleware.wrap_tool_call(
            request,
            lambda _: ToolMessage(content="ok", tool_call_id="call-2"),
        )

        self.assertEqual(response.status, "success")
        self.assertEqual(response.content, "ok")

    def test_validation_middleware_blocks_final_sql_before_validation_pass(self) -> None:
        middleware = SQLValidationMiddleware(prompt="prompt")
        request = ToolCallRequest(
            tool_call={
                "name": "execute_sql",
                "args": {"sql": "-- final: answer query\nSELECT COUNT(*) FROM orders"},
                "id": "call-3",
            },
            tool=None,
            state={
                "messages": [
                    HumanMessage(
                        content="Compare active customers by segment and show the top regions."
                    ),
                    AIMessage(content="NL_AST\n{}"),
                ]
            },
            runtime=None,
        )

        response = middleware.wrap_tool_call(request, lambda _: self.fail("tool should be blocked"))

        self.assertEqual(response.status, "error")
        self.assertIn("SQL validation required", response.content)

    def test_validation_middleware_allows_final_sql_after_validation_pass(self) -> None:
        middleware = SQLValidationMiddleware(prompt="prompt")
        request = ToolCallRequest(
            tool_call={
                "name": "execute_sql",
                "args": {"sql": "-- final: answer query\nSELECT COUNT(*) FROM orders"},
                "id": "call-4",
            },
            tool=None,
            state={
                "messages": [
                    HumanMessage(
                        content="Compare active customers by segment and show the top regions."
                    ),
                    AIMessage(content="NL_AST\n{}"),
                    AIMessage(content="SQL_VALIDATION: PASS"),
                ]
            },
            runtime=None,
        )

        response = middleware.wrap_tool_call(
            request,
            lambda _: ToolMessage(content="ok", tool_call_id="call-4"),
        )

        self.assertEqual(response.status, "success")
        self.assertEqual(response.content, "ok")


if __name__ == "__main__":
    unittest.main()
