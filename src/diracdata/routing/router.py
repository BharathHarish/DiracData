"""Agentic model routing. The outer-loop MAIN model reasons over a catalog of models (family, cost,
capability, tool/reasoning support) and the framed task, and CHOOSES the cheapest model that will
still be correct -- plus its token budget, temperature, and step budget. Routing is a judgment, not a
policy: there are no hard-coded tier->profile mappings. The only deterministic parts are validation
(the chosen model must exist and support tools) and a safe fallback to the global model.

The finish gate stays the correctness authority; a bad pick is caught and the router is re-asked for a
stronger model (escalation).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from diracdata.config import Config
from diracdata.prompts import load_prompt
from diracdata.streaming import collect
from diracdata.utils.model_factory import BUILT_IN_MODEL_PROFILES, render_catalog
from diracdata.utils.streaming import Sink, loads_json, null_sink

_ROUTE_PROMPT = load_prompt("route")


@dataclass(frozen=True)
class RouteSignals:
    """Context handed to the router (not a policy input -- just facts for it to reason over)."""
    exact_match: bool = False
    slot_match: bool = False
    intent: str = ""            # the framed intent, if available
    task_type: str = ""         # triage's verdict: "rca" | "analytics" (so routing sees what triage saw)


@dataclass(frozen=True)
class RunPlan:
    """What the router decided for this turn; the agent applies it."""
    authoring_profile: str      # "" -> use the global model (fallback / router off)
    max_tokens: int
    temperature: float
    max_steps: int
    allow_shortcut: bool
    reasoning: str = ""


def _standard_plan(config: Config) -> RunPlan:
    """Today's behaviour: the global model at the normal budget."""
    temp = 0.0 if config.deterministic_sampling else config.agent_llm_temperature
    return RunPlan(authoring_profile="", max_tokens=config.agent_llm_max_tokens, temperature=temp,
                   max_steps=config.max_steps, allow_shortcut=False, reasoning="router off / standard")


def _valid_authoring(profile_id: str) -> bool:
    p = BUILT_IN_MODEL_PROFILES.get(profile_id)
    return bool(p and p.supports_tools)


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _parse_plan(raw: dict, config: Config) -> RunPlan:
    """Turn the router model's JSON into a validated RunPlan; fall back to the global model on any
    invalid/hallucinated model choice."""
    profile = str(raw.get("authoring_profile") or "").strip()
    if not _valid_authoring(profile):
        return _standard_plan(config)
    temp = 0.0 if config.deterministic_sampling else float(raw.get("temperature") or 0.0)
    return RunPlan(
        authoring_profile=profile,
        max_tokens=_clamp(raw.get("max_tokens"), 512, 32000, config.agent_llm_max_tokens),
        temperature=temp,
        max_steps=_clamp(raw.get("max_steps"), config.router_min_steps, 40, config.max_steps),
        allow_shortcut=bool(raw.get("allow_shortcut")),
        reasoning=str(raw.get("reasoning") or "")[:200],
    )


# route(task, signals, failed_profile=None) -> (RunPlan, tokens)
Route = Callable[..., tuple[RunPlan, int]]


def make_router(model: Any, config: Config, *, sink: Sink = null_sink) -> Route:
    """Return route(task, signals, failed_profile=None) -> (RunPlan, tokens). One model call by the
    main model, reasoning over the catalog. Router off -> the standard plan, no call."""
    from langchain_core.messages import HumanMessage, SystemMessage

    system = _ROUTE_PROMPT + "\n\nMODEL CATALOG:\n" + render_catalog()

    def route(task: str, signals: RouteSignals, failed_profile: str | None = None) -> tuple[RunPlan, int]:
        if not config.router_enabled:
            return _standard_plan(config), 0
        payload = {
            "task": task,
            "intent": signals.intent,
            "task_type": signals.task_type or "(unclassified)",
            "precedent_exists": bool(signals.exact_match or signals.slot_match),
            "previous_model_that_failed": failed_profile or "(none)",
        }
        out = collect(model=model, stage="route", sink=sink, config=config,
                      messages=[SystemMessage(content=system),
                                HumanMessage(content=json.dumps(payload, default=str))])
        return _parse_plan(loads_json(out["text"]), config), out["tokens"]

    return route
