"""Adaptive model routing. From cheap, mostly model-emitted signals (framing self-assessment +
experience-match strength), pick a per-turn RunPlan: which model the analyst runs on, its step budget,
and whether it may take the experience shortcut -- trading cost for depth. Routing is a RESOURCE
decision, never a correctness gate: a mis-route is caught by the finish gate and escalated one tier up.

Depends only on `diracdata.config`. The agent applies the plan (via the ModelRegistry); the Router
itself just decides.
"""

from diracdata.routing.router import RouteSignals, RunPlan, make_router

__all__ = ["RouteSignals", "RunPlan", "make_router"]
