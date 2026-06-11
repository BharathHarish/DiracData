"""Provider-agnostic chat model factory for learning and answer-time agents."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from diracdata.config.settings import DiracDataSettings
from diracdata.llms.chat_models import init_langchain_chat_model


class ModelProvider(StrEnum):
    """Supported LangChain chat model providers."""

    ANTHROPIC = "anthropic"
    BEDROCK_CONVERSE = "bedrock_converse"
    GOOGLE_GENAI = "google_genai"
    OPENAI = "openai"


@dataclass(frozen=True)
class ChatModelProfile:
    """Named model profile that can be selected from ENV or CLI."""

    profile_id: str
    provider: ModelProvider
    model: str
    display_name: str
    region_name: str | None = None
    base_url: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    supports_tool_use: bool = True
    supports_streaming: bool = True
    is_moe: bool = False
    notes: tuple[str, ...] = ()
    model_kwargs: dict[str, Any] = field(default_factory=dict)
    credential_source: str = "default"


@dataclass(frozen=True)
class ResolvedChatModel:
    """Concrete model settings after applying profile and runtime overrides."""

    profile_id: str | None
    provider: ModelProvider
    model: str
    max_tokens: int
    temperature: float
    region_name: str | None = None
    base_url: str | None = None
    supports_tool_use: bool = True
    supports_streaming: bool = True
    is_moe: bool = False
    model_kwargs: dict[str, Any] = field(default_factory=dict)
    credential_source: str = "default"


BUILT_IN_MODEL_PROFILES: dict[str, ChatModelProfile] = {
    "anthropic_sonnet_46": ChatModelProfile(
        profile_id="anthropic_sonnet_46",
        provider=ModelProvider.ANTHROPIC,
        model="claude-sonnet-4-6",
        display_name="Claude Sonnet 4.6",
        max_tokens=8192,
    ),
    "anthropic_haiku_45": ChatModelProfile(
        profile_id="anthropic_haiku_45",
        provider=ModelProvider.ANTHROPIC,
        model="claude-haiku-4-5-20251001",
        display_name="Claude Haiku 4.5",
        max_tokens=8192,
    ),
    "bedrock_anthropic_sonnet_46_ap_south_1": ChatModelProfile(
        profile_id="bedrock_anthropic_sonnet_46_ap_south_1",
        provider=ModelProvider.BEDROCK_CONVERSE,
        model="global.anthropic.claude-sonnet-4-6",
        display_name="Claude Sonnet 4.6 on Bedrock Converse",
        region_name="ap-south-1",
        max_tokens=8192,
        notes=(
            "Learning/evaluation profile for using Claude Sonnet through a Bedrock inference profile.",
        ),
        credential_source="bedrock",
    ),
    "bedrock_anthropic_haiku_45_ap_south_1": ChatModelProfile(
        profile_id="bedrock_anthropic_haiku_45_ap_south_1",
        provider=ModelProvider.BEDROCK_CONVERSE,
        model="anthropic.claude-haiku-4-5-20251001-v1:0",
        display_name="Claude Haiku 4.5 on Bedrock Converse",
        region_name="ap-south-1",
        max_tokens=8192,
        notes=(
            "Economy Claude profile for Bedrock Converse UAT.",
        ),
        credential_source="bedrock",
    ),
    "openai_gpt_5_nano": ChatModelProfile(
        profile_id="openai_gpt_5_nano",
        provider=ModelProvider.OPENAI,
        model="gpt-5-nano",
        display_name="GPT-5 nano",
        max_tokens=8192,
        notes=(
            "Lowest-cost GPT-5 profile for testing whether the harness works on small models.",
        ),
        credential_source="openai",
    ),
    "openai_gpt_5_mini": ChatModelProfile(
        profile_id="openai_gpt_5_mini",
        provider=ModelProvider.OPENAI,
        model="gpt-5-mini",
        display_name="GPT-5 mini",
        max_tokens=8192,
        notes=(
            "Cost-efficient GPT-5 profile for harder agent/tool UAT than nano.",
        ),
        credential_source="openai",
    ),
    "openai_gpt_5_4_mini": ChatModelProfile(
        profile_id="openai_gpt_5_4_mini",
        provider=ModelProvider.OPENAI,
        model="gpt-5.4-mini",
        display_name="GPT-5.4 Mini",
        max_tokens=8192,
        notes=(
            "Smaller GPT-5.4 series profile for cost-sensitive agent/tool UAT.",
        ),
        credential_source="openai",
    ),
    "openai_gpt_5_4_nano": ChatModelProfile(
        profile_id="openai_gpt_5_4_nano",
        provider=ModelProvider.OPENAI,
        model="gpt-5.4-nano",
        display_name="GPT-5.4 Nano",
        max_tokens=8192,
        notes=(
            "Lightweight GPT-5.4 series profile for low-latency candidate agent UAT.",
        ),
        credential_source="openai",
    ),
    "openai_gpt_4_1_nano": ChatModelProfile(
        profile_id="openai_gpt_4_1_nano",
        provider=ModelProvider.OPENAI,
        model="gpt-4.1-nano",
        display_name="GPT-4.1 nano",
        max_tokens=8192,
        notes=(
            "Low-cost non-reasoning compatibility fallback for tool-use tests.",
        ),
        credential_source="openai",
    ),
    "openai_gpt_4o_mini": ChatModelProfile(
        profile_id="openai_gpt_4o_mini",
        provider=ModelProvider.OPENAI,
        model="gpt-4o-mini",
        display_name="GPT-4o mini",
        max_tokens=8192,
        notes=(
            "Older low-cost OpenAI chat profile retained as a compatibility fallback.",
        ),
        credential_source="openai",
    ),
    "gemini_3_5_flash": ChatModelProfile(
        profile_id="gemini_3_5_flash",
        provider=ModelProvider.GOOGLE_GENAI,
        model="gemini-3.5-flash",
        display_name="Gemini 3.5 Flash",
        max_tokens=8192,
        notes=(
            "Newest listed Gemini Flash text profile from the direct Gemini API model list.",
        ),
    ),
    "gemini_3_1_flash_lite": ChatModelProfile(
        profile_id="gemini_3_1_flash_lite",
        provider=ModelProvider.GOOGLE_GENAI,
        model="gemini-3.1-flash-lite",
        display_name="Gemini 3.1 Flash Lite",
        max_tokens=8192,
        notes=(
            "Listed Gemini Flash-Lite text profile for direct Gemini API free-tier probing.",
        ),
    ),
    "gemini_3_1_flash_lite_preview": ChatModelProfile(
        profile_id="gemini_3_1_flash_lite_preview",
        provider=ModelProvider.GOOGLE_GENAI,
        model="gemini-3.1-flash-lite-preview",
        display_name="Gemini 3.1 Flash Lite Preview",
        max_tokens=8192,
    ),
    "gemini_3_flash_preview": ChatModelProfile(
        profile_id="gemini_3_flash_preview",
        provider=ModelProvider.GOOGLE_GENAI,
        model="gemini-3-flash-preview",
        display_name="Gemini 3 Flash Preview",
        max_tokens=8192,
    ),
    "gemini_3_pro_preview": ChatModelProfile(
        profile_id="gemini_3_pro_preview",
        provider=ModelProvider.GOOGLE_GENAI,
        model="gemini-3-pro-preview",
        display_name="Gemini 3 Pro Preview",
        max_tokens=8192,
        notes=(
            "Preview Pro profile; direct Gemini API free-tier quotas may be limited.",
        ),
    ),
    "gemini_3_1_pro_preview": ChatModelProfile(
        profile_id="gemini_3_1_pro_preview",
        provider=ModelProvider.GOOGLE_GENAI,
        model="gemini-3.1-pro-preview",
        display_name="Gemini 3.1 Pro Preview",
        max_tokens=8192,
        notes=(
            "Preview Pro profile; direct Gemini API free-tier quotas may be limited.",
        ),
    ),
    "gemini_flash_latest": ChatModelProfile(
        profile_id="gemini_flash_latest",
        provider=ModelProvider.GOOGLE_GENAI,
        model="gemini-flash-latest",
        display_name="Gemini Flash Latest",
        max_tokens=8192,
        notes=(
            "Alias profile from the direct Gemini API model list; resolved model may drift.",
        ),
    ),
    "gemini_flash_lite_latest": ChatModelProfile(
        profile_id="gemini_flash_lite_latest",
        provider=ModelProvider.GOOGLE_GENAI,
        model="gemini-flash-lite-latest",
        display_name="Gemini Flash-Lite Latest",
        max_tokens=8192,
        notes=(
            "Alias profile from the direct Gemini API model list; resolved model may drift.",
        ),
    ),
    "gemini_pro_latest": ChatModelProfile(
        profile_id="gemini_pro_latest",
        provider=ModelProvider.GOOGLE_GENAI,
        model="gemini-pro-latest",
        display_name="Gemini Pro Latest",
        max_tokens=8192,
        notes=(
            "Alias profile from the direct Gemini API model list; free-tier quotas may be limited.",
        ),
    ),
    "gemini_2_5_flash_lite": ChatModelProfile(
        profile_id="gemini_2_5_flash_lite",
        provider=ModelProvider.GOOGLE_GENAI,
        model="gemini-2.5-flash-lite",
        display_name="Gemini 2.5 Flash-Lite",
        max_tokens=8192,
        notes=(
            "Fastest/cost-conscious Gemini text profile; useful first probe for free-tier limits.",
        ),
    ),
    "gemini_2_5_flash": ChatModelProfile(
        profile_id="gemini_2_5_flash",
        provider=ModelProvider.GOOGLE_GENAI,
        model="gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        max_tokens=8192,
        notes=(
            "Balanced Gemini profile for tool-use UAT on the direct Gemini API.",
        ),
    ),
    "gemini_2_5_pro": ChatModelProfile(
        profile_id="gemini_2_5_pro",
        provider=ModelProvider.GOOGLE_GENAI,
        model="gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        max_tokens=8192,
        notes=(
            "Stronger Gemini profile; free-tier quotas may be lower than Flash variants.",
        ),
    ),
    "gemini_2_0_flash": ChatModelProfile(
        profile_id="gemini_2_0_flash",
        provider=ModelProvider.GOOGLE_GENAI,
        model="gemini-2.0-flash",
        display_name="Gemini 2.0 Flash",
        max_tokens=8192,
        notes=(
            "Older Flash profile retained as a compatibility fallback for free-tier testing.",
        ),
    ),
    "bedrock_qwen3_next_80b_a3b_ap_south_1": ChatModelProfile(
        profile_id="bedrock_qwen3_next_80b_a3b_ap_south_1",
        provider=ModelProvider.BEDROCK_CONVERSE,
        model="qwen.qwen3-next-80b-a3b",
        display_name="Qwen3 Next 80B A3B on Bedrock Converse",
        region_name="ap-south-1",
        max_tokens=8192,
        is_moe=True,
        notes=(
            "AWS lists this runtime model as 80B total and 3B active parameters.",
            "Use the Mantle profile if Converse tool-calling is unavailable for the account.",
        ),
    ),
    "bedrock_qwen3_coder_480b_a35b_ap_south_1": ChatModelProfile(
        profile_id="bedrock_qwen3_coder_480b_a35b_ap_south_1",
        provider=ModelProvider.BEDROCK_CONVERSE,
        model="qwen.qwen3-coder-480b-a35b-v1:0",
        display_name="Qwen3 Coder 480B A35B on Bedrock Converse",
        region_name="ap-south-1",
        max_tokens=8192,
        is_moe=True,
        notes=(
            "Larger Qwen Coder MoE profile for testing stronger open-model SQL/tool behavior.",
        ),
    ),
    "bedrock_gemma_3_12b_it_ap_south_1": ChatModelProfile(
        profile_id="bedrock_gemma_3_12b_it_ap_south_1",
        provider=ModelProvider.BEDROCK_CONVERSE,
        model="google.gemma-3-12b-it",
        display_name="Gemma 3 12B IT on Bedrock Converse",
        region_name="ap-south-1",
        max_tokens=8192,
        notes=(
            "Small open model profile for low-cost SQL-agent harness testing.",
        ),
        credential_source="bedrock",
    ),
    "bedrock_openai_gpt_oss_120b_ap_south_1": ChatModelProfile(
        profile_id="bedrock_openai_gpt_oss_120b_ap_south_1",
        provider=ModelProvider.BEDROCK_CONVERSE,
        model="openai.gpt-oss-120b-1:0",
        display_name="OpenAI GPT-OSS 120B on Bedrock Converse",
        region_name="ap-south-1",
        max_tokens=8192,
        notes=(
            "Open-weight OpenAI model served through Bedrock for SQL-agent harness testing.",
        ),
        credential_source="bedrock",
    ),
    "bedrock_zai_glm_5_ap_south_1": ChatModelProfile(
        profile_id="bedrock_zai_glm_5_ap_south_1",
        provider=ModelProvider.BEDROCK_CONVERSE,
        model="zai.glm-5",
        display_name="Z.ai GLM-5 on Bedrock Converse",
        region_name="ap-south-1",
        max_tokens=8192,
        notes=(
            "GLM open-model profile for SQL-agent harness testing.",
        ),
        credential_source="bedrock",
    ),
    "bedrock_meta_llama3_70b_instruct_ap_south_1": ChatModelProfile(
        profile_id="bedrock_meta_llama3_70b_instruct_ap_south_1",
        provider=ModelProvider.BEDROCK_CONVERSE,
        model="meta.llama3-70b-instruct-v1:0",
        display_name="Meta Llama 3 70B Instruct on Bedrock Converse",
        region_name="ap-south-1",
        max_tokens=2048,
        supports_tool_use=False,
        notes=(
            "Llama 3 70B Instruct profile for SQL-agent harness testing.",
        ),
        credential_source="bedrock",
    ),
    "bedrock_kimi_k2_thinking_ap_south_1": ChatModelProfile(
        profile_id="bedrock_kimi_k2_thinking_ap_south_1",
        provider=ModelProvider.BEDROCK_CONVERSE,
        model="moonshot.kimi-k2-thinking",
        display_name="Moonshot Kimi K2 Thinking on Bedrock Converse",
        region_name="ap-south-1",
        max_tokens=8192,
        notes=(
            "Reasoning-oriented Kimi profile for SQL-agent harness testing.",
        ),
        credential_source="bedrock",
    ),
    "bedrock_deepseek_v32_ap_south_1": ChatModelProfile(
        profile_id="bedrock_deepseek_v32_ap_south_1",
        provider=ModelProvider.BEDROCK_CONVERSE,
        model="deepseek.v3.2",
        display_name="DeepSeek V3.2 on Bedrock Converse",
        region_name="ap-south-1",
        max_tokens=8192,
        notes=(
            "DeepSeek profile for SQL-agent harness testing.",
        ),
        credential_source="bedrock",
    ),
    "bedrock_nvidia_nemotron_super_3_120b_ap_south_1": ChatModelProfile(
        profile_id="bedrock_nvidia_nemotron_super_3_120b_ap_south_1",
        provider=ModelProvider.BEDROCK_CONVERSE,
        model="nvidia.nemotron-super-3-120b",
        display_name="Nvidia Nemotron Super 3 120B on Bedrock Converse",
        region_name="ap-south-1",
        max_tokens=8192,
        notes=(
            "Nemotron profile for SQL-agent harness testing; smoke latency may be high.",
        ),
        credential_source="bedrock",
    ),
    "bedrock_mantle_qwen3_next_80b_a3b_ap_south_1": ChatModelProfile(
        profile_id="bedrock_mantle_qwen3_next_80b_a3b_ap_south_1",
        provider=ModelProvider.OPENAI,
        model="qwen.qwen3-next-80b-a3b-instruct",
        display_name="Qwen3 Next 80B A3B on Bedrock Mantle",
        region_name="ap-south-1",
        base_url="https://bedrock-mantle.ap-south-1.api.aws/v1",
        max_tokens=8192,
        is_moe=True,
        notes=(
            "AWS lists Bedrock Mantle as the OpenAI-compatible endpoint with client-side tool calling.",
        ),
        credential_source="bedrock",
    ),
}


class ChatModelFactory:
    """Build LangChain chat models from DiracData settings and named profiles."""

    def __init__(
        self,
        *,
        settings: DiracDataSettings,
        profiles: dict[str, ChatModelProfile] | None = None,
    ) -> None:
        self.settings = settings
        self.profiles = profiles or BUILT_IN_MODEL_PROFILES

    def available_profiles(self) -> list[ChatModelProfile]:
        return sorted(self.profiles.values(), key=lambda profile: profile.profile_id)

    def resolve_agent_model(self) -> ResolvedChatModel:
        profile = self._selected_profile()
        if profile is None:
            provider = ModelProvider(self.settings.agent_llm_provider.lower())
            return ResolvedChatModel(
                profile_id=None,
                provider=provider,
                model=self.settings.agent_llm_model,
                max_tokens=self.settings.agent_llm_max_tokens,
                temperature=self.settings.agent_llm_temperature,
                region_name=_resolved_provider_region(
                    provider,
                    bedrock_region=self.settings.bedrock_region,
                ),
                base_url=self._provider_base_url(provider),
                credential_source=_default_credential_source(provider),
            )

        region = _resolved_profile_region(
            profile,
            bedrock_region=self.settings.bedrock_region,
        )
        base_url = _profile_base_url(profile, region_name=region)
        return ResolvedChatModel(
            profile_id=profile.profile_id,
            provider=profile.provider,
            model=profile.model,
            max_tokens=_resolved_max_tokens(
                settings_value=self.settings.agent_llm_max_tokens,
                profile_value=profile.max_tokens,
            ),
            temperature=self.settings.agent_llm_temperature
            if self.settings.agent_llm_temperature is not None
            else profile.temperature or 0.0,
            region_name=region,
            base_url=base_url,
            supports_tool_use=profile.supports_tool_use,
            supports_streaming=profile.supports_streaming,
            is_moe=profile.is_moe,
            model_kwargs=dict(profile.model_kwargs),
            credential_source=profile.credential_source,
        )

    def create_agent_chat_model(self) -> object:
        resolved = self.resolve_agent_model()
        _validate_required_secrets(self.settings, resolved)
        kwargs: dict[str, Any] = dict(resolved.model_kwargs)
        if resolved.provider == ModelProvider.ANTHROPIC:
            kwargs["base_url"] = self.settings.anthropic_base_url
        if resolved.provider == ModelProvider.BEDROCK_CONVERSE and resolved.region_name:
            kwargs["region_name"] = resolved.region_name
        if resolved.provider == ModelProvider.OPENAI and resolved.base_url:
            kwargs["base_url"] = resolved.base_url
        api_key = _api_key_for_provider(self.settings, resolved)
        if api_key is not None:
            kwargs["api_key"] = api_key

        with _provider_environment(settings=self.settings, resolved=resolved):
            return init_langchain_chat_model(
                model=resolved.model,
                model_provider=resolved.provider.value,
                max_tokens=resolved.max_tokens,
                temperature=resolved.temperature,
                **kwargs,
            )

    def _selected_profile(self) -> ChatModelProfile | None:
        profile_id = self.settings.agent_model_profile
        if not profile_id:
            return None
        profile = self.profiles.get(profile_id)
        if profile is None:
            available = ", ".join(sorted(self.profiles))
            raise ValueError(
                f"Unknown DIRACDATA_AGENT_MODEL_PROFILE={profile_id!r}. "
                f"Available profiles: {available}"
            )
        return profile

    def _provider_base_url(self, provider: ModelProvider) -> str | None:
        if provider == ModelProvider.ANTHROPIC:
            return self.settings.anthropic_base_url
        return None


def agent_chat_model_from_settings(settings: DiracDataSettings) -> object:
    """Create the answer-time LangChain chat model using the model factory."""
    return ChatModelFactory(settings=settings).create_agent_chat_model()


def _resolved_max_tokens(
    *,
    settings_value: int | None,
    profile_value: int | None,
) -> int:
    if settings_value is None:
        return profile_value or 8192
    if profile_value is None:
        return settings_value
    return min(settings_value, profile_value)


def _resolved_profile_region(
    profile: ChatModelProfile,
    *,
    bedrock_region: str | None,
) -> str | None:
    if profile.provider == ModelProvider.BEDROCK_CONVERSE:
        return bedrock_region or profile.region_name
    if profile.provider == ModelProvider.OPENAI and profile.base_url and profile.region_name:
        return bedrock_region or profile.region_name
    return profile.region_name


def _resolved_provider_region(
    provider: ModelProvider,
    *,
    bedrock_region: str | None,
) -> str | None:
    if provider == ModelProvider.BEDROCK_CONVERSE:
        return bedrock_region
    return None


def _default_credential_source(provider: ModelProvider) -> str:
    if provider == ModelProvider.OPENAI:
        return "openai"
    return "default"


def _profile_base_url(profile: ChatModelProfile, *, region_name: str | None) -> str | None:
    if not profile.base_url:
        return None
    if region_name is None:
        return profile.base_url
    return profile.base_url.replace(f".{profile.region_name}.", f".{region_name}.")


def _validate_required_secrets(settings: DiracDataSettings, resolved: ResolvedChatModel) -> None:
    if resolved.provider == ModelProvider.ANTHROPIC and not settings.anthropic_api_key:
        raise ValueError("DIRACDATA_ANTHROPIC_API_KEY is required for Anthropic chat models")
    if resolved.provider == ModelProvider.GOOGLE_GENAI and not _api_key_for_provider(
        settings,
        resolved,
    ):
        raise ValueError(
            "DIRACDATA_GOOGLE_API_KEY, GOOGLE_API_KEY, or GEMINI_API_KEY "
            "is required for Google Gemini chat models"
        )
    if resolved.provider == ModelProvider.OPENAI and not _api_key_for_provider(settings, resolved):
        if resolved.credential_source == "bedrock":
            raise ValueError(
                "AWS_BEARER_TOKEN_BEDROCK or DIRACDATA_BEDROCK_API_KEY "
                "is required for Bedrock OpenAI-compatible chat model profiles"
            )
        raise ValueError(
            "DIRACDATA_OPENAI_API_KEY or OPENAI_API_KEY is required for OpenAI chat models"
        )


def _api_key_for_provider(
    settings: DiracDataSettings,
    resolved: ResolvedChatModel,
) -> str | None:
    if resolved.provider == ModelProvider.ANTHROPIC:
        return settings.anthropic_api_key
    if resolved.provider == ModelProvider.OPENAI:
        if resolved.credential_source == "bedrock":
            return os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or settings.bedrock_api_key
        return settings.openai_api_key or os.environ.get("OPENAI_API_KEY")
    if resolved.provider == ModelProvider.GOOGLE_GENAI:
        return (
            settings.google_api_key
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )
    if resolved.provider == ModelProvider.BEDROCK_CONVERSE:
        return settings.bedrock_api_key
    return None


@contextmanager
def _provider_environment(
    *,
    settings: DiracDataSettings,
    resolved: ResolvedChatModel,
) -> Iterator[None]:
    env_restore: dict[str, str | None] = {}
    if resolved.provider == ModelProvider.ANTHROPIC and settings.anthropic_api_key:
        env_restore["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key

    if resolved.provider == ModelProvider.OPENAI:
        api_key = _api_key_for_provider(settings, resolved)
        if api_key is not None and not os.environ.get("OPENAI_API_KEY"):
            env_restore["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY")
            os.environ["OPENAI_API_KEY"] = api_key
    if resolved.provider == ModelProvider.GOOGLE_GENAI:
        api_key = _api_key_for_provider(settings, resolved)
        if api_key is not None and not os.environ.get("GOOGLE_API_KEY"):
            env_restore["GOOGLE_API_KEY"] = os.environ.get("GOOGLE_API_KEY")
            os.environ["GOOGLE_API_KEY"] = api_key

    try:
        yield
    finally:
        for key, value in env_restore.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
