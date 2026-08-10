"""Model factory: build a LangChain chat model from a named profile + Config. The single entry the
app uses is `ChatModelFactory(settings=...).create_chat_model(profile_id=...)`; `build_model_init` is
split out as a pure function so the determinism floor (pinned temperature, seed forwarding) is
unit-testable without a live provider.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from diracdata.config import Config


class ModelProvider(StrEnum):
    ANTHROPIC = "anthropic"
    BEDROCK_CONVERSE = "bedrock_converse"
    OPENAI = "openai"
    FIREWORKS = "fireworks"           # OpenAI-COMPATIBLE -- driven through the OpenAI transport + base_url


# Providers that speak the OpenAI wire protocol -> built with model_provider="openai" + a base_url.
# Add any other OpenAI-compatible service here (Together, Groq, ...) and it drops in with just a key.
_OPENAI_COMPATIBLE = {ModelProvider.FIREWORKS}


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
    # --- catalog facts (for the agentic router to reason over) ---
    cost_tier: str = "unknown"        # free | low | mid | high  (relative $/token)
    capability: str = "standard"      # light | standard | strong | frontier
    supports_tools: bool = True        # can drive the tool loop (Bedrock Converse tool-use etc.)
    supports_reasoning: bool = False   # surfaces native reasoning/thinking
    note: str = ""                     # one-line hint for the router


# The MODEL GARDEN -- four tiers the router chooses among (cheapest-correct + escalation). Nano (basic)
# < Mini (standard) < Haiku (strong) < Sonnet 5 (frontier). The router reads cost_tier + capability +
# note to pick per query; frontier is reserved for the hardest cold/novel work where correctness rules.
BUILT_IN_MODEL_PROFILES: dict[str, ChatModelProfile] = {
    "anthropic_sonnet_5": ChatModelProfile(
        "anthropic_sonnet_5",
        ModelProvider.ANTHROPIC,
        "claude-sonnet-5",
        "Claude Sonnet 5",
        cost_tier="high", capability="frontier", supports_tools=True, supports_reasoning=True,
        note="TOP tier -- reserve for the HARDEST cold/novel root-cause + multi-metric decomposition, "
             "or to ESCALATE when a strong model could not converge; correctness dominates cost here",
    ),
    "anthropic_haiku_45": ChatModelProfile(
        "anthropic_haiku_45",
        ModelProvider.ANTHROPIC,
        "claude-haiku-4-5-20251001",
        "Claude Haiku 4.5",
        cost_tier="mid", capability="strong", supports_tools=True, supports_reasoning=False,
        note="STRONG all-rounder -- use for COMPLEX / cold / root-cause (RCA) / multi-step decomposition "
             "where correctness matters; escalate to the frontier tier only if it cannot converge",
    ),
    "openai_gpt_5_4_mini": ChatModelProfile(
        "openai_gpt_5_4_mini",
        ModelProvider.OPENAI,
        "gpt-5.4-mini",
        "GPT-5.4 Mini",
        credential_source="openai",
        cost_tier="low", capability="standard", supports_tools=True, supports_reasoning=True,
        note="all-rounder for SMALL / MEDIUM analytics (multi-join, cohort); prefer when a good "
             "experience/precedent match exists to adapt",
    ),
    "openai_gpt_5_4_nano": ChatModelProfile(
        "openai_gpt_5_4_nano",
        ModelProvider.OPENAI,
        "gpt-5.4-nano",
        "GPT-5.4 Nano",
        credential_source="openai",
        cost_tier="low", capability="basic", supports_tools=True, supports_reasoning=True,
        note="cheapest -- use for SIMPLE single-fact lookups / single-metric counts / strongly "
             "precedented queries; escalate if it cannot converge",
    ),
    # Fireworks garden (OpenAI-compatible). CHEAP-FIRST thesis (Fireworks list $/1M out):
    #   Start even cold RCA on DeepSeek Flash ($0.28). Escalate inside the active garden only when a
    #   cheaper model fails to converge. Active UAT garden = Flash + GPT-OSS + MiniMax M2.7/M3 +
    #   Nemotron Ultra. Kimi/GLM (~$4+) stay registered for later escalation recording, NOT the
    #   default garden. Never use Kimi K3 (~$15/M out).
    "fireworks_deepseek_v4_flash": ChatModelProfile(
        "fireworks_deepseek_v4_flash", ModelProvider.FIREWORKS,
        "accounts/fireworks/models/deepseek-v4-flash-0731",
        "DeepSeek V4 Flash (Fireworks)", cost_tier="low", capability="basic",
        supports_tools=True, supports_reasoning=True,
        note="~$0.28/M out -- DEFAULT first pick for ALMOST EVERYTHING including cold RCA / metric "
             "decomposition (proven on retail RCA); give a generous step budget on hard tasks; "
             "escalate only if it fails to converge",
    ),
    "fireworks_gpt_oss_120b": ChatModelProfile(
        "fireworks_gpt_oss_120b", ModelProvider.FIREWORKS, "accounts/fireworks/models/gpt-oss-120b",
        "GPT-OSS 120B (Fireworks)", cost_tier="low", capability="basic",
        supports_tools=True, supports_reasoning=True,
        note="~$0.60/M out -- cheap alternate for SIMPLE / strongly precedented lookups; "
             "prefer DeepSeek Flash first when both fit",
    ),
    "fireworks_minimax_m2p7": ChatModelProfile(
        "fireworks_minimax_m2p7", ModelProvider.FIREWORKS,
        "accounts/fireworks/models/minimax-m2p7",
        "MiniMax M2.7 (Fireworks)", cost_tier="mid", capability="standard",
        supports_tools=True, supports_reasoning=True,
        note="~$1.20/M out -- mid escalate when Flash/OSS cannot converge on multi-join / cohort / "
             "agentic tool loops; still cheap vs Nemotron",
    ),
    "fireworks_minimax_m3": ChatModelProfile(
        "fireworks_minimax_m3", ModelProvider.FIREWORKS, "accounts/fireworks/models/minimax-m3",
        "MiniMax M3 (Fireworks)", cost_tier="mid", capability="standard",
        supports_tools=True, supports_reasoning=True,
        note="~$1.20/M out -- mid escalate alternate to M2.7 for medium analytics / tool-heavy turns",
    ),
    "fireworks_nemotron_3_ultra": ChatModelProfile(
        "fireworks_nemotron_3_ultra", ModelProvider.FIREWORKS,
        "accounts/fireworks/models/nemotron-3-ultra-nvfp4",
        "NVIDIA Nemotron 3 Ultra NVFP4 (Fireworks)", cost_tier="mid", capability="strong",
        supports_tools=True, supports_reasoning=True,
        note="~$2.40/M out -- strongest model IN the active cheap-first garden; use only when Flash/"
             "MiniMax failed to converge on hard RCA, or as the first escalate for the hardest cold work",
    ),
    # Registered for later high-cost escalation experiments -- NOT in FIREWORKS_COST_AWARE_GARDEN.
    "fireworks_kimi_k2p7_code": ChatModelProfile(
        "fireworks_kimi_k2p7_code", ModelProvider.FIREWORKS,
        "accounts/fireworks/models/kimi-k2p7-code",
        "Kimi K2.7 Code (Fireworks)", cost_tier="high", capability="strong",
        supports_tools=True, supports_reasoning=True,
        note="~$4.00/M out -- HIGH-COST escalate only after Nemotron/Flash fail; not in default garden",
    ),
    "fireworks_glm_5p2": ChatModelProfile(
        "fireworks_glm_5p2",
        ModelProvider.FIREWORKS,
        "accounts/fireworks/models/glm-5p2",
        "GLM-5.2 (Fireworks)",
        cost_tier="high", capability="strong", supports_tools=True, supports_reasoning=True,
        note="~$4.40/M out -- HIGH-COST escalate only after cheaper garden models fail; not in default garden",
    ),
    "fireworks_qwen3p7_plus": ChatModelProfile(
        "fireworks_qwen3p7_plus", ModelProvider.FIREWORKS, "accounts/fireworks/models/qwen3p7-plus",
        "Qwen3.7 Plus (Fireworks)", cost_tier="mid", capability="standard",
        supports_tools=True, supports_reasoning=True,
        note="~$1.60/M out -- mid-tier Fireworks option (not in the default cheap-first UAT garden)",
    ),
}

# Active cheap-first UAT garden: try Flash (even on RCA) first; escalate inside this set only.
FIREWORKS_COST_AWARE_GARDEN: tuple[str, ...] = (
    "fireworks_deepseek_v4_flash",
    "fireworks_gpt_oss_120b",
    "fireworks_minimax_m2p7",
    "fireworks_minimax_m3",
    "fireworks_nemotron_3_ultra",
)


def garden_profiles(garden: list[str] | tuple[str, ...] | None = None
                    ) -> dict[str, ChatModelProfile]:
    """Subset of BUILT_IN_MODEL_PROFILES the router may choose among. Empty/None -> full catalog."""
    if not garden:
        return BUILT_IN_MODEL_PROFILES
    out: dict[str, ChatModelProfile] = {}
    for pid in garden:
        p = BUILT_IN_MODEL_PROFILES.get(pid)
        if p is not None:
            out[pid] = p
    return out or BUILT_IN_MODEL_PROFILES


def model_catalog(profiles: dict[str, ChatModelProfile] | None = None) -> list[dict[str, Any]]:
    """The list of models + characteristics + cost the agentic router reasons over."""
    profiles = profiles if profiles is not None else BUILT_IN_MODEL_PROFILES
    return [{"id": p.profile_id, "family": p.provider.value, "cost": p.cost_tier,
             "capability": p.capability, "tools": p.supports_tools,
             "reasoning": p.supports_reasoning, "note": p.note} for p in profiles.values()]


def render_catalog(profiles: dict[str, ChatModelProfile] | None = None) -> str:
    """A compact human/LLM-readable catalog for the routing prompt."""
    lines = []
    for m in model_catalog(profiles):
        lines.append(
            f"- {m['id']} | family={m['family']} | cost={m['cost']} | capability={m['capability']} "
            f"| tools={'yes' if m['tools'] else 'NO'} | reasoning={'yes' if m['reasoning'] else 'no'} "
            f"| {m['note']}")
    return "\n".join(lines)


class ChatModelFactory:
    def __init__(self, *, settings: Config) -> None:
        self.settings = settings

    def create_chat_model(self, *, profile_id: str | None = None) -> object:
        provider, api_key, region_name, init_kwargs = build_model_init(
            settings=self.settings, profile_id=profile_id
        )
        _validate(provider=provider, api_key=api_key, region_name=region_name)
        with _provider_environment(settings=self.settings, provider=provider, api_key=api_key):
            return init_chat_model(**init_kwargs)


def build_model_init(
    *, settings: Config, profile_id: str | None = None
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
    elif provider == ModelProvider.FIREWORKS:
        # OpenAI-compatible: the OpenAI transport (below) + the Fireworks key + endpoint.
        api_key = settings.fireworks_api_key
        kwargs["base_url"] = (profile.base_url if profile and profile.base_url
                              else settings.fireworks_base_url)
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
    # OpenAI-compatible providers (Fireworks, ...) are built with the OpenAI transport + a base_url,
    # so LangChain uses ChatOpenAI against their endpoint -- no per-provider client needed.
    transport = "openai" if provider in _OPENAI_COMPATIBLE else provider.value
    init_kwargs = dict(
        model=model,
        model_provider=transport,
        max_tokens=max_tokens,
        temperature=temperature,
        **kwargs,
    )
    return provider, api_key, region_name, init_kwargs


def init_chat_model(**kwargs: Any) -> object:
    try:
        from langchain.chat_models import init_chat_model as lc_init_chat_model
    except ImportError as exc:
        raise RuntimeError("langchain chat model integrations are required") from exc
    return lc_init_chat_model(**kwargs)


def _validate(*, provider: ModelProvider, api_key: str | None, region_name: str | None) -> None:
    if provider in {ModelProvider.ANTHROPIC, ModelProvider.OPENAI, ModelProvider.FIREWORKS} and not api_key:
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
    settings: Config,
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
    if provider in _OPENAI_COMPATIBLE and api_key:      # OpenAI transport reads OPENAI_API_KEY as fallback
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
