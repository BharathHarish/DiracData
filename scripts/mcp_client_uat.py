#!/usr/bin/env python3
"""A REAL MCP client + LLM driver — the same shape as Cursor/Claude Desktop, but driven here so we
can prove the MCP end-to-end ourselves:

  1. spawn `diracdata-mcp` as a stdio SUBPROCESS (the real server, not in-process),
  2. connect an MCP ClientSession over stdio, list the tools,
  3. bind those tools to a Fireworks LLM and run a tool-calling loop until the model answers,
  4. print the transcript (every tool call + result) and the final answer.

Run:  PYTHONPATH=src .venv/bin/python scripts/mcp_client_uat.py --schema retail_complex \
        --question "..." --model fireworks_deepseek_v4_flash
"""
from __future__ import annotations
import argparse, asyncio, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mcp import ClientSession, StdioServerParameters      # noqa: E402
from mcp.client.stdio import stdio_client                 # noqa: E402


def _to_openai_tools(mcp_tools) -> list:
    """Convert MCP tool defs -> OpenAI/Fireworks function-tool schema."""
    out = []
    for t in mcp_tools:
        out.append({"type": "function", "function": {
            "name": t.name,
            "description": (t.description or "")[:1024],
            "parameters": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
                          or {"type": "object", "properties": {}},
        }})
    return out


async def run(schema: str, question: str, model_profile: str, env_file: str, max_iters: int) -> None:
    from diracdata.config import settings_from_env
    from diracdata.models import chat_model
    s = settings_from_env(env_file)
    # Raw Fireworks client via the project's model factory, but we need tool-calling; use openai SDK
    # directly against Fireworks so we control the loop exactly like an MCP host would.
    from openai import OpenAI
    fw = OpenAI(base_url="https://api.fireworks.ai/inference/v1",
                api_key=os.environ.get("FIREWORKS_API_KEY") or s.fireworks_api_key)
    from diracdata.models.factory import BUILT_IN_MODEL_PROFILES
    prof = BUILT_IN_MODEL_PROFILES.get(model_profile)
    model_id = prof.model if prof else model_profile
    print(f"[client] model_id={model_id}", file=sys.stderr, flush=True)

    server = StdioServerParameters(
        command=str(ROOT / ".venv" / "bin" / "diracdata-mcp"),
        args=["--schema", schema, "--store", "s3", "--env-file", env_file, "--model", model_profile],
        env=os.environ.copy(),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            tools = listed.tools
            print(f"[client] connected — {len(tools)} tools: {[t.name for t in tools]}", file=sys.stderr, flush=True)
            oai_tools = _to_openai_tools(tools)

            sys_prompt = (
                "You are a data analyst. Answer the user's question about the '" + schema + "' database "
                "by calling the provided tools (schema-aware context + a DuckDB engine). "
                "ALWAYS call find_examples first, then describe/join tools to ground yourself, then run_sql. "
                "Verify multi-table SQL with data_check. When you have the answer, state it with the numbers."
            )
            messages = [{"role": "system", "content": sys_prompt},
                        {"role": "user", "content": question}]

            for step in range(max_iters):
                resp = fw.chat.completions.create(model=model_id, messages=messages,
                                                  tools=oai_tools, tool_choice="auto", temperature=0)
                msg = resp.choices[0].message
                if not msg.tool_calls:
                    print("\n=== FINAL ANSWER ===\n" + (msg.content or ""), flush=True)
                    return
                messages.append({"role": "assistant", "content": msg.content or "",
                                 "tool_calls": [{"id": tc.id, "type": "function",
                                                 "function": {"name": tc.function.name,
                                                              "arguments": tc.function.arguments}}
                                                for tc in msg.tool_calls]})
                for tc in msg.tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except Exception:
                        args = {}
                    print(f"  >> {name}({json.dumps(args)[:160]})", file=sys.stderr, flush=True)
                    result = await session.call_tool(name, args)
                    text = "".join(getattr(c, "text", "") for c in result.content)[:6000]
                    print(f"     << {text[:200].strip()}", file=sys.stderr, flush=True)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
            print("\n=== STOPPED (max_iters) ===", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--question", required=True)
    ap.add_argument("--model", default="fireworks_deepseek_v4_flash")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--max-iters", type=int, default=15)
    args = ap.parse_args()
    asyncio.run(run(args.schema, args.question, args.model, args.env_file, args.max_iters))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
