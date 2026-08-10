"""diracdata.prebuilt -- the one-liner analyst (the create_react_agent equivalent).

    from diracdata.agents import data_analyst
    from diracdata.model_providers import FireworksAI

    analyst = data_analyst(
        schema="ecommerce",                       # a lake schema (data + learned context in the object store)
        model=FireworksAI("deepseek-v4-flash"),   # or "profile_id", or a chat model, or None (ENV default)
        conversation="sess-1",                    # checkpoint id -> continuity across turns/sessions
        memory=True,                              # experiential memory (async curator writes back)
        garden=["fireworks_deepseek_v4_flash", "fireworks_glm_5p2"],  # router garden (optional)
        stream=True,                              # live tool-call / router stream to stderr
    )
    print(analyst.ask("Which market has the weakest gross profit, and why?"))

Everything the wired UAT assembled by hand (stores, engine, models, context, runtime, checkpoints,
memory) is hidden here. `data_analyst(...)` returns a `DataAnalyst`; `.ask(q)` answers one question,
`.run(q)` returns the full Answer (answer + tokens + steps).
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import replace
from typing import Any


class DataAnalyst:
    def __init__(self, agent: Any, conversation: Any) -> None:
        self._agent = agent
        self._conversation = conversation

    def run(self, question: str):
        ans = self._agent.run(question, conversation=self._conversation)
        self._agent.flush_memory()
        return ans

    def ask(self, question: str) -> str:
        return self.run(question).answer


def _stderr_sink():
    def sink(stage: str, kind: str, text: str) -> None:
        if kind == "tool_call":
            print(f"  >> {text}", file=sys.stderr, flush=True)
        elif kind == "info":
            print(f"  [{stage}] {text}", file=sys.stderr, flush=True)
    return sink


def data_analyst(*, schema: str, model: Any = None, conversation: str | None = None,
                 memory: bool = False, garden: list | tuple | None = None, router: bool | None = None,
                 settings: Any = None, env_file: str | None = None, stream: bool = False,
                 engine: Any = None) -> DataAnalyst:
    """Build a ready-to-ask analyst over `schema`. Checkpoints (`conversation`), experiential memory
    (`memory`), and the router `garden` are just kwargs -- all the wiring is hidden."""
    from diracdata.config import settings_from_env
    from diracdata.model_providers import _Provider
    from diracdata.models import chat_model
    from diracdata.stores import store_from_settings
    from diracdata.engines import DuckDBEngine, SourceRegistry
    from diracdata.context import context_store_from_settings
    from diracdata.context.workspace import Workspace
    from diracdata.context.valuecache import ColumnValueCache
    from diracdata.runtime.results import ResultStore
    from diracdata.checkpoints import Conversation
    from diracdata.memory import ExperienceBook
    from diracdata.execution import make_executor
    from diracdata.streaming import mode_sink
    from diracdata.agent import Agent

    settings = settings if settings is not None else settings_from_env(env_file)
    if isinstance(model, _Provider):
        settings = model.apply(settings)
    elif isinstance(model, str):
        settings = replace(settings, agent_model_profile=model)
    if memory:
        settings = replace(settings, agentic_memory_enabled=True)
    if garden is not None:
        settings = replace(settings, router_garden=tuple(garden))
    if router is not None:
        settings = replace(settings, router_enabled=router)

    llm = (model.build(settings) if isinstance(model, _Provider)
           else model if (model is not None and not isinstance(model, str))
           else chat_model(model, settings=settings))

    store = store_from_settings(settings)
    engine = engine or DuckDBEngine.from_settings(settings, schema)   # object-store-native (lake_source from ENV)
    registry = SourceRegistry.of(engine)
    fabric = context_store_from_settings(settings)               # learned context, object-store only
    workspace = Workspace.from_store(store=fabric, schema=schema)
    result_store = ResultStore(engine=engine, store=store, schema=schema, sources=registry,
                               preview_rows=settings.preview_rows, preview_all_max=settings.preview_all_max,
                               reconciler_memory_limit=settings.reconciler_memory_limit,
                               reconciler_temp_dir=settings.reconciler_temp_dir,
                               reconciler_threads=settings.reconciler_threads,
                               executor=make_executor(settings))
    book = ExperienceBook(schema, store) if memory else None
    sink = mode_sink(_stderr_sink() if stream else (lambda *a: None), "updates" if stream else "off")

    agent = Agent(model=llm, workspace=workspace, engine=engine, result_store=result_store, sink=sink,
                  config=settings, value_cache=ColumnValueCache(fabric, schema), sources=registry,
                  experience_book=book)
    conv = Conversation(conversation or f"anon-{uuid.uuid4().hex[:8]}", store=store, config=settings)
    return DataAnalyst(agent, conv)
