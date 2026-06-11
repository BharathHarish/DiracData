"""Agent runtime settings and streaming mode parsing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from diracdata.config.settings import DiracDataSettings


class AgentStreaming(StrEnum):
    OFF = "off"
    ON = "on"


class LangGraphStreamMode(StrEnum):
    VALUES = "values"
    UPDATES = "updates"
    MESSAGES = "messages"
    CUSTOM = "custom"
    CHECKPOINTS = "checkpoints"
    TASKS = "tasks"
    DEBUG = "debug"


@dataclass(frozen=True)
class AgentRuntimeSettings:
    """Resolved settings for answer-time agent execution."""

    streaming: AgentStreaming
    stream_modes: list[LangGraphStreamMode]
    stream_version: str
    checkpointer: str
    store: str
    schema_search_limit: int
    profile_values_limit: int
    sql_max_rows: int
    sql_timeout_seconds: int

    @classmethod
    def from_settings(cls, settings: DiracDataSettings) -> "AgentRuntimeSettings":
        return cls(
            streaming=_parse_streaming(settings.agent_streaming),
            stream_modes=parse_stream_modes(settings.agent_stream_modes),
            stream_version=settings.agent_stream_version,
            checkpointer=settings.agent_checkpointer.lower(),
            store=settings.agent_store.lower(),
            schema_search_limit=settings.agent_schema_search_limit,
            profile_values_limit=settings.agent_profile_values_limit,
            sql_max_rows=settings.agent_sql_max_rows,
            sql_timeout_seconds=settings.agent_sql_timeout_seconds,
        )


def parse_stream_modes(value: str | list[str] | tuple[str, ...] | None) -> list[LangGraphStreamMode]:
    """Parse and validate LangGraph stream modes."""
    if value is None:
        return [LangGraphStreamMode.UPDATES, LangGraphStreamMode.MESSAGES]
    if isinstance(value, str):
        raw_modes = [part.strip() for part in value.split(",")]
    else:
        raw_modes = [str(part).strip() for part in value]

    modes = []
    for raw_mode in raw_modes:
        if not raw_mode:
            continue
        try:
            mode = LangGraphStreamMode(raw_mode)
        except ValueError as exc:
            valid = ", ".join(mode.value for mode in LangGraphStreamMode)
            raise ValueError(f"Unsupported LangGraph stream mode {raw_mode!r}. Valid: {valid}") from exc
        if mode not in modes:
            modes.append(mode)
    if not modes:
        raise ValueError("At least one stream mode is required when streaming is enabled")
    return modes


def stream_mode_values(modes: list[LangGraphStreamMode]) -> list[str]:
    return [mode.value for mode in modes]


def _parse_streaming(value: str) -> AgentStreaming:
    normalized = value.strip().lower()
    if normalized in {"", "off", "false", "0", "no"}:
        return AgentStreaming.OFF
    if normalized in {"on", "true", "1", "yes"}:
        return AgentStreaming.ON
    raise ValueError("DIRACDATA_AGENT_STREAMING must be off/on")
