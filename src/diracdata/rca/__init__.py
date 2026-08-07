"""Metric root-cause. `attribution` is the ONE primitive -- a complete, reconciled, cited decomposition
of a metric's change (driver-tree graph walk + per-dimension attribution), consumed by the agent for
judgment (hypothesis, verification, narration). `kernels` is the pure attribution math it splits with."""

from diracdata.rca.attribution import (
    AttributionResult,
    attribute as attribute_metric,
    build_attribution_tool,
    default_dimensions,
    seed_attribution,
)
from diracdata.rca.kernels import (
    Contribution,
    adtributor,
    attribute,
    attribute_additive,
    attribute_multiplicative,
    attribute_ratio,
)

__all__ = [
    "AttributionResult",
    "attribute_metric",
    "build_attribution_tool",
    "default_dimensions",
    "seed_attribution",
    "Contribution",
    "adtributor",
    "attribute",
    "attribute_additive",
    "attribute_multiplicative",
    "attribute_ratio",
]
