"""diracdata.models -- the model layer: a provider-agnostic FACTORY (profile -> a chat model) plus a
caching REGISTRY (per-stage / per-query choices never rebuild the same model twice).

Quick start -- import any model by profile and chat, provider chosen by config/ENV:

    from diracdata.models import chat_model
    m = chat_model("fireworks_deepseek_v4_flash")     # or None -> DIRACDATA_AGENT_MODEL_PROFILE
    m.invoke("hello")

The factory reads provider creds + base URLs from `Config` (ENV via settings_from_env). Adding a
provider/model = a new `ChatModelProfile` in factory.py; nothing else changes.
"""

from __future__ import annotations

from typing import Any

from diracdata.models.factory import (
    BUILT_IN_MODEL_PROFILES,
    ChatModelFactory,
    ChatModelProfile,
    ModelProvider,
    build_model_init,
    garden_profiles,
    model_catalog,
    render_catalog,
)
from diracdata.models.registry import ModelBuilder, ModelRegistry, StageModel

__all__ = [
    "chat_model", "ChatModelFactory", "ChatModelProfile", "ModelProvider",
    "BUILT_IN_MODEL_PROFILES", "garden_profiles", "model_catalog", "render_catalog",
    "build_model_init", "ModelRegistry", "StageModel", "ModelBuilder",
]


def chat_model(profile_id: str | None = None, *, settings: Any = None) -> object:
    """Build one chat model by profile id (or the config default DIRACDATA_AGENT_MODEL_PROFILE when
    None). `settings` is a Config; if omitted, load it from the environment. One call to start chatting."""
    if settings is None:
        from diracdata.config import settings_from_env
        settings = settings_from_env()
    return ChatModelFactory(settings=settings).create_chat_model(profile_id=profile_id)
