"""Pure attribution kernels for metric root-cause -- no DB, no LLM, just the EXACT arithmetic that
splits a metric's period-over-period change into its drivers.

Three kinds cover the whole space (proportion / binary / rate fold into `ratio`):
  - additive        M = A + B + C ...   contribution(child) = child's own delta
  - multiplicative  M = A x B           symmetric split (no interaction residual)
  - ratio           M = N / D           numerator vs denominator split

Every split RECONCILES to the parent's delta with ~zero residual, so an agent verifies by a glance,
not by re-deriving. Plus `adtributor`: rank a dimension's slices by surprise (KL on share shift) x
explanatory power (share of the total delta) -- the deterministic "where did it concentrate" pass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Contribution:
    """One driver's contribution to its PARENT metric's change."""
    name: str
    v0: float          # value in the base period
    v1: float          # value in the compared period
    delta: float       # v1 - v0 (the driver's own change)
    contribution: float  # signed contribution to the PARENT's delta (kernel-specific)
    pct: float         # contribution / parent_delta (share of the move), 0 if parent_delta == 0


def _pct(part: float, whole: float) -> float:
    return part / whole if whole not in (0, 0.0) else 0.0


def attribute_additive(children: list[tuple[str, float, float]]) -> list[Contribution]:
    """M = sum(children). Each child's contribution to M's delta is its OWN delta. Exact by definition
    (a refund/expense child enters with a negative sign already baked into its values)."""
    parent_delta = sum(v1 - v0 for _, v0, v1 in children)
    return [Contribution(n, v0, v1, v1 - v0, v1 - v0, _pct(v1 - v0, parent_delta))
            for n, v0, v1 in children]


def attribute_multiplicative(a: tuple[str, float, float], b: tuple[str, float, float]
                             ) -> list[Contribution]:
    """M = A x B. SYMMETRIC (mean-value) split -- A-effect = dA*(b0+b1)/2, B-effect = dB*(a0+a1)/2.
    These sum EXACTLY to a1*b1 - a0*b0, so there is NO leftover interaction term to explain."""
    na, a0, a1 = a
    nb, b0, b1 = b
    parent_delta = a1 * b1 - a0 * b0
    ae = (a1 - a0) * (b0 + b1) / 2.0
    be = (b1 - b0) * (a0 + a1) / 2.0
    return [Contribution(na, a0, a1, a1 - a0, ae, _pct(ae, parent_delta)),
            Contribution(nb, b0, b1, b1 - b0, be, _pct(be, parent_delta))]


def attribute_ratio(numerator: tuple[str, float, float], denominator: tuple[str, float, float]
                    ) -> list[Contribution]:
    """M = N / D (covers proportion / rate / binary too). N-effect = dN/d1, D-effect = -N0*dD/(d0*d1).
    Sums EXACTLY to n1/d1 - n0/d0 (first-order/Taylor split done exactly for the two-term case)."""
    nn, n0, n1 = numerator
    nd, d0, d1 = denominator
    if d0 == 0 or d1 == 0:
        raise ValueError("ratio attribution needs non-zero denominators in both periods")
    parent_delta = (n1 / d1) - (n0 / d0)
    n_eff = (n1 - n0) / d1
    d_eff = -n0 * (d1 - d0) / (d0 * d1)
    return [Contribution(nn, n0, n1, n1 - n0, n_eff, _pct(n_eff, parent_delta)),
            Contribution(nd, d0, d1, d1 - d0, d_eff, _pct(d_eff, parent_delta))]


def attribute(kind: str, children: list[tuple[str, float, float]]) -> dict:
    """Dispatch to the kernel by metric KIND and return {contributions, parent_delta, residual}. The
    residual is |parent_delta - sum(contributions)| -- ~0 for a correct kernel, and the agent's cheap
    reconciliation check. `children` for additive = the parts; for multiplicative/ratio = exactly two
    (A,B) / (numerator,denominator)."""
    k = (kind or "").strip().lower()
    if k in ("ratio", "proportion", "rate", "binary"):
        if len(children) != 2:
            raise ValueError("ratio kind needs exactly [numerator, denominator]")
        contribs = attribute_ratio(children[0], children[1])
        n0, n1 = children[0][1], children[0][2]
        d0, d1 = children[1][1], children[1][2]
        parent_delta = (n1 / d1) - (n0 / d0)
    elif k in ("multiplicative", "product"):
        if len(children) != 2:
            raise ValueError("multiplicative kind needs exactly [A, B]")
        contribs = attribute_multiplicative(children[0], children[1])
        parent_delta = children[0][2] * children[1][2] - children[0][1] * children[1][1]
    else:  # additive (default) -- also the safe fallback
        contribs = attribute_additive(children)
        parent_delta = sum(v1 - v0 for _, v0, v1 in children)
    residual = parent_delta - sum(c.contribution for c in contribs)
    return {"contributions": contribs, "parent_delta": parent_delta, "residual": residual}


def adtributor(slices: list[tuple[str, float, float]], top_k: int = 5) -> list[dict]:
    """Rank a dimension's slices by WHERE the metric's change concentrated (Adtributor). For each slice:
      surprise           = q * ln(q / p)             (KL term on the share shift; p=base share, q=now share)
      explanatory_power  = slice_delta / |total_delta|
      impact             = |surprise| * |explanatory_power|
    Returns the top_k slices by impact, each with its delta and impact. Shares are computed on the
    MAGNITUDE of each value (abs), so a metric that is NEGATIVE in every slice (e.g. a loss-making
    margin/profit) still ranks by share shift -- for a non-negative metric abs() is the identity, so
    ranking is unchanged. Pass values already normalised to a comparable window (e.g. daily averages)
    if periods differ in length."""
    base_total = sum(abs(v0) for _, v0, _ in slices) or 1.0
    now_total = sum(abs(v1) for _, _, v1 in slices) or 1.0
    total_delta = sum(v1 - v0 for _, v0, v1 in slices)
    denom = abs(total_delta) or 1.0
    scored: list[dict] = []
    for name, v0, v1 in slices:
        p = abs(v0) / base_total
        q = abs(v1) / now_total
        surprise = q * math.log(q / p) if p > 0 and q > 0 else 0.0
        explan = (v1 - v0) / denom
        scored.append({"slice": name, "v0": v0, "v1": v1, "delta": v1 - v0,
                       "impact": abs(surprise) * abs(explan)})
    scored.sort(key=lambda s: s["impact"], reverse=True)
    return scored[:top_k]
