"""LangGraph checkpointer factory for agents."""

from __future__ import annotations

from diracdata.config.settings import DiracDataSettings


def checkpointer_from_settings(settings: DiracDataSettings) -> object | None:
    kind = settings.agent_checkpointer.lower()
    if kind in {"", "none", "off"}:
        return None
    if kind == "memory":
        try:
            from langgraph.checkpoint.memory import InMemorySaver
        except ImportError as exc:
            raise RuntimeError("In-memory checkpointer requires langgraph") from exc

        return InMemorySaver()
    raise ValueError(f"Unsupported DIRACDATA_AGENT_CHECKPOINTER: {settings.agent_checkpointer}")
