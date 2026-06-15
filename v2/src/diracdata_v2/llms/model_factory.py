"""Small LangChain init_chat_model factory for v2 agent tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from diracdata_v2.settings import V2Settings


class ModelProvider(StrEnum):
    ANTHROPIC = "anthropic"
    BEDROCK_CONVERSE = "bedrock_converse"
    OPENAI = "openai"


@dataclass(frozen=True)
class ChatModelProfile:
    profile_id: str
    provider: ModelProvider
    model: str
    display_name: str
    region_name: str | None = None
    base_url: str | None = None
    max_tokens: int = 8192
    is_moe: bool = False
    credential_source: str = "default"
    model_kwargs: dict[str, Any] = field(default_factory=dict)


BUILT_IN_MODEL_PROFILES: dict[str, ChatModelProfile] = {
    "anthropic_sonnet_46": ChatModelProfile(
        "anthropic_sonnet_46",
        ModelProvider.ANTHROPIC,
        "claude-sonnet-4-6",
        "Claude Sonnet 4.6",
    ),
    "anthropic_haiku_45": ChatModelProfile(
        "anthropic_haiku_45",
        ModelProvider.ANTHROPIC,
        "claude-haiku-4-5-20251001",
        "Claude Haiku 4.5",
    ),
    "openai_gpt_5_4_mini": ChatModelProfile(
        "openai_gpt_5_4_mini",
        ModelProvider.OPENAI,
        "gpt-5.4-mini",
        "GPT-5.4 Mini",
        credential_source="openai",
    ),
    "bedrock_qwen3_next_80b_a3b_ap_south_1": ChatModelProfile(
        "bedrock_qwen3_next_80b_a3b_ap_south_1",
        ModelProvider.BEDROCK_CONVERSE,
        "qwen.qwen3-next-80b-a3b",
        "Qwen3 Next 80B A3B on Bedrock",
        region_name="ap-south-1",
        is_moe=True,
        credential_source="bedrock",
    ),
    "bedrock_zai_glm_5_ap_south_1": ChatModelProfile(
        "bedrock_zai_glm_5_ap_south_1",
        ModelProvider.BEDROCK_CONVERSE,
        "zai.glm-5",
        "Z.ai GLM-5 on Bedrock",
        region_name="ap-south-1",
        credential_source="bedrock",
    ),
}


class ChatModelFactory:
    def __init__(self, *, settings: V2Settings) -> None:
        self.settings = settings

    def create_agent_chat_model(self) -> object:
        return self.create_chat_model(profile_id=self.settings.agent_model_profile)

    def create_chat_model(self, *, profile_id: str | None = None) -> object:
        profile = BUILT_IN_MODEL_PROFILES.get(profile_id or self.settings.agent_model_profile)
        provider = profile.provider if profile else ModelProvider(self.settings.agent_llm_provider)
        model = profile.model if profile else self.settings.agent_llm_model
        max_tokens = min(self.settings.agent_llm_max_tokens, profile.max_tokens) if profile else self.settings.agent_llm_max_tokens
        kwargs: dict[str, Any] = dict(profile.model_kwargs) if profile else {}
        region_name = self.settings.bedrock_region or (profile.region_name if profile else None)
        if provider == ModelProvider.ANTHROPIC:
            kwargs["base_url"] = self.settings.anthropic_base_url
            api_key = self.settings.anthropic_api_key
        elif provider == ModelProvider.OPENAI:
            api_key = self.settings.openai_api_key
            if profile and profile.base_url:
                kwargs["base_url"] = profile.base_url
        elif provider == ModelProvider.BEDROCK_CONVERSE:
            api_key = self.settings.bedrock_api_key
            if region_name:
                kwargs["region_name"] = region_name
        else:
            api_key = None
        if api_key:
            kwargs["api_key"] = api_key
        _validate(provider=provider, api_key=api_key, region_name=region_name)
        with _provider_environment(settings=self.settings, provider=provider, api_key=api_key):
            return init_chat_model(
                model=model,
                model_provider=provider.value,
                max_tokens=max_tokens,
                temperature=self.settings.agent_llm_temperature,
                **kwargs,
            )


def agent_chat_model_from_settings(settings: V2Settings) -> object:
    return ChatModelFactory(settings=settings).create_agent_chat_model()


def init_chat_model(**kwargs: Any) -> object:
    try:
        from langchain.chat_models import init_chat_model as lc_init_chat_model
    except ImportError as exc:
        raise RuntimeError("v2 agent requires langchain chat model integrations") from exc
    return lc_init_chat_model(**kwargs)


def _validate(*, provider: ModelProvider, api_key: str | None, region_name: str | None) -> None:
    if provider in {ModelProvider.ANTHROPIC, ModelProvider.OPENAI} and not api_key:
        raise ValueError(f"{provider.value} API key is required")
    if provider == ModelProvider.BEDROCK_CONVERSE and not region_name:
        raise ValueError("Bedrock region is required")


@contextmanager
def _provider_environment(
    *,
    settings: V2Settings,
    provider: ModelProvider,
    api_key: str | None,
) -> Iterator[None]:
    restore: dict[str, str | None] = {}
    if provider == ModelProvider.ANTHROPIC and api_key:
        restore["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = api_key
    if provider == ModelProvider.OPENAI and api_key:
        restore["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = api_key
    if provider == ModelProvider.BEDROCK_CONVERSE and api_key:
        restore["AWS_BEARER_TOKEN_BEDROCK"] = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = api_key
    try:
        yield
    finally:
        for key, value in restore.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
