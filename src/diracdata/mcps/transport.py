"""Shared MCP transport for stdio (Cursor / Claude Desktop) and Streamable HTTP.

ChatGPT, Claude.ai custom connectors, and Gemini remote MCP speak Streamable HTTP
(typically POST /mcp). HTTPS is terminated in front of that listener (tunnel or
reverse proxy) — this module binds HTTP, defaulting to loopback.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

TRANSPORTS = ("stdio", "streamable-http", "sse")
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})


def add_transport_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=TRANSPORTS,
        help="stdio (default: Cursor / Claude Desktop) | streamable-http (ChatGPT / Claude.ai / Gemini) | sse",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address for HTTP transports (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765,
                        help="bind port for HTTP transports (default 8765)")
    parser.add_argument("--mcp-path", default="/mcp",
                        help="Streamable HTTP mount path (default /mcp). SSE uses /sse.")


def endpoint_url(*, transport: str, host: str, port: int, mcp_path: str) -> str:
    if transport == "stdio":
        return "stdio"
    path = mcp_path if transport == "streamable-http" else "/sse"
    if not path.startswith("/"):
        path = "/" + path
    return f"http://{host}:{port}{path}"


def describe_transport(*, name: str, transport: str, host: str, port: int, mcp_path: str) -> str:
    if transport == "stdio":
        return f"[{name}] transport=stdio"
    url = endpoint_url(transport=transport, host=host, port=port, mcp_path=mcp_path)
    extra = ""
    if host not in LOOPBACK:
        extra = (" WARNING: not loopback — this process can run SQL. "
                 "Put TLS + auth in front; do not expose a public unauthenticated URL.")
    return (
        f"[{name}] transport={transport} {url}{extra}\n"
        f"  Cursor/Claude Desktop: keep stdio.\n"
        f"  ChatGPT / Claude.ai / Gemini: connector URL = this path; wrap with HTTPS "
        f"(OpenAI Secure MCP Tunnel, Cloudflare Tunnel, or a reverse proxy)."
    )


def run_mcp_server(
    server: Any,
    *,
    transport: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    mcp_path: str = "/mcp",
) -> None:
    """Block on the MCP server. stdio uses stdin/stdout; HTTP transports use uvicorn."""
    if transport not in TRANSPORTS:
        raise ValueError(f"unknown transport {transport!r}")
    if transport == "stdio":
        server.run("stdio")
        return
    kwargs: dict[str, Any] = {"host": host, "port": port}
    if transport == "streamable-http":
        kwargs["streamable_http_path"] = mcp_path if mcp_path.startswith("/") else f"/{mcp_path}"
    server.run(transport, **kwargs)
