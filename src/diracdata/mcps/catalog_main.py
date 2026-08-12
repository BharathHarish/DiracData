"""CLI entry for dirac-catalog-mcp — catalog-aware MCP server (stdio).

Register in ~/.cursor/mcp.json:
    {
      "mcpServers": {
        "dirac-catalog-spider2": {
          "command": ".../.venv/bin/dirac-catalog-mcp",
          "args": ["--catalog", "spider2_local", "--env-file", ".../.env"]
        }
      }
    }
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="DiracData catalog-aware MCP server (stdio).")
    ap.add_argument("--catalog",  required=True, help="catalog name (e.g. 'local', 'spider2_local')")
    ap.add_argument("--env-file", default=None,   help="load config/credentials from this .env")
    ap.add_argument("--model",    default=None,   help="LLM for refresh_*_md (profile id)")
    args = ap.parse_args()

    from diracdata.mcps.catalog_server import catalog_mcp
    server = catalog_mcp(catalog=args.catalog, env_file=args.env_file, model=args.model)
    print(f"[dirac-catalog-mcp] serving catalog={args.catalog} — stdio", file=sys.stderr)
    server.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
