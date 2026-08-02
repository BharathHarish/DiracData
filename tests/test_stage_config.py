"""Phase 0a: per-stage model + sampling config. A stage with no DIRACDATA_STAGE_* vars falls back to
the global model (no behaviour change); an override applies to ONLY that stage; the determinism floor
clamps temperature regardless of any per-stage value.
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.config import Config, Stage, StageConfig  # noqa: E402


class _Env:
    """Set env vars for the duration of a block, restoring prior values."""
    def __init__(self, **kv):
        self.kv = kv
        self.prev = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.prev[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class ResolveStageTests(unittest.TestCase):
    def test_defaults_reproduce_global_for_every_stage(self) -> None:
        c = Config()  # no overrides
        for stage in Stage:
            r = c.resolve_stage(stage)
            self.assertEqual(r.profile_id, c.agent_model_profile)
            self.assertEqual(r.max_tokens, c.agent_llm_max_tokens)
            self.assertEqual(r.temperature, 0.0)              # determinism default on
            self.assertIsNone(r.reasoning_effort)

    def test_override_applies_to_only_that_stage(self) -> None:
        c = Config(stages={Stage.VERIFY: StageConfig(max_tokens=2048, model_profile="anthropic_haiku_45")})
        v = c.resolve_stage(Stage.VERIFY)
        self.assertEqual(v.max_tokens, 2048)
        self.assertEqual(v.profile_id, "anthropic_haiku_45")
        # authoring untouched -> global
        a = c.resolve_stage(Stage.AUTHORING)
        self.assertEqual(a.max_tokens, c.agent_llm_max_tokens)
        self.assertEqual(a.profile_id, c.agent_model_profile)

    def test_determinism_floor_clamps_stage_temperature(self) -> None:
        c = Config(deterministic_sampling=True,
                   stages={Stage.AUTHORING: StageConfig(temperature=0.7)})
        self.assertEqual(c.resolve_stage(Stage.AUTHORING).temperature, 0.0)

    def test_stage_temperature_honored_when_determinism_off(self) -> None:
        c = Config(deterministic_sampling=False,
                   stages={Stage.AUTHORING: StageConfig(temperature=0.7)})
        self.assertEqual(c.resolve_stage(Stage.AUTHORING).temperature, 0.7)


class StagesFromEnvTests(unittest.TestCase):
    def test_no_stage_env_gives_empty_configs(self) -> None:
        c = Config.from_env(None)  # no .env, no STAGE_* set in this process (assert per-field)
        for stage in Stage:
            sc = c.stages[stage]
            self.assertIsNone(sc.model_profile)
            self.assertIsNone(sc.max_tokens)
            self.assertIsNone(sc.temperature)

    def test_env_populates_a_single_stage(self) -> None:
        with _Env(DIRACDATA_STAGE_FRAMING_MODEL_PROFILE="anthropic_haiku_45",
                  DIRACDATA_STAGE_FRAMING_MAX_TOKENS="1024",
                  DIRACDATA_STAGE_VERIFY_TEMPERATURE="0.2"):
            c = Config.from_env(None)
        self.assertEqual(c.stages[Stage.FRAMING].model_profile, "anthropic_haiku_45")
        self.assertEqual(c.stages[Stage.FRAMING].max_tokens, 1024)
        self.assertIsNone(c.stages[Stage.FRAMING].temperature)
        self.assertEqual(c.stages[Stage.VERIFY].temperature, 0.2)
        self.assertIsNone(c.stages[Stage.AUTHORING].model_profile)
        # resolution reflects it
        self.assertEqual(c.resolve_stage(Stage.FRAMING).profile_id, "anthropic_haiku_45")
        self.assertEqual(c.resolve_stage(Stage.FRAMING).max_tokens, 1024)


if __name__ == "__main__":
    unittest.main()
