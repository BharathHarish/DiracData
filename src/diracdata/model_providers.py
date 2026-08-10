"""diracdata.model_providers -- pick a model with a provider class, api_key from arg or ENV.

    from diracdata.model_providers import FireworksAI, Anthropic, OpenAI, Bedrock
    model = FireworksAI("deepseek-v4-flash")            # api_key -> DIRACDATA_FIREWORKS_API_KEY
    model = Anthropic("haiku", api_key="sk-ant-...")    # or pass it explicitly

    model.chat("hello")     # raw single-turn chat (the chat.completions analog)
    model.build()           # the underlying chat model

Each provider accepts a friendly alias OR a raw profile id. Add a model = a new alias / a new
ChatModelProfile in diracdata.models.factory; nothing else changes.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any


class _Provider:
    _key_field = ""            # the Config field that holds this provider's api_key
    _aliases: dict = {}        # friendly alias -> built-in profile id

    def __init__(self, model: str, *, api_key: str | None = None) -> None:
        self.profile_id = self._aliases.get(model, model)
        self.api_key = api_key

    def apply(self, settings: Any) -> Any:
        """Return settings with this provider's profile (and api_key, if given) applied."""
        s = replace(settings, agent_model_profile=self.profile_id)
        if self.api_key:
            s = replace(s, **{self._key_field: self.api_key})
        return s

    def build(self, settings: Any = None) -> object:
        from diracdata.config import settings_from_env
        from diracdata.models import chat_model
        s = self.apply(settings if settings is not None else settings_from_env())
        return chat_model(self.profile_id, settings=s)

    def chat(self, prompt: str, *, settings: Any = None) -> str:
        return self.build(settings).invoke(prompt).content


class FireworksAI(_Provider):
    _key_field = "fireworks_api_key"
    _aliases = {"deepseek-v4-flash": "fireworks_deepseek_v4_flash",
                "gpt-oss-120b": "fireworks_gpt_oss_120b",
                "glm-5p2": "fireworks_glm_5p2",
                "kimi-k2p7": "fireworks_kimi_k2p7_code",
                "minimax-m3": "fireworks_minimax_m3",
                "qwen3p7-plus": "fireworks_qwen3p7_plus"}


class Anthropic(_Provider):
    _key_field = "anthropic_api_key"
    _aliases = {"haiku": "anthropic_haiku_45", "sonnet-5": "anthropic_sonnet_5"}


class OpenAI(_Provider):
    _key_field = "openai_api_key"
    _aliases = {"gpt-5-mini": "openai_gpt_5_4_mini", "gpt-5-nano": "openai_gpt_5_4_nano"}


class Bedrock(_Provider):
    _key_field = "aws_secret_access_key"   # Bedrock auth is via AWS creds/region in Config
    _aliases: dict = {}
