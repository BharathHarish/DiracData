"""LangChain-backed chat model utilities."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from diracdata.config.settings import DiracDataSettings


@dataclass(frozen=True)
class ChatModelMessage:
    role: str
    content: str


class ChatModelClient(Protocol):
    model: str

    def complete(self, messages: list[ChatModelMessage]) -> str: ...


@dataclass
class LangChainChatModelClient:
    """Chat model client built with LangChain's provider-agnostic init_chat_model API."""

    model: str
    model_provider: str
    max_tokens: int
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = 0.0
    model_kwargs: dict[str, Any] = field(default_factory=dict)
    cache: dict[str, str] = field(default_factory=dict)

    def complete(self, messages: list[ChatModelMessage]) -> str:
        key = _messages_key(
            self.model,
            self.model_provider,
            self.max_tokens,
            self.temperature,
            messages,
        )
        if key in self.cache:
            return self.cache[key]

        with self._provider_environment():
            chat_model = self._init_chat_model()
            response = chat_model.invoke(
                [
                    {"role": message.role, "content": message.content}
                    for message in messages
                ]
            )
        text = _response_text(response)
        self.cache[key] = text
        return text

    def _init_chat_model(self) -> object:
        kwargs = dict(self.model_kwargs)
        if self.base_url is not None:
            kwargs["base_url"] = self.base_url
        if self.api_key is not None:
            kwargs["api_key"] = self.api_key
        return init_langchain_chat_model(
            model=self.model,
            model_provider=self.model_provider,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            **kwargs,
        )

    @contextmanager
    def _provider_environment(self) -> Iterator[None]:
        env_restore: dict[str, str | None] = {}
        if self.model_provider == "anthropic" and self.api_key:
            env_restore["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY")
            os.environ["ANTHROPIC_API_KEY"] = self.api_key
        if self.model_provider == "google_genai" and self.api_key:
            if not os.environ.get("GOOGLE_API_KEY"):
                env_restore["GOOGLE_API_KEY"] = os.environ.get("GOOGLE_API_KEY")
                os.environ["GOOGLE_API_KEY"] = self.api_key
        if self.model_provider == "openai" and self.api_key:
            if not os.environ.get("OPENAI_API_KEY"):
                env_restore["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY")
                os.environ["OPENAI_API_KEY"] = self.api_key
        if self.model_provider == "bedrock_converse" and self.api_key:
            if not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
                env_restore["AWS_BEARER_TOKEN_BEDROCK"] = os.environ.get(
                    "AWS_BEARER_TOKEN_BEDROCK"
                )
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = self.api_key

        try:
            yield
        finally:
            for key, value in env_restore.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def chat_model_client_from_settings(settings: DiracDataSettings) -> ChatModelClient:
    if settings.llm_model_profile:
        return _chat_model_client_from_profile(settings)

    provider = settings.llm_provider.lower()
    if provider == "anthropic" and not settings.anthropic_api_key:
        raise ValueError("DIRACDATA_ANTHROPIC_API_KEY is required for Anthropic chat models")
    if provider == "google_genai" and not settings.google_api_key:
        raise ValueError(
            "DIRACDATA_GOOGLE_API_KEY, GOOGLE_API_KEY, or GEMINI_API_KEY "
            "is required for Google Gemini chat models"
        )
    if provider == "openai" and not settings.openai_api_key:
        raise ValueError("DIRACDATA_OPENAI_API_KEY or OPENAI_API_KEY is required for OpenAI chat models")
    if provider == "bedrock_converse" and not settings.bedrock_region:
        raise ValueError("DIRACDATA_BEDROCK_REGION is required for Bedrock Converse learning models")
    return LangChainChatModelClient(
        model=settings.llm_model,
        model_provider=provider,
        max_tokens=settings.llm_max_tokens,
        api_key=(
            settings.anthropic_api_key
            if provider == "anthropic"
            else settings.google_api_key
            if provider == "google_genai"
            else settings.openai_api_key
            if provider == "openai"
            else settings.bedrock_api_key
            if provider == "bedrock_converse"
            else None
        ),
        base_url=settings.anthropic_base_url if provider == "anthropic" else None,
        temperature=settings.llm_temperature,
        model_kwargs=(
            {"region_name": settings.bedrock_region}
            if provider == "bedrock_converse" and settings.bedrock_region
            else {}
        ),
    )


def _chat_model_client_from_profile(settings: DiracDataSettings) -> ChatModelClient:
    from diracdata.llms.model_factory import BUILT_IN_MODEL_PROFILES, ModelProvider

    profile = BUILT_IN_MODEL_PROFILES.get(settings.llm_model_profile or "")
    if profile is None:
        available = ", ".join(sorted(BUILT_IN_MODEL_PROFILES))
        raise ValueError(
            f"Unknown DIRACDATA_LLM_MODEL_PROFILE={settings.llm_model_profile!r}. "
            f"Available profiles: {available}"
        )
    provider = profile.provider
    region_name = (
        settings.bedrock_region or profile.region_name
        if provider == ModelProvider.BEDROCK_CONVERSE
        else profile.region_name
    )
    api_key = None
    if provider == ModelProvider.ANTHROPIC:
        api_key = settings.anthropic_api_key
        if not api_key:
            raise ValueError("DIRACDATA_ANTHROPIC_API_KEY is required for Anthropic chat models")
    elif provider == ModelProvider.GOOGLE_GENAI:
        api_key = settings.google_api_key
        if not api_key:
            raise ValueError(
                "DIRACDATA_GOOGLE_API_KEY, GOOGLE_API_KEY, or GEMINI_API_KEY "
                "is required for Google Gemini chat models"
            )
    elif provider == ModelProvider.OPENAI:
        api_key = settings.openai_api_key
        if not api_key:
            raise ValueError(
                "DIRACDATA_OPENAI_API_KEY or OPENAI_API_KEY is required for OpenAI chat models"
            )
    elif provider == ModelProvider.BEDROCK_CONVERSE:
        api_key = settings.bedrock_api_key
        if not region_name:
            raise ValueError("DIRACDATA_BEDROCK_REGION is required for Bedrock Converse learning models")

    return LangChainChatModelClient(
        model=profile.model,
        model_provider=provider.value,
        max_tokens=settings.llm_max_tokens if settings.llm_max_tokens is not None else profile.max_tokens or 8192,
        api_key=api_key,
        base_url=settings.anthropic_base_url if provider == ModelProvider.ANTHROPIC else None,
        temperature=settings.llm_temperature,
        model_kwargs={
            **dict(profile.model_kwargs),
            **({"region_name": region_name} if provider == ModelProvider.BEDROCK_CONVERSE else {}),
        },
    )


def agent_chat_model_from_settings(settings: DiracDataSettings) -> object:
    """Create a LangChain chat model instance for answer-time agents."""
    from diracdata.llms.model_factory import agent_chat_model_from_settings as factory_agent_model

    return factory_agent_model(settings)


def init_langchain_chat_model(
    *,
    model: str,
    model_provider: str,
    max_tokens: int,
    temperature: float,
    base_url: str | None = None,
    **extra_kwargs: Any,
) -> object:
    """Initialize a provider-agnostic LangChain chat model."""
    try:
        from langchain.chat_models import init_chat_model
    except ImportError as exc:
        raise RuntimeError(
            "LangChain chat models require langchain and the provider integration package. "
            "Install project dependencies first."
        ) from exc

    kwargs: dict[str, object] = {
        "model": model,
        "model_provider": model_provider,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if base_url:
        kwargs["base_url"] = base_url
    kwargs.update(extra_kwargs)
    return init_chat_model(**kwargs)


@contextmanager
def _provider_environment(
    *,
    model_provider: str,
    api_key: str | None,
) -> Iterator[None]:
    env_restore: dict[str, str | None] = {}
    if model_provider == "anthropic" and api_key:
        env_restore["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = api_key
    if model_provider == "google_genai" and api_key:
        if not os.environ.get("GOOGLE_API_KEY"):
            env_restore["GOOGLE_API_KEY"] = os.environ.get("GOOGLE_API_KEY")
            os.environ["GOOGLE_API_KEY"] = api_key
    if model_provider == "openai" and api_key:
        if not os.environ.get("OPENAI_API_KEY"):
            env_restore["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY")
            os.environ["OPENAI_API_KEY"] = api_key

    try:
        yield
    finally:
        for key, value in env_restore.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _messages_key(
    model: str,
    provider: str,
    max_tokens: int,
    temperature: float,
    messages: list[ChatModelMessage],
) -> str:
    digest = hashlib.sha256()
    digest.update(provider.encode("utf-8"))
    digest.update(model.encode("utf-8"))
    digest.update(str(max_tokens).encode("utf-8"))
    digest.update(str(temperature).encode("utf-8"))
    for message in messages:
        digest.update(message.role.encode("utf-8"))
        digest.update(b"\0")
        digest.update(message.content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


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
        return "\n".join(part for part in parts if part).strip()
    return str(content)
