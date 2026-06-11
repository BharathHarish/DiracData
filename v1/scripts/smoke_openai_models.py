"""Smoke test OpenAI profiles through DiracData's LangChain model factory."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path
import sys
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import settings_from_env
from diracdata.llms import ChatModelFactory, ModelProvider


DEFAULT_PROMPT = "Reply with exactly: openai-ok"
DEFAULT_PROFILES = (
    "openai_gpt_5_4_mini",
    "openai_gpt_5_4_nano",
    "openai_gpt_5_nano",
    "openai_gpt_5_mini",
    "openai_gpt_4_1_nano",
    "openai_gpt_4o_mini",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--model-profile",
        action="append",
        default=[],
        help=(
            "OpenAI model profile to probe. May be repeated. Examples: "
            "openai_gpt_5_4_mini, openai_gpt_5_4_nano, openai_gpt_5_mini."
        ),
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument(
        "--list-openai-models",
        action="store_true",
        help="List OpenAI models visible to this API key before running probes.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = settings_from_env(args.env_file)
    _normalize_openai_api_key_env(settings.openai_api_key)
    if args.list_openai_models:
        print(json.dumps({"openai_models": _list_openai_models()}, indent=2), flush=True)

    profiles = tuple(args.model_profile) or DEFAULT_PROFILES
    rows = []
    for profile in profiles:
        started = time.perf_counter()
        try:
            row = _probe_profile(settings=settings, args=args, profile=profile)
            row["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        except Exception as exc:  # noqa: BLE001 - smoke CLI should expose provider failures
            row = {
                "status": "error",
                "profile_id": profile,
                "stream": args.stream,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
        rows.append(row)
        print(json.dumps(row, indent=2), flush=True)

    if not any(row.get("status") == "ok" for row in rows):
        raise SystemExit(1)


def _probe_profile(
    *,
    settings: object,
    args: argparse.Namespace,
    profile: str,
) -> dict[str, Any]:
    profile_settings = replace(
        settings,
        agent_model_profile=profile,
        agent_llm_max_tokens=args.max_tokens,
        agent_llm_temperature=args.temperature,
    )
    factory = ChatModelFactory(settings=profile_settings)
    resolved = factory.resolve_agent_model()
    if resolved.provider != ModelProvider.OPENAI or resolved.credential_source != "openai":
        raise ValueError(
            f"{profile} resolves to {resolved.provider.value} "
            f"with credential_source={resolved.credential_source!r}, not native OpenAI"
        )

    model = factory.create_agent_chat_model()
    messages = [{"role": "user", "content": args.prompt}]
    if args.stream:
        chunks = list(model.stream(messages))
        text = "".join(_response_text(chunk) for chunk in chunks).strip()
        chunk_count = len(chunks)
    else:
        response = model.invoke(messages)
        text = _response_text(response)
        chunk_count = None
    if not text:
        raise ValueError(
            "Model returned empty text. Increase --max-tokens; GPT-5 profiles may spend "
            "completion budget on hidden reasoning before visible output."
        )
    return {
        "status": "ok",
        "profile_id": profile,
        "model": resolved.model,
        "provider": resolved.provider.value,
        "stream": args.stream,
        "text": text,
        "chunk_count": chunk_count,
    }


def _normalize_openai_api_key_env(settings_key: str | None) -> None:
    key = settings_key or os.environ.get("DIRACDATA_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        os.environ.setdefault("OPENAI_API_KEY", key)


def _list_openai_models() -> list[dict[str, Any]]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Listing OpenAI models requires the openai package.") from exc

    client = OpenAI()
    rows = []
    for model in client.models.list().data:
        model_id = getattr(model, "id", "")
        if not isinstance(model_id, str):
            continue
        if not _is_useful_chat_model_id(model_id):
            continue
        rows.append({"id": model_id, "created": getattr(model, "created", None)})
    return sorted(rows, key=lambda row: str(row["id"]))


def _is_useful_chat_model_id(model_id: str) -> bool:
    prefixes = ("gpt-5", "gpt-4.1", "gpt-4o")
    return model_id.startswith(prefixes) and not any(
        marker in model_id for marker in ("audio", "image", "transcribe", "tts")
    )


def _response_text(response: object) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    return str(content)


if __name__ == "__main__":
    main()
