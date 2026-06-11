import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import DiracDataSettings
from diracdata.llms import ChatModelFactory, ModelProvider


@unittest.skipUnless(
    os.environ.get("DIRACDATA_RUN_LIVE_GEMINI") == "1",
    "set DIRACDATA_RUN_LIVE_GEMINI=1 to run live Gemini smoke tests",
)
class GeminiSmokeTest(unittest.TestCase):
    def test_gemini_flash_lite_connection(self) -> None:
        api_key = (
            os.environ.get("DIRACDATA_GOOGLE_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )
        self.assertIsNotNone(api_key)
        settings = DiracDataSettings(
            agent_model_profile="gemini_2_5_flash_lite",
            google_api_key=api_key,
            agent_llm_max_tokens=64,
            agent_llm_temperature=0.0,
        )

        factory = ChatModelFactory(settings=settings)
        resolved = factory.resolve_agent_model()
        self.assertEqual(resolved.provider, ModelProvider.GOOGLE_GENAI)

        model = factory.create_agent_chat_model()
        response = model.invoke([{"role": "user", "content": "Reply with exactly: gemini-ok"}])

        self.assertIn("gemini-ok", str(getattr(response, "content", response)).lower())


if __name__ == "__main__":
    unittest.main()
