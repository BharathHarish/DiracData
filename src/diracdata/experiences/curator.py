"""The Curator -- the agentic fold that keeps `experiences.md` relevant and succinct. Given a finished
turn's trace, a small tool loop (LLM judgement, prompts/curate.md) reads the current book and makes
targeted section edits: add / refine / merge / prune -- or nothing, when the turn taught nothing.

Self-contained: uses `streaming.collect` for the model calls and its own minimal loop, so the
experiences package never imports the agent loop.
"""

from __future__ import annotations

from typing import Any, Callable

from diracdata.config import Config
from diracdata.experiences.book import ExperienceBook
from diracdata.prompts import load_prompt
from diracdata.streaming import collect
from diracdata.utils.streaming import Sink, null_sink, to_ai_message

_CURATE_PROMPT = load_prompt("curate")

# curate(book, candidate_md) -> None
Curate = Callable[[ExperienceBook, str], None]


def make_curator(model: Any, config: Config, *, sink: Sink = null_sink) -> Curate:
    """Return curate(book, candidate_md). One tool loop by `model` over read/update tools bound to the
    given book. Router/agent pass their main model; it runs in the background consolidator thread."""
    from langchain.tools import tool

    def curate(book: ExperienceBook, candidate_md: str) -> None:
        @tool("read_experiences")
        def read_experiences() -> str:
            """Return the current experiences.md for this schema (all sections). Call this FIRST."""
            return book.read() or "(the knowledge doc is currently empty)"

        @tool("update_experiences")
        def update_experiences(section: str, body: str) -> str:
            """REPLACE one section's body with `body` (markdown bullets); empty body deletes it. Write
            the FULL new body for the section, not just the added line."""
            book.update_section(section, body)
            return f"section '{section.upper()}' updated ({len(body)} chars)"

        _run(model, [read_experiences, update_experiences], candidate_md, config, sink)

    return curate


def _run(model: Any, tools: list, candidate_md: str, config: Config, sink: Sink) -> None:
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    bound = model.bind_tools(tools)
    by_name = {t.name: t for t in tools}
    task = ("A conversation turn just finished. Decide what durable knowledge, if any, to keep.\n\n"
            "TURN TRACE:\n" + candidate_md)
    convo: list[Any] = [SystemMessage(content=_CURATE_PROMPT), HumanMessage(content=task)]
    for i in range(config.curator_max_steps):
        last = i == config.curator_max_steps - 1
        out = collect(model=(model if last else bound), messages=convo, stage="curate",
                      sink=sink, config=config)
        convo.append(out.get("message") or to_ai_message(out["text"], out["tool_calls"]))
        if not out["tool_calls"] or last:
            return
        for call in out["tool_calls"]:
            name, args = call.get("name", ""), call.get("args", {}) or {}
            handler = by_name.get(name)
            try:
                obs = str(handler.invoke(args)) if handler else f"no such tool: {name}"
            except Exception as exc:  # noqa: BLE001
                obs = f"tool '{name}' errored: {type(exc).__name__}: {exc}"
            convo.append(ToolMessage(content=obs[:config.obs_cap], tool_call_id=call.get("id", name)))
