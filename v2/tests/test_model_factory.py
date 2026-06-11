import unittest
from unittest.mock import patch

from diracdata_v2.llms.model_factory import BUILT_IN_MODEL_PROFILES, ChatModelFactory
from diracdata_v2.settings import V2Settings


class ModelFactoryTests(unittest.TestCase):
    def test_required_agent_profiles_exist(self):
        for profile in [
            "bedrock_qwen3_next_80b_a3b_ap_south_1",
            "bedrock_zai_glm_5_ap_south_1",
            "openai_gpt_5_4_mini",
            "anthropic_haiku_45",
            "anthropic_sonnet_46",
        ]:
            self.assertIn(profile, BUILT_IN_MODEL_PROFILES)

    def test_factory_uses_langchain_init_chat_model(self):
        settings = V2Settings(
            agent_model_profile="anthropic_haiku_45",
            anthropic_api_key="test-key",
        )
        with patch("diracdata_v2.llms.model_factory.init_chat_model") as init_model:
            init_model.return_value = object()
            ChatModelFactory(settings=settings).create_agent_chat_model()

        kwargs = init_model.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(kwargs["model_provider"], "anthropic")


if __name__ == "__main__":
    unittest.main()

