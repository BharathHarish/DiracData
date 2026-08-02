"""ModelRegistry -- build chat models on demand and cache them by (profile, max_tokens, temperature,
reasoning_effort). The builder is injectable so callers (and tests) can construct models without a
live provider; the default builder wraps the existing ChatModelFactory.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from diracdata.config import Config, Stage
from diracdata.utils.model_factory import BUILT_IN_MODEL_PROFILES, ChatModelFactory

# builder(config, profile_id, max_tokens, temperature, reasoning_effort) -> a bound chat model
ModelBuilder = Callable[[Config, str, int | None, float | None, str | None], Any]


@dataclass(frozen=True)
class StageModel:
    model: Any
    profile_id: str
    provider: str        # drives streaming-adapter selection in a later phase


def _default_builder(config: Config, profile_id: str, max_tokens: int | None,
                     temperature: float | None, reasoning_effort: str | None) -> Any:
    """Build via ChatModelFactory, overriding only the sampling knobs that were given. `reasoning_effort`
    is accepted for a later phase and is not yet wired into the provider request."""
    overrides: dict[str, Any] = {"agent_model_profile": profile_id}
    if max_tokens is not None:
        overrides["agent_llm_max_tokens"] = max_tokens
    if temperature is not None:
        overrides["agent_llm_temperature"] = temperature
    settings = replace(config, **overrides)
    return ChatModelFactory(settings=settings).create_chat_model(profile_id=profile_id)


def _provider_of(config: Config, profile_id: str) -> str:
    profile = BUILT_IN_MODEL_PROFILES.get(profile_id)
    return profile.provider.value if profile else config.agent_llm_provider


class ModelRegistry:
    def __init__(self, config: Config, *, builder: ModelBuilder | None = None) -> None:
        self._config = config
        self._builder = builder or _default_builder
        self._cache: dict[tuple, StageModel] = {}

    def get(self, profile_id: str, *, max_tokens: int | None = None,
            temperature: float | None = None, reasoning_effort: str | None = None) -> StageModel:
        key = (profile_id, max_tokens, temperature, reasoning_effort)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        model = self._builder(self._config, profile_id, max_tokens, temperature, reasoning_effort)
        stage_model = StageModel(model=model, profile_id=profile_id,
                                 provider=_provider_of(self._config, profile_id))
        self._cache[key] = stage_model
        return stage_model

    def for_stage(self, stage: Stage) -> StageModel:
        r = self._config.resolve_stage(stage)
        return self.get(r.profile_id, max_tokens=r.max_tokens, temperature=r.temperature,
                        reasoning_effort=r.reasoning_effort)
