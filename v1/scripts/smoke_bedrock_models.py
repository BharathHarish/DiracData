"""Smoke test Amazon Bedrock Converse and ConverseStream for one chat model."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import settings_from_env
from diracdata.llms import ChatModelFactory, ModelProvider


DEFAULT_PROMPT = "Reply with exactly: bedrock-ok"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--region", default=None)
    parser.add_argument("--model-id", default=None)
    parser.add_argument(
        "--model-profile",
        default=None,
        help=(
            "Named DiracData model profile, for example "
            "bedrock_qwen3_next_80b_a3b_ap_south_1 or "
            "bedrock_qwen3_coder_480b_a35b_ap_south_1."
        ),
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Use Bedrock ConverseStream instead of Converse.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = settings_from_env(args.env_file)
    if args.model_profile:
        from dataclasses import replace

        settings = replace(
            settings,
            agent_model_profile=args.model_profile,
            bedrock_region=args.region or settings.bedrock_region,
        )
        resolved = ChatModelFactory(settings=settings).resolve_agent_model()
        if resolved.provider != ModelProvider.BEDROCK_CONVERSE:
            raise SystemExit(
                f"{args.model_profile} resolves to {resolved.provider.value}; "
                "this smoke script uses boto3 bedrock-runtime and requires bedrock_converse."
            )
        args.model_id = args.model_id or resolved.model
        args.region = args.region or resolved.region_name
    args.region = args.region or settings.bedrock_region or settings.aws_region
    if not args.model_id:
        raise SystemExit("--model-id or --model-profile is required")
    _normalize_bedrock_api_key_env()
    client = boto3.client("bedrock-runtime", region_name=args.region)
    started = time.perf_counter()
    try:
        if args.stream:
            response = _converse_stream(client=client, args=args)
        else:
            response = _converse(client=client, args=args)
    except Exception as exc:  # noqa: BLE001 - CLI smoke should return provider error
        print(
            json.dumps(
                {
                    "status": "error",
                    "model_id": args.model_id,
                    "region": args.region,
                    "stream": args.stream,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                indent=2,
            ),
            flush=True,
        )
        raise SystemExit(1) from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    response["elapsed_ms"] = elapsed_ms
    print(json.dumps(response, indent=2), flush=True)


def _normalize_bedrock_api_key_env() -> None:
    token = (
        os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        or os.environ.get("DIRACDATA_BEDROCK_API_KEY")
    )
    if token:
        os.environ.setdefault("AWS_BEARER_TOKEN_BEDROCK", token)


def _converse(*, client: Any, args: argparse.Namespace) -> dict[str, Any]:
    response = client.converse(
        modelId=args.model_id,
        messages=[
            {
                "role": "user",
                "content": [{"text": args.prompt}],
            }
        ],
        inferenceConfig={
            "maxTokens": args.max_tokens,
            "temperature": args.temperature,
        },
    )
    return {
        "status": "ok",
        "api": "Converse",
        "model_id": args.model_id,
        "region": args.region,
        "stream": False,
        "text": _extract_text(response),
        "usage": response.get("usage", {}),
        "stop_reason": response.get("stopReason"),
    }


def _converse_stream(*, client: Any, args: argparse.Namespace) -> dict[str, Any]:
    response = client.converse_stream(
        modelId=args.model_id,
        messages=[
            {
                "role": "user",
                "content": [{"text": args.prompt}],
            }
        ],
        inferenceConfig={
            "maxTokens": args.max_tokens,
            "temperature": args.temperature,
        },
    )
    parts: list[str] = []
    metadata: dict[str, Any] = {}
    chunk_count = 0
    for event in response["stream"]:
        chunk_count += 1
        delta = event.get("contentBlockDelta", {}).get("delta", {})
        if isinstance(delta.get("text"), str):
            parts.append(delta["text"])
        if "metadata" in event:
            metadata = event["metadata"]

    return {
        "status": "ok",
        "api": "ConverseStream",
        "model_id": args.model_id,
        "region": args.region,
        "stream": True,
        "text": "".join(parts).strip(),
        "chunk_count": chunk_count,
        "usage": metadata.get("usage", {}),
        "metrics": metadata.get("metrics", {}),
    }


def _extract_text(response: dict[str, Any]) -> str:
    content = response.get("output", {}).get("message", {}).get("content", [])
    parts = [item.get("text", "") for item in content if isinstance(item, dict)]
    return "".join(parts).strip()


if __name__ == "__main__":
    main()
