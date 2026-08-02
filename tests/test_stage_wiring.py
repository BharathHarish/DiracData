"""Phase 0c: the agent picks a model PER STAGE. With no override every stage uses the injected model
(fakes work, nothing rebuilt); a per-stage override routes only that stage through the registry.
Uses a fake registry builder so no real model is constructed.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.agent import V4Agent  # noqa: E402
from diracdata.config import Config, Stage, StageConfig  # noqa: E402
from diracdata.models import ModelRegistry  # noqa: E402


class _Injected:
    tag = "injected"


class _Built:
    def __init__(self, profile):
        self.tag = profile


def _agent(config, registry=None):
    return V4Agent(model=_Injected(), workspace=None, engine=None, result_store=None,
                   config=config, model_registry=registry, subagents=False, frame=False)


class StageWiringTests(unittest.TestCase):
    def test_default_every_stage_uses_injected_model(self) -> None:
        # even with a registry present, an empty stage config must return the injected model untouched
        built = []
        reg = ModelRegistry(Config(), builder=lambda *a: built.append(a) or _Built(a[1]))
        agent = _agent(Config(), registry=reg)
        for stage in Stage:
            self.assertIs(agent._stage_model(stage), agent.model)
        self.assertEqual(built, [])                       # registry never invoked for defaults

    def test_override_routes_only_that_stage_through_registry(self) -> None:
        built = []
        cfg = Config(stages={Stage.VERIFY: StageConfig(model_profile="anthropic_haiku_45")})
        reg = ModelRegistry(cfg, builder=lambda c, p, mt, t, r: built.append(p) or _Built(p))
        agent = _agent(cfg, registry=reg)
        # verify -> built via registry; others -> injected
        vm = agent._stage_model(Stage.VERIFY)
        self.assertIsInstance(vm, _Built)
        self.assertEqual(vm.tag, "anthropic_haiku_45")
        self.assertIs(agent._stage_model(Stage.AUTHORING), agent.model)
        self.assertIs(agent._stage_model(Stage.FRAMING), agent.model)
        self.assertEqual(built, ["anthropic_haiku_45"])   # only the overridden stage built

    def test_from_env_empty_stages_still_use_injected(self) -> None:
        # from_env populates stages with EMPTY StageConfigs -> still the default path
        agent = _agent(Config.from_env(None))
        self.assertIs(agent._stage_model(Stage.SUMMARIZE), agent.model)


if __name__ == "__main__":
    unittest.main()
