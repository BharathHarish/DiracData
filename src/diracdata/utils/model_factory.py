"""Back-compat shim: the model factory now lives in `diracdata.models.factory`.

Import from `diracdata.models` in new code. This re-export keeps existing imports working.
"""

from diracdata.models.factory import (  # noqa: F401
    BUILT_IN_MODEL_PROFILES,
    ChatModelFactory,
    ChatModelProfile,
    FIREWORKS_COST_AWARE_GARDEN,
    ModelProvider,
    build_model_init,
    garden_profiles,
    init_chat_model,
    model_catalog,
    render_catalog,
)

__all__ = ["ModelProvider", "ChatModelProfile", "BUILT_IN_MODEL_PROFILES", "FIREWORKS_COST_AWARE_GARDEN",
           "ChatModelFactory", "build_model_init", "init_chat_model", "garden_profiles",
           "model_catalog", "render_catalog"]
