"""Model construction + caching. The ONE place chat models are built, keyed by profile + sampling
overrides, so a per-stage/per-query model choice never rebuilds the same model twice.

Depends only on `diracdata.config` and `diracdata.utils.model_factory` -- nothing from agents/memory.
"""

from diracdata.models.registry import ModelBuilder, ModelRegistry, StageModel

__all__ = ["ModelRegistry", "StageModel", "ModelBuilder"]
