from pathlib import Path
import os
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config.settings import DiracDataSettings
from diracdata.llms import ChatModelMessage, LangChainChatModelClient, chat_model_client_from_settings


class EnvCheckingModel:
    def invoke(self, messages: list[dict[str, str]]) -> str:
        if os.environ.get("ANTHROPIC_API_KEY") != "test-key":
            raise AssertionError("Anthropic key was not available during invoke")
        return "ok"


class GoogleEnvCheckingModel:
    def invoke(self, messages: list[dict[str, str]]) -> str:
        if os.environ.get("GOOGLE_API_KEY") != "google-test-key":
            raise AssertionError("Google key was not available during invoke")
        return "ok"


class OpenAIEnvCheckingModel:
    def invoke(self, messages: list[dict[str, str]]) -> str:
        if os.environ.get("OPENAI_API_KEY") != "openai-test-key":
            raise AssertionError("OpenAI key was not available during invoke")
        return "ok"


class EnvCheckingClient(LangChainChatModelClient):
    def _init_chat_model(self) -> object:
        if os.environ.get("ANTHROPIC_API_KEY") != "test-key":
            raise AssertionError("Anthropic key was not available during model initialization")
        return EnvCheckingModel()


class GoogleEnvCheckingClient(LangChainChatModelClient):
    def _init_chat_model(self) -> object:
        if os.environ.get("GOOGLE_API_KEY") != "google-test-key":
            raise AssertionError("Google key was not available during model initialization")
        return GoogleEnvCheckingModel()


class OpenAIEnvCheckingClient(LangChainChatModelClient):
    def _init_chat_model(self) -> object:
        if os.environ.get("OPENAI_API_KEY") != "openai-test-key":
            raise AssertionError("OpenAI key was not available during model initialization")
        return OpenAIEnvCheckingModel()


class LearningLLMClientsTest(unittest.TestCase):
    def test_anthropic_provider_requires_api_key(self) -> None:
        settings = DiracDataSettings(llm_provider="anthropic", anthropic_api_key=None)
        with self.assertRaises(ValueError):
            chat_model_client_from_settings(settings)

    def test_anthropic_provider_builds_client_from_settings(self) -> None:
        settings = DiracDataSettings(
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-6",
            llm_max_tokens=123,
            llm_temperature=0.2,
            anthropic_api_key="test-key",
        )
        client = chat_model_client_from_settings(settings)
        self.assertEqual(client.model, "claude-sonnet-4-6")
        self.assertIsInstance(client, LangChainChatModelClient)
        self.assertEqual(client.max_tokens, 123)
        self.assertEqual(client.temperature, 0.2)
        self.assertEqual(client.model_provider, "anthropic")

    def test_google_provider_requires_api_key(self) -> None:
        settings = DiracDataSettings(llm_provider="google_genai", google_api_key=None)
        with self.assertRaises(ValueError):
            chat_model_client_from_settings(settings)

    def test_google_provider_builds_client_from_settings(self) -> None:
        settings = DiracDataSettings(
            llm_provider="google_genai",
            llm_model="gemini-2.5-flash",
            llm_max_tokens=123,
            llm_temperature=0.2,
            google_api_key="google-test-key",
        )
        client = chat_model_client_from_settings(settings)
        self.assertEqual(client.model, "gemini-2.5-flash")
        self.assertIsInstance(client, LangChainChatModelClient)
        self.assertEqual(client.max_tokens, 123)
        self.assertEqual(client.temperature, 0.2)
        self.assertEqual(client.model_provider, "google_genai")

    def test_openai_provider_requires_api_key(self) -> None:
        settings = DiracDataSettings(llm_provider="openai", openai_api_key=None)
        with self.assertRaises(ValueError):
            chat_model_client_from_settings(settings)

    def test_openai_provider_builds_client_from_settings(self) -> None:
        settings = DiracDataSettings(
            llm_provider="openai",
            llm_model="gpt-5-nano",
            llm_max_tokens=123,
            llm_temperature=0.2,
            openai_api_key="openai-test-key",
        )
        client = chat_model_client_from_settings(settings)
        self.assertEqual(client.model, "gpt-5-nano")
        self.assertIsInstance(client, LangChainChatModelClient)
        self.assertEqual(client.max_tokens, 123)
        self.assertEqual(client.temperature, 0.2)
        self.assertEqual(client.model_provider, "openai")

    def test_bedrock_provider_builds_client_from_settings(self) -> None:
        settings = DiracDataSettings(
            llm_provider="bedrock_converse",
            llm_model="anthropic.claude-sonnet-4-6",
            llm_max_tokens=123,
            llm_temperature=0.2,
            bedrock_region="ap-south-1",
            bedrock_api_key="bedrock-test-key",
        )
        client = chat_model_client_from_settings(settings)
        self.assertEqual(client.model, "anthropic.claude-sonnet-4-6")
        self.assertIsInstance(client, LangChainChatModelClient)
        self.assertEqual(client.max_tokens, 123)
        self.assertEqual(client.temperature, 0.2)
        self.assertEqual(client.model_provider, "bedrock_converse")
        self.assertEqual(client.model_kwargs["region_name"], "ap-south-1")

    def test_learning_model_profile_builds_bedrock_client(self) -> None:
        settings = DiracDataSettings(
            llm_model_profile="bedrock_anthropic_sonnet_46_ap_south_1",
            llm_max_tokens=456,
            llm_temperature=0.1,
            bedrock_api_key="bedrock-test-key",
        )
        client = chat_model_client_from_settings(settings)
        self.assertEqual(client.model, "global.anthropic.claude-sonnet-4-6")
        self.assertEqual(client.model_provider, "bedrock_converse")
        self.assertEqual(client.max_tokens, 456)
        self.assertEqual(client.temperature, 0.1)
        self.assertEqual(client.api_key, "bedrock-test-key")
        self.assertEqual(client.model_kwargs["region_name"], "ap-south-1")

    def test_unknown_learning_model_profile_fails(self) -> None:
        settings = DiracDataSettings(llm_model_profile="missing-profile")
        with self.assertRaises(ValueError) as context:
            chat_model_client_from_settings(settings)
        self.assertIn("missing-profile", str(context.exception))

    def test_anthropic_key_is_scoped_across_init_and_invoke(self) -> None:
        old_value = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            client = EnvCheckingClient(
                model="claude-sonnet-4-6",
                model_provider="anthropic",
                max_tokens=100,
                api_key="test-key",
            )
            self.assertEqual(client.complete([ChatModelMessage("user", "hello")]), "ok")
            self.assertNotIn("ANTHROPIC_API_KEY", os.environ)
        finally:
            if old_value is not None:
                os.environ["ANTHROPIC_API_KEY"] = old_value

    def test_google_key_is_scoped_across_init_and_invoke(self) -> None:
        old_google = os.environ.pop("GOOGLE_API_KEY", None)
        old_gemini = os.environ.pop("GEMINI_API_KEY", None)
        try:
            client = GoogleEnvCheckingClient(
                model="gemini-2.5-flash",
                model_provider="google_genai",
                max_tokens=100,
                api_key="google-test-key",
            )
            self.assertEqual(client.complete([ChatModelMessage("user", "hello")]), "ok")
            self.assertNotIn("GOOGLE_API_KEY", os.environ)
        finally:
            if old_google is not None:
                os.environ["GOOGLE_API_KEY"] = old_google
            if old_gemini is not None:
                os.environ["GEMINI_API_KEY"] = old_gemini

    def test_openai_key_is_scoped_across_init_and_invoke(self) -> None:
        old_value = os.environ.pop("OPENAI_API_KEY", None)
        try:
            client = OpenAIEnvCheckingClient(
                model="gpt-5-nano",
                model_provider="openai",
                max_tokens=100,
                api_key="openai-test-key",
            )
            self.assertEqual(client.complete([ChatModelMessage("user", "hello")]), "ok")
            self.assertNotIn("OPENAI_API_KEY", os.environ)
        finally:
            if old_value is not None:
                os.environ["OPENAI_API_KEY"] = old_value
