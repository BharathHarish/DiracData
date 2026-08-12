"""LLM client — thin wrapper over Fireworks (OpenAI-compatible). Tool-calling only.

Same pattern as scripts/mcp_client_uat.py. Kept minimal so we can swap providers.
"""
from __future__ import annotations
import os
from typing import Any, Dict, List, Optional
from openai import OpenAI
from .config import ModellerConfig


# Model-profile → resolved model id (mirrors src/diracdata/utils/model_factory.py profiles)
_FIREWORKS_MODELS = {
    "fireworks_deepseek_v4_flash": "accounts/fireworks/models/deepseek-v4-flash-0731",
    "fireworks_gpt_oss_120b":       "accounts/fireworks/models/gpt-oss-120b",
    "fireworks_kimi_k2p7_code":     "accounts/fireworks/models/kimi-k2p7-code",
    "fireworks_glm_5p2":            "accounts/fireworks/models/glm-5p2",
}


def make_llm(cfg: ModellerConfig) -> OpenAI:
    api_key = os.environ.get("FIREWORKS_API_KEY", "")
    return OpenAI(base_url="https://api.fireworks.ai/inference/v1", api_key=api_key)


def resolve_model(profile: str) -> str:
    return _FIREWORKS_MODELS.get(profile, profile)


def chat_with_tools(
    client: OpenAI,
    model: str,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    temperature: float = 0.0,
    tool_choice: str = "auto",
) -> Any:
    """Single chat.completions.create call with tool support. Returns the message."""
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        temperature=temperature,
    )
    return resp.choices[0].message, {
        "prompt_tokens":     resp.usage.prompt_tokens if resp.usage else 0,
        "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
        "total_tokens":      resp.usage.total_tokens if resp.usage else 0,
    }
