"""Learn-time boundary-convention heuristics for threshold / bucket columns."""
from __future__ import annotations

import re
from typing import Any


_THRESHOLD_NAME = re.compile(
    r"(threshold|cutoff|bucket|bin_edge|upper_bound|lower_bound|max_val|min_val)",
    re.I,
)


def detect_boundary_convention(
    column_name: str,
    sample_values: list[Any] | None = None,
    *,
    profile_hint: str | None = None,
) -> dict[str, Any]:
    """Infer inclusive/exclusive boundary convention for bucket edges.

    Returns {applies, convention, confidence, notes}.
    convention ∈ {left_closed_right_open, left_open_right_closed, inclusive, unknown}.
    """
    name = column_name or ""
    applies = bool(_THRESHOLD_NAME.search(name) or (profile_hint and "bucket" in profile_hint.lower()))
    if not applies:
        return {
            "applies": False,
            "convention": None,
            "confidence": 0.0,
            "notes": "column name does not look like a threshold/bucket edge",
        }

    vals = [v for v in (sample_values or []) if v is not None]
    notes = []
    # Heuristic: sorted numeric edges with 0/1 style flags often mean half-open intervals
    nums = []
    for v in vals:
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            continue
    convention = "left_closed_right_open"  # analytics default (pandas cut)
    confidence = 0.4
    if nums:
        notes.append(f"saw {len(nums)} numeric edge samples; defaulting to [low, high)")
        confidence = 0.55
    if re.search(r"exclusive|right_open|half.open", profile_hint or "", re.I):
        convention = "left_closed_right_open"
        confidence = 0.8
        notes.append("hint suggests right-open intervals")
    if re.search(r"inclusive", profile_hint or "", re.I):
        convention = "inclusive"
        confidence = 0.75
        notes.append("hint suggests inclusive bounds")

    return {
        "applies": True,
        "convention": convention,
        "confidence": confidence,
        "notes": "; ".join(notes) if notes else "default analytics half-open interval",
        "column": name,
    }


def enrich_metric_boundary(metric: dict[str, Any], convention: str) -> dict[str, Any]:
    out = dict(metric)
    out["boundary_convention"] = convention
    return out
