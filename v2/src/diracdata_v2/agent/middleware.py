"""Middleware stages for the lean v2 SQL analyst agent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest


@dataclass
class NLASTMiddleware(AgentMiddleware):
    """Require a visible NL AST before complex analytics tool use."""

    prompt: str
    tools = ()

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        if _is_complex_state(request.state) and not _has_marker(request.state, "NL_AST"):
            return handler(_append_system_prompt(request, self.prompt))
        return handler(request)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage],
    ) -> ToolMessage:
        if (
            _is_complex_state(request.state)
            and not _has_marker(request.state, "NL_AST")
            and _tool_name(request) in {"schema_search_ast", "column_values", "execute_sql"}
        ):
            return ToolMessage(
                content=(
                    "NL AST required before this tool call for a complex analytics question. "
                    "Write a visible `NL_AST` JSON block that preserves the user's intent, "
                    "then retry the needed tool call."
                ),
                tool_call_id=_tool_id(request),
                status="error",
            )
        return handler(request)


@dataclass
class SQLAuthoringMiddleware(AgentMiddleware):
    """Inject SQL authoring discipline once an NL AST exists."""

    prompt: str
    tools = ()

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        if _is_complex_state(request.state) and _has_marker(request.state, "NL_AST"):
            return handler(_append_system_prompt(request, self.prompt))
        return handler(request)


@dataclass
class SQLValidationMiddleware(AgentMiddleware):
    """Require a visible semantic validation block before final SQL execution."""

    prompt: str
    tools = ()

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        if _is_complex_state(request.state) and _has_marker(request.state, "NL_AST"):
            return handler(_append_system_prompt(request, self.prompt))
        return handler(request)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage],
    ) -> ToolMessage:
        if _tool_name(request) != "execute_sql" or not _is_complex_state(request.state):
            return handler(request)

        sql = str((_tool_args(request) or {}).get("sql", ""))
        if _is_probe_sql(sql):
            return handler(request)

        if _is_final_sql(sql) and _has_sql_validation_pass(request.state):
            return handler(request)

        return ToolMessage(
            content=(
                "SQL validation required before executing this complex SQL. "
                "Use `-- probe:` for verification SQL. For final SQL, first write a visible "
                "`SQL_VALIDATION` block comparing the NL AST to the candidate SQL. "
                "Only after `SQL_VALIDATION: PASS`, call execute_sql with a leading `-- final:` comment."
            ),
            tool_call_id=_tool_id(request),
            status="error",
        )


def _append_system_prompt(request: ModelRequest, prompt: str) -> ModelRequest:
    base = request.system_message.text if request.system_message is not None else ""
    combined = f"{base}\n\n{prompt}" if base else prompt
    return request.override(system_message=SystemMessage(content=combined))


def _is_complex_state(state: Any) -> bool:
    return _is_complex_question(_last_user_text(state))


def _is_complex_question(text: str) -> bool:
    lowered = f" {text.lower()} "
    if not text.strip():
        return False
    hard_markers = [
        " at least ",
        " at most ",
        " between ",
        " which ",
        " split ",
        " compare ",
        " trend ",
        " cohort ",
        " active ",
        " retained ",
        " during ",
        " over ",
        " top ",
        " rank ",
    ]
    marker_count = sum(1 for marker in hard_markers if marker in lowered)
    conjunction_count = len(re.findall(r"\b(and|also|while|with)\b", lowered))
    question_count = text.count("?")
    word_count = len(re.findall(r"\w+", text))
    return marker_count >= 1 or conjunction_count >= 2 or question_count >= 2 or word_count >= 24


def _has_marker(state: Any, marker: str) -> bool:
    return marker.lower() in _all_message_text(state).lower()


def _has_sql_validation_pass(state: Any) -> bool:
    text = _all_message_text(state)
    return bool(re.search(r"SQL_VALIDATION\s*:?\s*PASS", text, flags=re.IGNORECASE))


def _is_probe_sql(sql: str) -> bool:
    return bool(re.match(r"^\s*--\s*probe\s*:", sql, flags=re.IGNORECASE))


def _is_final_sql(sql: str) -> bool:
    return bool(re.match(r"^\s*--\s*final\s*:", sql, flags=re.IGNORECASE))


def _last_user_text(state: Any) -> str:
    messages = _messages(state)
    for message in reversed(messages):
        role = _message_role(message)
        if role in {"human", "user"}:
            return _message_text(message)
    return ""


def _all_message_text(state: Any) -> str:
    return "\n".join(_message_text(message) for message in _messages(state))


def _messages(state: Any) -> list[Any]:
    if isinstance(state, dict):
        return list(state.get("messages", []) or [])
    return list(getattr(state, "messages", []) or [])


def _message_role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role") or message.get("type") or "")
    return str(getattr(message, "type", "") or getattr(message, "role", ""))


def _message_text(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text is not None:
                    parts.append(str(text))
        return "\n".join(parts)
    return str(content or "")


def _tool_name(request: ToolCallRequest) -> str:
    return str(request.tool_call.get("name", ""))


def _tool_args(request: ToolCallRequest) -> dict[str, Any]:
    args = request.tool_call.get("args", {})
    return args if isinstance(args, dict) else {}


def _tool_id(request: ToolCallRequest) -> str:
    return str(request.tool_call.get("id") or "unknown_tool_call")
