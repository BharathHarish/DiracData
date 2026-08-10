#!/usr/bin/env python3
"""ONE-FILE DEMO of the *proposed* clean DiracData developer API (not committed to the package -- this
file prototypes the ergonomics as a thin facade over the existing packages, and RUNS on ecommerce).

    PYTHONPATH=src .venv/bin/python scripts/diracdata_demo.py

Shows:
  1. model providers      ->  FireworksAI("deepseek-v4-flash", api_key=...)   (api_key falls back to ENV)
  2. raw chat             ->  model.chat("hello")                             (like chat.completions)
  3. analyst over a lake  ->  Analyst(schema="ecommerce", model=model).ask("...")
  4. conversation cont.   ->  Analyst(..., conversation="sess-1")             (checkpoint -> follow-ups resolve)
  5. experiential memory  ->  Analyst(..., memory=True)
"""
from __future__ import annotations

import sys
import uuid
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diracdata.config import settings_from_env


# =================================================================================================
# PROPOSED API #1 -- diracdata.model_providers  (PascalCase provider classes, api_key or ENV)
# =================================================================================================
class _Provider:
    """A provider = (alias->profile map, which Config field holds its api_key). Returns a chat model,
    and offers a dead-simple .chat() like OpenAI's chat.completions."""
    _key_field = ""
    _aliases: dict = {}

    def __init__(self, model: str, *, api_key: str | None = None, env_file: str = str(ROOT / ".env")):
        self.profile_id = self._aliases.get(model, model)      # accept a friendly alias OR a raw profile id
        self._api_key = api_key
        self._env_file = env_file

    def settings(self):
        s = settings_from_env(self._env_file)
        s = replace(s, agent_model_profile=self.profile_id)
        if self._api_key:                                      # explicit key overrides ENV
            s = replace(s, **{self._key_field: self._api_key})
        return s

    def build(self):
        from diracdata.models import chat_model
        return chat_model(self.profile_id, settings=self.settings())

    def chat(self, prompt: str) -> str:                       # raw single-turn chat (no warehouse)
        return self.build().invoke(prompt).content


class FireworksAI(_Provider):
    _key_field = "fireworks_api_key"
    _aliases = {"deepseek-v4-flash": "fireworks_deepseek_v4_flash",
                "gpt-oss-120b": "fireworks_gpt_oss_120b",
                "glm-5p2": "fireworks_glm_5p2",
                "kimi-k2p7": "fireworks_kimi_k2p7_code"}


class Anthropic(_Provider):
    _key_field = "anthropic_api_key"
    _aliases = {"haiku": "anthropic_haiku_45", "sonnet-5": "anthropic_sonnet_5"}


class OpenAI(_Provider):
    _key_field = "openai_api_key"
    _aliases = {"gpt-5-mini": "openai_gpt_5_4_mini", "gpt-5-nano": "openai_gpt_5_4_nano"}


# =================================================================================================
# PROPOSED API #2 -- diracdata.Analyst  (one facade; checkpoints + memory are constructor flags)
# =================================================================================================
class Analyst:
    """Ask analytics questions over a lake schema. `conversation=<id>` turns on continuity (a
    checkpoint); `memory=True` turns on experiential memory. All the stores/engines/context/runtime
    wiring is hidden here -- this is the whole public surface a user needs."""

    def __init__(self, *, schema: str, model, conversation: str | None = None, memory: bool = False,
                 env_file: str = str(ROOT / ".env")):
        from diracdata.stores import store_from_settings
        from diracdata.engines import DuckDBEngine, SourceRegistry
        from diracdata.context.fabric import fabric_store_from_settings
        from diracdata.context.workspace import Workspace
        from diracdata.context.valuecache import ColumnValueCache
        from diracdata.runtime.results import ResultStore
        from diracdata.checkpoints import Conversation
        from diracdata.memory import ExperienceBook
        from diracdata.execution import make_executor
        from diracdata.streaming import mode_sink
        from diracdata.agent import Agent

        settings = model.settings() if isinstance(model, _Provider) else settings_from_env(env_file)
        if memory:                                            # enable the experiential-memory subsystem
            settings = replace(settings, agentic_memory_enabled=True)
        llm = model.build() if isinstance(model, _Provider) else model

        store = store_from_settings(settings)
        engine = DuckDBEngine.from_settings(settings, schema)  # object-store-native (lake_source from ENV)
        registry = SourceRegistry.of(engine)
        fabric = fabric_store_from_settings(settings)
        workspace = Workspace.from_store(store=fabric, schema=schema)
        result_store = ResultStore(engine=engine, store=store, schema=schema, sources=registry,
                                   preview_rows=settings.preview_rows, preview_all_max=settings.preview_all_max,
                                   reconciler_memory_limit=settings.reconciler_memory_limit,
                                   reconciler_temp_dir=settings.reconciler_temp_dir,
                                   reconciler_threads=settings.reconciler_threads,
                                   executor=make_executor(settings))
        book = ExperienceBook(schema, store) if memory else None

        self._agent = Agent(model=llm, workspace=workspace, engine=engine, result_store=result_store,
                            sink=mode_sink(lambda *a: None, "off"), config=settings,
                            value_cache=ColumnValueCache(fabric, schema), sources=registry,
                            experience_book=book)
        # continuity: a Conversation checkpoint, resumable across processes by reusing the same id
        self._conversation = Conversation(conversation or f"anon-{uuid.uuid4().hex[:8]}",
                                          store=store, config=settings)

    def ask(self, question: str) -> str:
        ans = self._agent.run(question, conversation=self._conversation)
        self._agent.flush_memory()
        return ans.answer


# =================================================================================================
# DEMO
# =================================================================================================
def main() -> int:
    # 1. pick a model -- provider class, api_key falls back to ENV (DIRACDATA_FIREWORKS_API_KEY)
    model = FireworksAI("deepseek-v4-flash")           # or FireworksAI("deepseek-v4-flash", api_key="fw_...")

    # 2. raw chat, no warehouse -- the chat.completions analog
    print("RAW CHAT:", model.chat("In one word, say hi.").strip()[:60])

    # 3+4+5. an analyst over ecommerce, WITH conversation continuity + memory
    analyst = Analyst(schema="ecommerce", model=model, conversation="demo-session-1", memory=True)

    print("\nQ1:", "How many orders were placed in total?")
    print("A1:", analyst.ask("How many orders were placed in total?"))

    # the follow-up says "that" -- continuity (the checkpoint) resolves it to the prior metric
    print("\nQ2 (follow-up, relies on continuity):", "And what is the total net revenue for those orders?")
    print("A2:", analyst.ask("And what is the total net revenue for those orders?"))

    print("\nDONE -- providers + Analyst + checkpoint + memory all ran on ecommerce.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
