"""Phase 3 (agentic) wiring: the agent assembles routing facts, the MAIN model chooses the analyst's
model, and _run_analyst builds exactly that model via the registry. Router off bypasses the router and
the registry (today's per-stage path). Scripted models, no network."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from langchain_core.messages import AIMessage, AIMessageChunk  # noqa: E402

from diracdata.agent import Agent  # noqa: E402
from diracdata.config import Config  # noqa: E402
from diracdata.runtime.working_memory import WorkingMemory  # noqa: E402
from diracdata.models import ModelRegistry  # noqa: E402


class _RouterMainModel:
    """Serves the route call (.invoke -> route JSON)."""
    def __init__(self, route_json):
        self.route_json = route_json

    def invoke(self, messages):
        return AIMessage(content=self.route_json)


class _AuthoringModel:
    """Drives run_loop to a final answer."""
    def bind_tools(self, tools):
        return self

    def stream(self, messages):
        yield AIMessageChunk(content="FINAL ANSWER: 100000")


class _StubGate:
    def __init__(self):
        self.result = None
        self.tokens = 0

    def submit(self, answer, result_ids, **kw):
        self.result = {"answer": answer, "result_ids": result_ids}
        return "ACCEPTED"


class _FakeWorkspace:
    def exact_match(self, goal):
        return object()

    def slot_match(self, goal):
        return None


ROUTE_JSON = ('{"reasoning":"free+strong for a precedented lookup","authoring_profile":'
              '"anthropic_haiku_45","max_tokens":4000,"temperature":0.0,'
              '"max_steps":4,"allow_shortcut":true}')


class RouterWiringTests(unittest.TestCase):
    def test_router_on_builds_the_chosen_model(self) -> None:
        cfg = Config(router_enabled=True, agent_model_profile="global")
        built = []
        reg = ModelRegistry(cfg, builder=lambda c, p, mt, t, r: built.append((p, mt)) or _AuthoringModel())
        agent = Agent(model=_RouterMainModel(ROUTE_JSON), workspace=_FakeWorkspace(), engine=None,
                        result_store=None, config=cfg, model_registry=reg, subagents=False, frame=False)
        signals = agent._route_signals("count clients", WorkingMemory(goal="q"))
        self.assertTrue(signals.exact_match)
        plan, _ = agent._route("count clients", signals)
        self.assertEqual(plan.authoring_profile, "anthropic_haiku_45")
        gate = _StubGate()
        agent._run_analyst(plan, [], "sys", WorkingMemory(goal="q"), gate, lambda *a: None)
        self.assertEqual(built, [("anthropic_haiku_45", 4000)])   # chosen model + budget built
        self.assertIsNotNone(gate.result)

    def test_router_off_uses_injected_model_no_registry(self) -> None:
        cfg = Config(router_enabled=False)
        built = []
        reg = ModelRegistry(cfg, builder=lambda c, p, mt, t, r: built.append(p) or _AuthoringModel())
        agent = Agent(model=_AuthoringModel(), workspace=_FakeWorkspace(), engine=None,
                        result_store=None, config=cfg, model_registry=reg, subagents=False, frame=False)
        plan, tok = agent._route("q", agent._route_signals("q", WorkingMemory(goal="q")))
        self.assertEqual(plan.authoring_profile, "")   # standard: global model
        self.assertEqual(tok, 0)
        gate = _StubGate()
        agent._run_analyst(plan, [], "sys", WorkingMemory(goal="q"), gate, lambda *a: None)
        self.assertEqual(built, [])                    # registry NOT used when off
        self.assertIsNotNone(gate.result)


if __name__ == "__main__":
    unittest.main()
