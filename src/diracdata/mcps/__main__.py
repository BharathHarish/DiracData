"""CLI entry for the DiracData context MCP (console script: `diracdata-mcp`, or `python -m diracdata.mcps`).

    diracdata-mcp --schema sales --data ~/warehouse.duckdb --model deepseek-v4-flash

Transport is stdio (for Claude Desktop / local MCP clients). Config not passed on the CLI is read from
the environment (or --env-file), so credentials can live in the MCP client's env block.
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="DiracData context MCP server (stdio).")
    ap.add_argument("--schema", default=None, help="default schema for the tools")
    ap.add_argument("--data", default=None, help="local source: a .duckdb file, or a dir / .parquet|.csv")
    ap.add_argument("--store", default="local", choices=["local", "s3"], help="where learned context lives")
    ap.add_argument("--context-dir", default=None, help="local context dir (default ~/.diracdata)")
    ap.add_argument("--model", default=None, help="LLM for learn_schema (profile id, e.g. deepseek-v4-flash)")
    ap.add_argument("--env-file", default=None, help="load config/credentials from this .env")
    args = ap.parse_args()

    from diracdata.mcps import context_mcp
    server = context_mcp(schema=args.schema, data=args.data, store=args.store, model=args.model,
                         context_dir=args.context_dir, env_file=args.env_file)
    print(f"[diracdata-mcp] serving schema={args.schema or 'default'} store={args.store} "
          f"source={args.data or 's3-lake'} — stdio", file=sys.stderr)
    server.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
