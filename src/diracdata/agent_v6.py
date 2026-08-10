"""AgentV6 -- the query agent that consumes the LEARNED GOVERNED SEMANTIC MODEL (semantic_model.yaml
compiled by scripts/learn2.py). Everything about the base Agent is reused verbatim; V6 only injects a
compact GOVERNED MODEL block into the analyst system prompt via the `_extra_context` hook (which the
base returns "" for -- so the current Agent is untouched and cannot regress).

The block gives the analyst, up front: each table's verified GRAIN + kind, the JOIN CARDINALITY
(so it aggregates-then-joins across a many_to_many and never fan-out/chasm double-counts), and the
COMPLEX-COLUMN ACCESS RECIPES (so it copies `UNNEST(UNNEST(fulfillment.shipments).items).sku` instead
of trial-and-erroring the nested-unnest syntax)."""

from __future__ import annotations

from typing import Any

from diracdata.agent import Agent


class AgentV6(Agent):
    def __init__(self, *, semantic_model: dict | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.semantic_model = semantic_model or {}

    def _extra_context(self) -> str:
        return render_semantic_model(self.semantic_model)


def render_semantic_model(sm: dict) -> str:
    """Render the governed model as a compact, grain-safe context block for the analyst."""
    if not sm or not sm.get("models"):
        return ""
    models = sm.get("models") or {}
    lines = ["GOVERNED SEMANTIC MODEL (verified -- HONOR these; do not re-derive grain or join safety):"]
    lines.append("GRAIN & KIND (what one row is):")
    for t, md in models.items():
        g = md.get("grain")
        k = md.get("kind")
        lines.append(f"  - {t}: grain = {g}" + (f" ; kind = {k}" if k else ""))
    # complex column access recipes
    recipes = []
    for t, md in models.items():
        for c, cd in (md.get("columns") or {}).items():
            if isinstance(cd, dict) and cd.get("access_recipe"):
                recipes.append(f"  - {t}.{c}: {cd['access_recipe']}")
    if recipes:
        lines.append("COMPLEX/NESTED COLUMN ACCESS RECIPES (use verbatim -- do not guess the unnest/extract):")
        lines += recipes
    # join cardinality -> fan-out / chasm safety
    rels = sm.get("relationships") or []
    if rels:
        lines.append("JOIN CARDINALITY (avoid fan-out/chasm: NEVER sum an additive measure across a "
                     "many_to_many join -- aggregate each fact separately then join the aggregates):")
        for j in rels:
            card = j.get("cardinality", "?")
            lines.append(f"  - {j.get('left')} -> {j.get('right')} : {card}")
    # metrics / dimensions catalog (names only; `define`/`attribute` fetch detail)
    if sm.get("metrics"):
        lines.append("DEFINED METRICS: " + ", ".join((sm.get("metrics") or {}).keys()))
    if sm.get("dimensions"):
        lines.append("DEFINED DIMENSIONS: " + ", ".join((sm.get("dimensions") or {}).keys()))
    return "\n".join(lines)
