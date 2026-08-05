"""Phase 3 (agentic): the router is a MODEL CALL over the catalog. It parses the model's choice into a
validated RunPlan, falls back to the global model on any invalid/hallucinated pick, and makes no call
when the router is off. Scripted model, no network."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from langchain_core.messages import AIMessage  # noqa: E402

from diracdata.config import Config  # noqa: E402
from diracdata.routing import RouteSignals, make_router  # noqa: E402


class _RouterModel:
    """Returns a fixed route JSON. No .stream -> collect falls back to .invoke."""
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        self._last = messages
        return AIMessage(content=self.text)


VALID = ('{"reasoning":"free+strong for a simple lookup","authoring_profile":'
         '"anthropic_haiku_45","max_tokens":4000,"temperature":0.0,'
         '"max_steps":4,"allow_shortcut":true}')


class AgenticRouterTests(unittest.TestCase):
    def test_off_returns_standard_without_calling_model(self) -> None:
        m = _RouterModel(VALID)
        route = make_router(m, Config(router_enabled=False, agent_model_profile="global", max_steps=24))
        plan, tok = route("q", RouteSignals())
        self.assertEqual(plan.authoring_profile, "")   # "" -> global model
        self.assertEqual(plan.max_steps, 24)
        self.assertEqual(tok, 0)
        self.assertEqual(m.calls, 0)                   # no model call when off

    def test_valid_choice_is_applied(self) -> None:
        m = _RouterModel(VALID)
        route = make_router(m, Config(router_enabled=True))
        plan, _ = route("count clients", RouteSignals(exact_match=True))
        self.assertEqual(plan.authoring_profile, "anthropic_haiku_45")
        self.assertEqual(plan.max_steps, 8)   # scripted 4 -> clamped up to the router_min_steps floor (8)
        self.assertTrue(plan.allow_shortcut)
        self.assertEqual(m.calls, 1)

    def test_hallucinated_model_falls_back_to_global(self) -> None:
        route = make_router(_RouterModel('{"authoring_profile":"gpt-9-ultra","max_steps":3}'),
                            Config(router_enabled=True, agent_model_profile="global", max_steps=24))
        plan, _ = route("q", RouteSignals())
        self.assertEqual(plan.authoring_profile, "")   # invalid id -> safe fallback
        self.assertEqual(plan.max_steps, 24)

    def test_budgets_are_clamped(self) -> None:
        route = make_router(
            _RouterModel('{"authoring_profile":"anthropic_haiku_45","max_tokens":999999,"max_steps":500}'),
            Config(router_enabled=True))
        plan, _ = route("q", RouteSignals())
        self.assertLessEqual(plan.max_tokens, 32000)   # clamped
        self.assertLessEqual(plan.max_steps, 40)

    def test_determinism_floor_pins_temperature(self) -> None:
        route = make_router(
            _RouterModel('{"authoring_profile":"anthropic_haiku_45","temperature":0.9,"max_steps":5}'),
            Config(router_enabled=True, deterministic_sampling=True))
        plan, _ = route("q", RouteSignals())
        self.assertEqual(plan.temperature, 0.0)

    def test_garbage_output_falls_back(self) -> None:
        route = make_router(_RouterModel("not json at all"), Config(router_enabled=True, max_steps=24))
        plan, _ = route("q", RouteSignals())
        self.assertEqual(plan.authoring_profile, "")   # fallback


if __name__ == "__main__":
    unittest.main()
