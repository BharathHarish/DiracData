"""Small LangChain init_chat_model factory for v2 agent tests."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
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
        provider, api_key, region_name, init_kwargs = build_model_init(
            settings=self.settings, profile_id=profile_id
        )
        _validate(provider=provider, api_key=api_key, region_name=region_name)
        with _provider_environment(settings=self.settings, provider=provider, api_key=api_key):
            return init_chat_model(**init_kwargs)


def build_model_init(
    *, settings: V2Settings, profile_id: str | None = None
) -> tuple[ModelProvider, str | None, str | None, dict[str, Any]]:
    """Assemble the kwargs passed to init_chat_model, as a pure function.

    Kept separate from client construction so the determinism floor (R1) is
    unit-testable without a live provider: the returned dict is exactly what the
    model is built with, so a test can assert temperature is pinned and the seed is
    only forwarded to providers that support it.
    """

    profile = BUILT_IN_MODEL_PROFILES.get(profile_id or settings.agent_model_profile)
    provider = profile.provider if profile else ModelProvider(settings.agent_llm_provider)
    model = profile.model if profile else settings.agent_llm_model
    max_tokens = min(settings.agent_llm_max_tokens, profile.max_tokens) if profile else settings.agent_llm_max_tokens
    kwargs: dict[str, Any] = dict(profile.model_kwargs) if profile else {}
    region_name = settings.bedrock_region or (profile.region_name if profile else None)
    if provider == ModelProvider.ANTHROPIC:
        kwargs["base_url"] = settings.anthropic_base_url
        api_key = settings.anthropic_api_key
    elif provider == ModelProvider.OPENAI:
        api_key = settings.openai_api_key
        if profile and profile.base_url:
            kwargs["base_url"] = profile.base_url
    elif provider == ModelProvider.BEDROCK_CONVERSE:
        api_key = settings.bedrock_api_key
        if region_name:
            kwargs["region_name"] = region_name
        if settings.llm_timeout_seconds is not None:
            kwargs["config"] = _bedrock_client_config(
                timeout_seconds=settings.llm_timeout_seconds,
                max_retries=settings.llm_max_retries,
            )
    else:
        api_key = None
    if api_key:
        kwargs["api_key"] = api_key
    if settings.llm_timeout_seconds is not None:
        kwargs["timeout"] = settings.llm_timeout_seconds
    kwargs["max_retries"] = max(0, settings.llm_max_retries)
    # Real token streaming is opt-in. ChatBedrockConverse defaults disable_streaming=True
    # (buffers to a single chunk); flip it when stream_tokens is on so model.stream()
    # yields incremental chunks. A profile may still pin its own value.
    kwargs.setdefault("disable_streaming", not settings.stream_tokens)
    # Determinism floor: when deterministic_sampling is on, pin temperature to 0.0 for
    # every stage regardless of agent_llm_temperature drift. A decode seed is forwarded
    # only to providers that actually honour one (OpenAI); others have no seed knob.
    temperature = 0.0 if settings.deterministic_sampling else settings.agent_llm_temperature
    if settings.agent_llm_seed is not None and provider == ModelProvider.OPENAI:
        kwargs["seed"] = settings.agent_llm_seed
    init_kwargs = dict(
        model=model,
        model_provider=provider.value,
        max_tokens=max_tokens,
        temperature=temperature,
        **kwargs,
    )
    return provider, api_key, region_name, init_kwargs


def agent_chat_model_from_settings(settings: V2Settings) -> object:
    return ChatModelFactory(settings=settings).create_agent_chat_model()


class ChatCompletionClient:
    """Tiny completion adapter for learning steps that expect dict messages."""

    def __init__(self, *, model: object, timeout_seconds: float | None = None) -> None:
        self._model = model
        self._timeout_seconds = timeout_seconds

    def complete(self, messages: list[dict[str, str]]) -> str:
        if self._timeout_seconds is None:
            response = self._model.invoke(messages)
            return _response_text(response)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="diracdata-llm-complete")
        future = executor.submit(self._model.invoke, messages)
        try:
            response = future.result(timeout=self._timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"LLM completion exceeded {self._timeout_seconds:g}s timeout") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        return _response_text(response)


def chat_completion_client_from_settings(
    settings: V2Settings,
    *,
    profile_id: str | None = None,
) -> ChatCompletionClient:
    return ChatCompletionClient(
        model=ChatModelFactory(settings=settings).create_chat_model(profile_id=profile_id),
        timeout_seconds=settings.llm_timeout_seconds,
    )


def init_chat_model(**kwargs: Any) -> object:
    try:
        from langchain.chat_models import init_chat_model as lc_init_chat_model
    except ImportError as exc:
        raise RuntimeError("v2 agent requires langchain chat model integrations") from exc
    return lc_init_chat_model(**kwargs)


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content)


def _validate(*, provider: ModelProvider, api_key: str | None, region_name: str | None) -> None:
    if provider in {ModelProvider.ANTHROPIC, ModelProvider.OPENAI} and not api_key:
        raise ValueError(f"{provider.value} API key is required")
    if provider == ModelProvider.BEDROCK_CONVERSE and not region_name:
        raise ValueError("Bedrock region is required")


def _bedrock_client_config(*, timeout_seconds: int, max_retries: int) -> object:
    try:
        from botocore.config import Config
    except ImportError as exc:
        raise RuntimeError("Bedrock Converse profiles require botocore") from exc
    safe_timeout = max(1, timeout_seconds)
    safe_retries = max(0, max_retries)
    return Config(
        connect_timeout=min(10, safe_timeout),
        read_timeout=safe_timeout,
        retries={"max_attempts": safe_retries + 1},
    )


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
