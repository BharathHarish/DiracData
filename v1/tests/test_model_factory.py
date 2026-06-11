from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import DiracDataSettings
from diracdata.llms.model_factory import ChatModelFactory, ModelProvider


class ModelFactoryTest(unittest.TestCase):
    def test_resolves_builtin_bedrock_qwen_profile(self) -> None:
        settings = DiracDataSettings(
            agent_model_profile="bedrock_qwen3_next_80b_a3b_ap_south_1",
            agent_llm_max_tokens=2048,
            agent_llm_temperature=0.1,
        )

        resolved = ChatModelFactory(settings=settings).resolve_agent_model()

        self.assertEqual(resolved.profile_id, "bedrock_qwen3_next_80b_a3b_ap_south_1")
        self.assertEqual(resolved.provider, ModelProvider.BEDROCK_CONVERSE)
        self.assertEqual(resolved.model, "qwen.qwen3-next-80b-a3b")
        self.assertEqual(resolved.region_name, "ap-south-1")
        self.assertTrue(resolved.is_moe)
        self.assertEqual(resolved.max_tokens, 2048)
        self.assertEqual(resolved.temperature, 0.1)

    def test_resolves_builtin_bedrock_anthropic_profiles(self) -> None:
        settings = DiracDataSettings(
            agent_model_profile="bedrock_anthropic_sonnet_46_ap_south_1",
        )

        resolved = ChatModelFactory(settings=settings).resolve_agent_model()

        self.assertEqual(resolved.profile_id, "bedrock_anthropic_sonnet_46_ap_south_1")
        self.assertEqual(resolved.provider, ModelProvider.BEDROCK_CONVERSE)
        self.assertEqual(resolved.model, "global.anthropic.claude-sonnet-4-6")
        self.assertEqual(resolved.region_name, "ap-south-1")
        self.assertFalse(resolved.is_moe)

        settings = DiracDataSettings(
            agent_model_profile="bedrock_anthropic_haiku_45_ap_south_1",
        )
        resolved = ChatModelFactory(settings=settings).resolve_agent_model()

        self.assertEqual(resolved.profile_id, "bedrock_anthropic_haiku_45_ap_south_1")
        self.assertEqual(resolved.provider, ModelProvider.BEDROCK_CONVERSE)
        self.assertEqual(resolved.model, "anthropic.claude-haiku-4-5-20251001-v1:0")
        self.assertEqual(resolved.region_name, "ap-south-1")

    def test_resolves_builtin_bedrock_qwen_coder_480b_profile(self) -> None:
        settings = DiracDataSettings(
            agent_model_profile="bedrock_qwen3_coder_480b_a35b_ap_south_1",
        )

        resolved = ChatModelFactory(settings=settings).resolve_agent_model()

        self.assertEqual(resolved.profile_id, "bedrock_qwen3_coder_480b_a35b_ap_south_1")
        self.assertEqual(resolved.provider, ModelProvider.BEDROCK_CONVERSE)
        self.assertEqual(resolved.model, "qwen.qwen3-coder-480b-a35b-v1:0")
        self.assertEqual(resolved.region_name, "ap-south-1")
        self.assertTrue(resolved.is_moe)

    def test_resolves_new_bedrock_serverless_open_model_profiles(self) -> None:
        expected_profiles = {
            "bedrock_gemma_3_12b_it_ap_south_1": (
                "google.gemma-3-12b-it",
                8192,
                True,
            ),
            "bedrock_openai_gpt_oss_120b_ap_south_1": (
                "openai.gpt-oss-120b-1:0",
                8192,
                True,
            ),
            "bedrock_zai_glm_5_ap_south_1": ("zai.glm-5", 8192, True),
            "bedrock_meta_llama3_70b_instruct_ap_south_1": (
                "meta.llama3-70b-instruct-v1:0",
                2048,
                False,
            ),
            "bedrock_kimi_k2_thinking_ap_south_1": (
                "moonshot.kimi-k2-thinking",
                8192,
                True,
            ),
            "bedrock_deepseek_v32_ap_south_1": ("deepseek.v3.2", 8192, True),
            "bedrock_nvidia_nemotron_super_3_120b_ap_south_1": (
                "nvidia.nemotron-super-3-120b",
                8192,
                True,
            ),
        }

        for (
            profile_id,
            (model_id, max_tokens, supports_tool_use),
        ) in expected_profiles.items():
            with self.subTest(profile_id=profile_id):
                settings = DiracDataSettings(agent_model_profile=profile_id)
                resolved = ChatModelFactory(settings=settings).resolve_agent_model()

                self.assertEqual(resolved.profile_id, profile_id)
                self.assertEqual(resolved.provider, ModelProvider.BEDROCK_CONVERSE)
                self.assertEqual(resolved.model, model_id)
                self.assertEqual(resolved.max_tokens, max_tokens)
                self.assertEqual(resolved.supports_tool_use, supports_tool_use)
                self.assertEqual(resolved.region_name, "ap-south-1")
                self.assertEqual(resolved.credential_source, "bedrock")

    def test_resolves_builtin_gemini_profile(self) -> None:
        settings = DiracDataSettings(
            agent_model_profile="gemini_2_5_flash",
            agent_llm_max_tokens=2048,
            agent_llm_temperature=0.1,
        )

        resolved = ChatModelFactory(settings=settings).resolve_agent_model()

        self.assertEqual(resolved.profile_id, "gemini_2_5_flash")
        self.assertEqual(resolved.provider, ModelProvider.GOOGLE_GENAI)
        self.assertEqual(resolved.model, "gemini-2.5-flash")
        self.assertIsNone(resolved.region_name)
        self.assertEqual(resolved.max_tokens, 2048)
        self.assertEqual(resolved.temperature, 0.1)

    def test_resolves_builtin_openai_low_cost_profile(self) -> None:
        settings = DiracDataSettings(
            agent_model_profile="openai_gpt_5_nano",
            agent_llm_max_tokens=2048,
            agent_llm_temperature=0.1,
        )

        resolved = ChatModelFactory(settings=settings).resolve_agent_model()

        self.assertEqual(resolved.profile_id, "openai_gpt_5_nano")
        self.assertEqual(resolved.provider, ModelProvider.OPENAI)
        self.assertEqual(resolved.model, "gpt-5-nano")
        self.assertEqual(resolved.credential_source, "openai")
        self.assertIsNone(resolved.region_name)
        self.assertEqual(resolved.max_tokens, 2048)
        self.assertEqual(resolved.temperature, 0.1)

    def test_resolves_builtin_openai_gpt_5_4_mini_profile(self) -> None:
        settings = DiracDataSettings(agent_model_profile="openai_gpt_5_4_mini")

        resolved = ChatModelFactory(settings=settings).resolve_agent_model()

        self.assertEqual(resolved.profile_id, "openai_gpt_5_4_mini")
        self.assertEqual(resolved.provider, ModelProvider.OPENAI)
        self.assertEqual(resolved.model, "gpt-5.4-mini")
        self.assertEqual(resolved.credential_source, "openai")

    def test_resolves_builtin_openai_gpt_5_4_nano_profile(self) -> None:
        settings = DiracDataSettings(agent_model_profile="openai_gpt_5_4_nano")

        resolved = ChatModelFactory(settings=settings).resolve_agent_model()

        self.assertEqual(resolved.profile_id, "openai_gpt_5_4_nano")
        self.assertEqual(resolved.provider, ModelProvider.OPENAI)
        self.assertEqual(resolved.model, "gpt-5.4-nano")
        self.assertEqual(resolved.credential_source, "openai")

    def test_resolves_listed_gemini_35_flash_profile(self) -> None:
        settings = DiracDataSettings(agent_model_profile="gemini_3_5_flash")

        resolved = ChatModelFactory(settings=settings).resolve_agent_model()

        self.assertEqual(resolved.profile_id, "gemini_3_5_flash")
        self.assertEqual(resolved.provider, ModelProvider.GOOGLE_GENAI)
        self.assertEqual(resolved.model, "gemini-3.5-flash")

    def test_bedrock_region_override_does_not_leak_into_gemini_profiles(self) -> None:
        settings = DiracDataSettings(
            agent_model_profile="gemini_2_5_flash",
            bedrock_region="ap-south-1",
        )

        resolved = ChatModelFactory(settings=settings).resolve_agent_model()

        self.assertIsNone(resolved.region_name)

    def test_bedrock_region_override_updates_profile_region(self) -> None:
        settings = DiracDataSettings(
            agent_model_profile="bedrock_qwen3_next_80b_a3b_ap_south_1",
            bedrock_region="us-east-1",
        )

        resolved = ChatModelFactory(settings=settings).resolve_agent_model()

        self.assertEqual(resolved.region_name, "us-east-1")

    def test_create_bedrock_chat_model_passes_langchain_provider_and_region(self) -> None:
        settings = DiracDataSettings(
            agent_model_profile="bedrock_qwen3_next_80b_a3b_ap_south_1",
            agent_llm_max_tokens=1024,
            bedrock_api_key="bedrock-key",
        )
        captured = {}

        def fake_init(**kwargs):
            captured.update(kwargs)
            return object()

        with patch("diracdata.llms.model_factory.init_langchain_chat_model", fake_init):
            ChatModelFactory(settings=settings).create_agent_chat_model()

        self.assertEqual(captured["model"], "qwen.qwen3-next-80b-a3b")
        self.assertEqual(captured["model_provider"], "bedrock_converse")
        self.assertEqual(captured["region_name"], "ap-south-1")
        self.assertEqual(captured["max_tokens"], 1024)
        self.assertEqual(captured["api_key"], "bedrock-key")

    def test_create_mantle_chat_model_passes_openai_compatible_base_url_and_key(self) -> None:
        settings = DiracDataSettings(
            agent_model_profile="bedrock_mantle_qwen3_next_80b_a3b_ap_south_1",
            bedrock_api_key="bedrock-key",
        )
        captured = {}

        def fake_init(**kwargs):
            captured.update(kwargs)
            return object()

        with patch("diracdata.llms.model_factory.init_langchain_chat_model", fake_init):
            ChatModelFactory(settings=settings).create_agent_chat_model()

        self.assertEqual(captured["model"], "qwen.qwen3-next-80b-a3b-instruct")
        self.assertEqual(captured["model_provider"], "openai")
        self.assertEqual(captured["base_url"], "https://bedrock-mantle.ap-south-1.api.aws/v1")
        self.assertEqual(captured["api_key"], "bedrock-key")

    def test_create_gemini_chat_model_passes_langchain_provider_and_key(self) -> None:
        settings = DiracDataSettings(
            agent_model_profile="gemini_2_5_flash_lite",
            agent_llm_max_tokens=1024,
            google_api_key="google-key",
        )
        captured = {}

        def fake_init(**kwargs):
            captured.update(kwargs)
            return object()

        with patch("diracdata.llms.model_factory.init_langchain_chat_model", fake_init):
            ChatModelFactory(settings=settings).create_agent_chat_model()

        self.assertEqual(captured["model"], "gemini-2.5-flash-lite")
        self.assertEqual(captured["model_provider"], "google_genai")
        self.assertEqual(captured["max_tokens"], 1024)
        self.assertEqual(captured["api_key"], "google-key")

    def test_create_openai_chat_model_passes_langchain_provider_and_key(self) -> None:
        settings = DiracDataSettings(
            agent_model_profile="openai_gpt_5_nano",
            agent_llm_max_tokens=1024,
            openai_api_key="openai-key",
        )
        captured = {}

        def fake_init(**kwargs):
            captured.update(kwargs)
            return object()

        with patch("diracdata.llms.model_factory.init_langchain_chat_model", fake_init):
            ChatModelFactory(settings=settings).create_agent_chat_model()

        self.assertEqual(captured["model"], "gpt-5-nano")
        self.assertEqual(captured["model_provider"], "openai")
        self.assertEqual(captured["max_tokens"], 1024)
        self.assertEqual(captured["api_key"], "openai-key")

    def test_openai_compatible_bedrock_profile_uses_bedrock_key(self) -> None:
        settings = DiracDataSettings(
            agent_model_profile="bedrock_mantle_qwen3_next_80b_a3b_ap_south_1",
            openai_api_key="openai-key",
            bedrock_api_key="bedrock-key",
        )
        captured = {}

        def fake_init(**kwargs):
            captured.update(kwargs)
            return object()

        with patch("diracdata.llms.model_factory.init_langchain_chat_model", fake_init):
            ChatModelFactory(settings=settings).create_agent_chat_model()

        self.assertEqual(captured["model_provider"], "openai")
        self.assertEqual(captured["api_key"], "bedrock-key")
        self.assertEqual(
            captured["base_url"],
            "https://bedrock-mantle.ap-south-1.api.aws/v1",
        )

    def test_unknown_profile_fails_with_available_profile_ids(self) -> None:
        settings = DiracDataSettings(agent_model_profile="missing_profile")

        with self.assertRaises(ValueError) as context:
            ChatModelFactory(settings=settings).resolve_agent_model()

        self.assertIn("missing_profile", str(context.exception))
        self.assertIn("anthropic_haiku_45", str(context.exception))


if __name__ == "__main__":
    unittest.main()
