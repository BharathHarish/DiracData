"""The tool registry.

`build_tools` returns the data + interaction tools that BOTH framing and the analyst share
(navigation/retrieval, define, find_examples, run_sql/query_result, ask_user). `build_control_tools`
adds the gate-dependent action tools (plan_update, finish) for the analyst only. Grouped by concern:
navigation.py (know the data), query.py (run/slice results), control.py (plan + gated finish).
"""

from __future__ import annotations

from typing import Any

from diracdata.config import Config
from diracdata.tools.control import build_control_tools, build_transcript_tool
from diracdata.tools.navigation import build_navigation_tools
from diracdata.tools.query import build_query_tools

__all__ = ["build_tools", "build_control_tools", "build_transcript_tool",
           "build_navigation_tools", "build_query_tools"]

_DEFAULTS = Config()


def _no_asker(question: str) -> str:
    return ""  # headless default: no user available -> the agent proceeds on a stated assumption


def build_tools(*, workspace: Any, engine: Any, result_store: Any, memory: Any,
                value_cache: Any = None, asker: Any = None, sources: Any = None,
                max_rows: int = _DEFAULTS.query_max_rows) -> list[Any]:
    from langchain.tools import tool

    ask = asker or _no_asker
    # tiered navigation + define/find_examples/join_path/data_check; drop its row-dumping run_sql
    nav = [t for t in build_navigation_tools(workspace=workspace, engine=engine,
                                             value_cache=value_cache, sources=sources)
           if t.name != "run_sql"]
    query = build_query_tools(engine=engine, result_store=result_store, memory=memory,
                              sources=sources, max_rows=max_rows)

    @tool("ask_user")
    def ask_user(question: str) -> str:
        """Ask the USER exactly ONE short, plain-language disambiguation question -- ONLY when two
        reasonable readings of the request would give MATERIALLY DIFFERENT numbers and nothing in the
        data settles it (e.g. 'retail stores only' = bought in a store, vs bought ONLY in stores). Ask
        about MEANING, never tables/columns/SQL. Returns the user's answer (empty if none available --
        then proceed on your most reasonable reading and say so)."""
        answer = ask(question) or ""
        memory.record_clarification(question, answer or "(no answer -- proceed on best reading)")
        return answer or "(no answer available -- proceed on your most reasonable reading and state it)"

    return nav + query + [ask_user]
