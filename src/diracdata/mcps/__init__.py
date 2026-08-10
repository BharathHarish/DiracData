"""diracdata.mcps -- MCP servers exposing DiracData to any client (Claude / ChatGPT / Gemini).

    from diracdata.mcps import context_mcp
    context_mcp(schema="sales", data="~/warehouse.duckdb").run("stdio")

`context_mcp` serves a schema's governed context (provider tools) + guarded SQL + the learning-agent
builder. Context defaults to LOCAL disk (~/.diracdata); pass store="s3" for shared/team context.
"""

from diracdata.mcps.server import context_mcp

__all__ = ["context_mcp"]
