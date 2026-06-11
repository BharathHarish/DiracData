"""LangGraph store factory for agents."""

from __future__ import annotations

from diracdata.config.settings import DiracDataSettings


def store_from_settings(settings: DiracDataSettings) -> object | None:
    kind = settings.agent_store.lower()
    if kind in {"", "none", "off"}:
        return None
    if kind == "memory":
        try:
            from langgraph.store.memory import InMemoryStore
        except ImportError as exc:
            raise RuntimeError("In-memory store requires langgraph") from exc

        return InMemoryStore()
    raise ValueError(f"Unsupported DIRACDATA_AGENT_STORE: {settings.agent_store}")
