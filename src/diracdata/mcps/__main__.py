"""CLI entry for the DiracData context MCP (console script: `diracdata-mcp`, or `python -m diracdata.mcps`).

    diracdata-mcp --schema sales --data ~/warehouse.duckdb --model deepseek-v4-flash
    diracdata-mcp --schema sales --transport streamable-http --port 8765

stdio is the default (Cursor / Claude Desktop). Streamable HTTP is for ChatGPT /
Claude.ai / Gemini connectors; put HTTPS in front of the listener.
"""

from __future__ import annotations

import argparse
import sys

from diracdata.mcps.transport import add_transport_args, describe_transport, run_mcp_server


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="DiracData context MCP server (stdio or Streamable HTTP).")
    ap.add_argument("--schema", default=None, help="default schema for the tools")
    ap.add_argument("--data", default=None, help="local source: a .duckdb file, or a dir / .parquet|.csv")
    ap.add_argument("--store", default="local", choices=["local", "s3"], help="where learned context lives")
    ap.add_argument("--context-dir", default=None, help="local context dir (default ~/.diracdata)")
    ap.add_argument("--model", default=None, help="LLM for learn_schema (profile id, e.g. deepseek-v4-flash)")
    ap.add_argument("--env-file", default=None, help="load config/credentials from this .env")
    add_transport_args(ap)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from diracdata.mcps import context_mcp
    server = context_mcp(schema=args.schema, data=args.data, store=args.store, model=args.model,
                         context_dir=args.context_dir, env_file=args.env_file)
    print(f"[diracdata-mcp] serving schema={args.schema or 'default'} store={args.store} "
          f"source={args.data or 's3-lake'}", file=sys.stderr)
    print(
        describe_transport(
            name="diracdata-mcp",
            transport=args.transport,
            host=args.host,
            port=args.port,
            mcp_path=args.mcp_path,
        ),
        file=sys.stderr,
    )
    run_mcp_server(
        server,
        transport=args.transport,
        host=args.host,
        port=args.port,
        mcp_path=args.mcp_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
