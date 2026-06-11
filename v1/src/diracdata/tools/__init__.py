"""Product-facing LangChain tools for DiracData agents.

Tool package exports are lazy so individual modules can be imported by learning
and grounding code without pulling in the full agent tool factory.
"""

__all__ = [
    "build_data_analyst_tools",
    "build_sql_tools",
    "validate_read_only_sql",
]


def __getattr__(name: str) -> object:
    if name == "build_data_analyst_tools":
        from diracdata.tools.factory import build_data_analyst_tools

        return build_data_analyst_tools
    if name in {"build_sql_tools", "validate_read_only_sql"}:
        from diracdata.tools.sql_tools import build_sql_tools, validate_read_only_sql

        values = {
            "build_sql_tools": build_sql_tools,
            "validate_read_only_sql": validate_read_only_sql,
        }
        return values[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
