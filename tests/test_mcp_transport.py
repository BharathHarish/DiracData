"""Streamable HTTP transport for remote MCP clients (ChatGPT / Claude.ai / Gemini)."""
from __future__ import annotations

import asyncio
import socket
import threading
import time
from typing import Any

import pytest

from diracdata.mcps.catalog_main import build_parser as catalog_parser
from diracdata.mcps.transport import (
    LOOPBACK,
    describe_transport,
    endpoint_url,
    run_mcp_server,
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_catalog_cli_defaults_to_stdio():
    args = catalog_parser().parse_args(["--catalog", "local"])
    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8765
    assert args.mcp_path == "/mcp"


def test_catalog_cli_streamable_http_flags():
    args = catalog_parser().parse_args([
        "--catalog", "local",
        "--transport", "streamable-http",
        "--host", "127.0.0.1",
        "--port", "9001",
        "--mcp-path", "/mcp",
    ])
    assert args.transport == "streamable-http"
    assert endpoint_url(transport=args.transport, host=args.host, port=args.port,
                        mcp_path=args.mcp_path) == "http://127.0.0.1:9001/mcp"


def test_describe_transport_warns_off_loopback():
    text = describe_transport(
        name="probe", transport="streamable-http", host="0.0.0.0", port=8765, mcp_path="/mcp",
    )
    assert "WARNING" in text
    assert "0.0.0.0" not in LOOPBACK


def test_run_stdio_delegates_to_server_run():
    class _Fake:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def run(self, transport: str, **kwargs: Any) -> None:
            self.calls.append((transport, kwargs))

    fake = _Fake()
    run_mcp_server(fake, transport="stdio")
    assert fake.calls == [("stdio", {})]


def test_streamable_http_initialize_and_list_tools():
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError:
        pytest.skip("mcp SDK not installed")

    port = _free_port()
    server = MCPServer(name="dirac-http-probe")

    @server.tool()
    def ping() -> str:
        """Health probe."""
        return "pong"

    t = threading.Thread(
        target=lambda: run_mcp_server(
            server, transport="streamable-http", host="127.0.0.1", port=port, mcp_path="/mcp",
        ),
        daemon=True,
    )
    t.start()

    url = f"http://127.0.0.1:{port}/mcp"

    async def _talk() -> list[str]:
        deadline = time.time() + 15
        last: Exception | None = None
        while time.time() < deadline:
            try:
                async with streamable_http_client(url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        listed = await session.list_tools()
                        return [tool.name for tool in listed.tools]
            except Exception as exc:  # server still binding
                last = exc
                await asyncio.sleep(0.15)
        raise AssertionError(f"streamable-http never became ready: {last}")

    names = asyncio.run(_talk())
    assert "ping" in names
