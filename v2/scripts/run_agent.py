#!/usr/bin/env python3
"""Run the lean v2 data analyst agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.agent import create_v2_agent  # noqa: E402
from diracdata_v2.settings import settings_from_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--model-profile", default=None)
    parser.add_argument("--question", required=True)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--stream-mode", default="updates")
    args = parser.parse_args()

    if args.model_profile:
        import os

        os.environ["DIRACDATA_AGENT_MODEL_PROFILE"] = args.model_profile
    settings = settings_from_env(args.env_file)
    runtime = create_v2_agent(settings=settings)

    if args.stream:
        for event in runtime.stream(args.question, stream_mode=_stream_mode(args.stream_mode)):
            print(_json(event))
    else:
        print(_json(runtime.invoke(args.question)))
    return 0


def _stream_mode(value: str) -> str | list[str]:
    parts = [item.strip() for item in value.split(",") if item.strip()]
    return parts if len(parts) > 1 else parts[0] if parts else "updates"


def _json(value: Any) -> str:
    return json.dumps(_plain(value), indent=2, default=str)


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    return value


if __name__ == "__main__":
    raise SystemExit(main())

