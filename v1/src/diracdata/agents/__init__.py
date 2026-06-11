"""Agent entrypoints.

The package keeps the heavy LangGraph/tool runtime import lazy so learning and
grounding modules can use lightweight artifact repositories without creating
agent-runtime circular imports.
"""

__all__ = [
    "AnalystCompilerRuntime",
    "DataAnalystAgentRuntime",
    "create_analyst_compiler",
    "create_data_analyst_agent",
]


def __getattr__(name: str) -> object:
    if name in {"DataAnalystAgentRuntime", "create_data_analyst_agent"}:
        from diracdata.agents.data_analyst_agent import (
            DataAnalystAgentRuntime,
            create_data_analyst_agent,
        )

        values = {
            "DataAnalystAgentRuntime": DataAnalystAgentRuntime,
            "create_data_analyst_agent": create_data_analyst_agent,
        }
        return values[name]
    if name in {"AnalystCompilerRuntime", "create_analyst_compiler"}:
        from diracdata.agents.analyst_compiler import (
            AnalystCompilerRuntime,
            create_analyst_compiler,
        )

        values = {
            "AnalystCompilerRuntime": AnalystCompilerRuntime,
            "create_analyst_compiler": create_analyst_compiler,
        }
        return values[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
