"""CLI entry for dirac-catalog-mcp — catalog-aware MCP server.

stdio (default) for Cursor / Claude Desktop:

    dirac-catalog-mcp --catalog local --env-file .env

Streamable HTTP for ChatGPT / Claude.ai / Gemini (then put HTTPS in front):

    dirac-catalog-mcp --catalog local --env-file .env --transport streamable-http --port 8765
    # connector URL: http://127.0.0.1:8765/mcp
"""
from __future__ import annotations

import argparse
import sys

from diracdata.mcps.transport import add_transport_args, describe_transport, run_mcp_server


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="DiracData catalog-aware MCP server (stdio or Streamable HTTP).",
    )
    ap.add_argument("--catalog",  required=True, help="catalog name (e.g. 'local', 'spider2_local')")
    ap.add_argument("--env-file", default=None,   help="load config/credentials from this .env")
    ap.add_argument("--model",    default=None,   help="LLM for refresh_*_md (profile id)")
    add_transport_args(ap)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from diracdata.mcps.catalog_server import catalog_mcp
    print(
        f"[dirac-catalog-mcp] serving catalog={args.catalog} "
        f"(harness: search_fabric/search_schema/profile/sql_diff)",
        file=sys.stderr,
    )
    print(
        describe_transport(
            name="dirac-catalog-mcp",
            transport=args.transport,
            host=args.host,
            port=args.port,
            mcp_path=args.mcp_path,
        ),
        file=sys.stderr,
    )
    server = catalog_mcp(catalog=args.catalog, env_file=args.env_file, model=args.model)
    run_mcp_server(
        server,
        transport=args.transport,
        host=args.host,
        port=args.port,
        mcp_path=args.mcp_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
