#!/usr/bin/env python3
"""Model-garden smoke: can a profile actually DRIVE A TOOL LOOP (not just stream)?

For one --profile it runs a minimal round-trip: bind a trivial `echo` tool, ask the model to call it,
then feed the tool result back and ask for a one-word answer. Reports reach / tool_call / final_answer
+ latency. Run ONE profile per process and wrap it with an OS-level timeout so a hung model (e.g. an
unresponsive Bedrock endpoint) is reported as a failure instead of hanging:

    perl -e 'alarm shift; exec @ARGV' 60 \
        env PYTHONPATH=src .venv/bin/python scripts/model_smoke.py --profile bedrock_kimi_k2_5_ap_south_1
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diracdata.config import settings_from_env  # noqa: E402
from diracdata.utils.model_factory import ChatModelFactory  # noqa: E402

GARDEN = [
    "bedrock_zai_glm_5_ap_south_1",
    "anthropic_haiku_45",
    "anthropic_sonnet_46",
    "openai_gpt_5_4_mini",
    "bedrock_gpt_oss_120b_ap_south_1",
    "bedrock_qwen3_next_80b_a3b_ap_south_1",
    "bedrock_kimi_k2_5_ap_south_1",
]


def _echo_tool():
    from langchain.tools import tool

    @tool("echo")
    def echo(text: str) -> str:
        """Echo back the given text."""
        return f"echo: {text}"

    return echo


def smoke(profile: str, settings) -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    r = {"profile": profile, "reach": "N", "tool_call": "N", "final": "N", "sec": 0.0, "note": ""}
    t0 = time.time()
    try:
        model = ChatModelFactory(settings=replace(settings, agent_model_profile=profile)
                                 ).create_chat_model(profile_id=profile)
        bound = model.bind_tools([_echo_tool()])
        msg = bound.invoke([SystemMessage(content="You have an `echo` tool."),
                            HumanMessage(content="Call the echo tool with text='ping'.")])
        r["reach"] = "Y"
        calls = list(getattr(msg, "tool_calls", []) or [])
        if calls:
            r["tool_call"] = "Y"
            tr = ToolMessage(content="echo: ping", tool_call_id=calls[0].get("id", "echo"))
            final = model.invoke([SystemMessage(content="Reply with one word."),
                                  HumanMessage(content="Say DONE."), msg, tr])
            r["final"] = "Y" if str(getattr(final, "content", "")).strip() else "N"
        else:
            r["note"] = "no tool_call emitted"
    except Exception as exc:  # noqa: BLE001
        r["note"] = f"{type(exc).__name__}: {str(exc)[:90]}"
    r["sec"] = round(time.time() - t0, 1)
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default=None, help="a single profile (recommended, with an OS timeout)")
    ap.add_argument("--all", action="store_true", help="run the whole garden in-process (no per-model timeout)")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    args = ap.parse_args()
    settings = settings_from_env(args.env_file)
    profiles = GARDEN if args.all else ([args.profile] if args.profile else GARDEN)
    for p in profiles:
        r = smoke(p, settings)
        drives = "YES" if r["tool_call"] == "Y" and r["final"] == "Y" else "NO"
        print(f"{p:<42} reach={r['reach']} tool={r['tool_call']} final={r['final']} "
              f"drives-loop={drives:<3} {r['sec']:>6}s  {r['note']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
