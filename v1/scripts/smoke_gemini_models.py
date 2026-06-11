"""Smoke test Google Gemini profiles through DiracData's LangChain model factory."""

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


DEFAULT_PROMPT = "Reply with exactly: gemini-ok"
DEFAULT_PROFILES = (
    "gemini_2_5_flash_lite",
    "gemini_2_5_flash",
    "gemini_2_5_pro",
    "gemini_2_0_flash",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--model-profile",
        action="append",
        default=[],
        help=(
            "Gemini model profile to probe. May be repeated. Examples: "
            "gemini_2_5_flash, gemini_2_5_flash_lite, gemini_3_5_flash."
        ),
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument(
        "--list-google-models",
        action="store_true",
        help="List models returned by the Google GenAI SDK before running probes.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = settings_from_env(args.env_file)
    _normalize_google_api_key_env(settings.google_api_key)
    if args.list_google_models:
        print(json.dumps({"google_models": _list_google_models()}, indent=2), flush=True)

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
    if resolved.provider != ModelProvider.GOOGLE_GENAI:
        raise ValueError(f"{profile} resolves to {resolved.provider.value}, not google_genai")

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
    return {
        "status": "ok",
        "profile_id": profile,
        "model": resolved.model,
        "provider": resolved.provider.value,
        "stream": args.stream,
        "text": text,
        "chunk_count": chunk_count,
    }


def _normalize_google_api_key_env(settings_key: str | None) -> None:
    key = (
        settings_key
        or os.environ.get("DIRACDATA_GOOGLE_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
    )
    if key:
        os.environ.setdefault("GOOGLE_API_KEY", key)


def _list_google_models() -> list[dict[str, Any]]:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "Listing Gemini models requires the google-genai package, installed with "
            "langchain-google-genai."
        ) from exc

    client = genai.Client()
    rows = []
    for model in client.models.list():
        payload = _model_payload(model)
        name = str(payload.get("name") or "")
        if "gemini" not in name.lower():
            continue
        rows.append(
            {
                "name": name,
                "display_name": payload.get("display_name") or payload.get("displayName"),
                "supported_actions": (
                    payload.get("supported_actions")
                    or payload.get("supportedActions")
                    or payload.get("supported_generation_methods")
                    or payload.get("supportedGenerationMethods")
                ),
            }
        )
    return rows


def _model_payload(model: object) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        payload = model.model_dump()
        if isinstance(payload, dict):
            return payload
    if hasattr(model, "to_json_dict"):
        payload = model.to_json_dict()
        if isinstance(payload, dict):
            return payload
    if isinstance(model, dict):
        return model
    return {key: getattr(model, key) for key in dir(model) if not key.startswith("_")}


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
