"""Phase 0b: ModelRegistry builds a model per (profile, overrides) and CACHES it, so per-stage/
per-query model selection never rebuilds the same model. Uses an injected builder (no network)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from diracdata.config import Config, Stage, StageConfig  # noqa: E402
from diracdata.models import ModelRegistry  # noqa: E402


class _FakeModel:
    def __init__(self, tag):
        self.tag = tag


def _fake_builder_factory():
    calls = []

    def builder(config, profile_id, max_tokens, temperature, reasoning_effort):
        calls.append((profile_id, max_tokens, temperature, reasoning_effort))
        return _FakeModel(profile_id)

    return builder, calls


class ModelRegistryTests(unittest.TestCase):
    def test_caches_by_key_no_rebuild(self) -> None:
        builder, calls = _fake_builder_factory()
        reg = ModelRegistry(Config(), builder=builder)
        a = reg.get("p1")
        b = reg.get("p1")
        self.assertIs(a, b)                       # same StageModel instance
        self.assertEqual(len(calls), 1)           # built once

    def test_different_overrides_are_distinct(self) -> None:
        builder, calls = _fake_builder_factory()
        reg = ModelRegistry(Config(), builder=builder)
        reg.get("p1", max_tokens=2048)
        reg.get("p1", max_tokens=4096)
        reg.get("p1")
        self.assertEqual(len(calls), 3)           # three distinct keys -> three builds

    def test_provider_from_builtin_profile_else_global(self) -> None:
        builder, _ = _fake_builder_factory()
        reg = ModelRegistry(Config(agent_llm_provider="anthropic"), builder=builder)
        self.assertEqual(reg.get("bedrock_zai_glm_5_ap_south_1").provider, "bedrock_converse")
        self.assertEqual(reg.get("some_unknown_profile").provider, "anthropic")  # falls back to global

    def test_for_stage_uses_resolved_config(self) -> None:
        builder, calls = _fake_builder_factory()
        cfg = Config(stages={Stage.VERIFY: StageConfig(model_profile="anthropic_haiku_45", max_tokens=1024)})
        reg = ModelRegistry(cfg, builder=builder)
        sm = reg.for_stage(Stage.VERIFY)
        self.assertEqual(sm.profile_id, "anthropic_haiku_45")
        self.assertEqual(calls[0][0], "anthropic_haiku_45")   # profile
        self.assertEqual(calls[0][1], 1024)                   # max_tokens resolved
        self.assertEqual(calls[0][2], 0.0)                    # temp clamped (determinism default on)

    def test_for_stage_default_uses_global_profile(self) -> None:
        builder, calls = _fake_builder_factory()
        cfg = Config()  # no stage overrides
        reg = ModelRegistry(cfg, builder=builder)
        sm = reg.for_stage(Stage.AUTHORING)
        self.assertEqual(sm.profile_id, cfg.agent_model_profile)


if __name__ == "__main__":
    unittest.main()
