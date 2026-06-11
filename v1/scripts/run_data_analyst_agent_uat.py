"""Run the data analyst agent UAT for a natural-language business question."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.agents import create_data_analyst_agent
from diracdata.agents.artifacts import LearnedArtifactRepository
from diracdata.agents.settings import AgentRuntimeSettings, AgentStreaming, parse_stream_modes
from diracdata.config import DiracDataSettings, settings_from_env
from diracdata.llms import ChatModelFactory
from diracdata.query_engines import query_engine_from_settings
from diracdata.storage import object_store_from_settings


DEFAULT_QUESTION = "count all male customers from california"
EXIT_COMMANDS = {"exit", "quit"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument(
        "--follow-up",
        action="append",
        default=[],
        help="Scripted follow-up question to ask on the same thread. May be repeated.",
    )
    parser.add_argument("--thread-id", default=None)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--catalog", default=None, help="Override DIRACDATA_CATALOG for this run.")
    parser.add_argument("--database", default=None, help="Override DIRACDATA_DATABASE for this run.")
    parser.add_argument("--schema", default=None, help="Override DIRACDATA_SCHEMA for this run.")
    parser.add_argument(
        "--catalog-config",
        type=Path,
        default=None,
        help="Override DIRACDATA_CATALOG_CONFIG for this run.",
    )
    parser.add_argument(
        "--agent-model-profile",
        default=None,
        help=(
            "Named model profile. Examples: anthropic_haiku_45, anthropic_sonnet_46, "
            "bedrock_qwen3_next_80b_a3b_ap_south_1, "
            "bedrock_qwen3_coder_480b_a35b_ap_south_1, "
            "bedrock_gemma_3_12b_it_ap_south_1, "
            "bedrock_openai_gpt_oss_120b_ap_south_1, bedrock_zai_glm_5_ap_south_1, "
            "bedrock_meta_llama3_70b_instruct_ap_south_1, gemini_2_5_flash, "
            "gemini_3_5_flash, openai_gpt_5_4_mini, openai_gpt_5_4_nano, "
            "openai_gpt_5_nano, openai_gpt_5_mini."
        ),
    )
    parser.add_argument(
        "--agent-llm-provider",
        default=None,
        help="Override DIRACDATA_AGENT_LLM_PROVIDER for this run.",
    )
    parser.add_argument(
        "--agent-model",
        "--model",
        dest="agent_model",
        default=None,
        help="Override DIRACDATA_AGENT_LLM_MODEL for this run.",
    )
    parser.add_argument(
        "--agent-max-tokens",
        type=int,
        default=None,
        help="Override DIRACDATA_AGENT_LLM_MAX_TOKENS for this run.",
    )
    parser.add_argument(
        "--agent-temperature",
        type=float,
        default=None,
        help="Override DIRACDATA_AGENT_LLM_TEMPERATURE for this run.",
    )
    parser.add_argument(
        "--bedrock-region",
        default=None,
        help="Override DIRACDATA_BEDROCK_REGION for Bedrock model profiles.",
    )
    parser.add_argument(
        "--list-model-profiles",
        action="store_true",
        help="Print available named model profiles and exit.",
    )
    parser.add_argument(
        "--stream-modes",
        default=None,
        help=(
            "Comma-separated LangGraph stream modes. Valid modes: values, updates, "
            "messages, custom, checkpoints, tasks, debug."
        ),
    )
    parser.add_argument("--trace-jsonl", type=Path, default=None)
    parser.add_argument(
        "--interactive",
        dest="interactive",
        action="store_true",
        default=None,
        help="Keep the session open and prompt for follow-up questions.",
    )
    parser.add_argument(
        "--no-interactive",
        dest="interactive",
        action="store_false",
        help="Exit after the initial question and scripted follow-ups.",
    )
    parser.add_argument(
        "--print-raw-events",
        action="store_true",
        help="Print full v2 StreamPart JSON events to stdout. Full events are always written to trace-jsonl when provided.",
    )
    parser.add_argument("--stream", dest="stream", action="store_true", default=None)
    parser.add_argument("--no-stream", dest="stream", action="store_false")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = _settings_with_agent_overrides(settings_from_env(args.env_file), args)
    if args.list_model_profiles:
        _print_model_profiles(settings)
        return
    object_store = object_store_from_settings(settings, create_bucket_if_missing=False)
    repository = LearnedArtifactRepository(settings=settings, object_store=object_store)
    preflight = repository.preflight()
    runtime_settings = AgentRuntimeSettings.from_settings(settings)
    resolved_model = ChatModelFactory(settings=settings).resolve_agent_model()
    stream_modes = (
        parse_stream_modes(args.stream_modes)
        if args.stream_modes is not None
        else runtime_settings.stream_modes
    )
    should_stream = (
        args.stream
        if args.stream is not None
        else runtime_settings.streaming == AgentStreaming.ON
    )
    interactive = _interactive_enabled(args.interactive)

    questions = [args.question, *args.follow_up]
    header = {
        "question": args.question,
        "follow_ups": args.follow_up,
        "question_count": len(questions),
        "thread_id": args.thread_id or "auto",
        "catalog": settings.catalog,
        "database": settings.database,
        "schema": settings.schema,
        "query_engine": settings.query_engine,
        "sql_dialect": settings.sql_dialect,
        "object_store": settings.object_store,
        "agent_model_profile": settings.agent_model_profile,
        "agent_llm_provider": settings.agent_llm_provider,
        "agent_llm_model": settings.agent_llm_model,
        "agent_llm_max_tokens": settings.agent_llm_max_tokens,
        "agent_llm_temperature": settings.agent_llm_temperature,
        "resolved_agent_model_profile": resolved_model.profile_id,
        "resolved_agent_llm_provider": resolved_model.provider.value,
        "resolved_agent_llm_model": resolved_model.model,
        "resolved_agent_region": resolved_model.region_name,
        "resolved_agent_is_moe": resolved_model.is_moe,
        "resolved_agent_max_tokens": resolved_model.max_tokens,
        "resolved_agent_supports_tool_use": resolved_model.supports_tool_use,
        "resolved_agent_supports_streaming": resolved_model.supports_streaming,
        "bedrock_region": settings.bedrock_region,
        "stream": should_stream,
        "stream_modes": [mode.value for mode in stream_modes],
        "stream_version": runtime_settings.stream_version,
        "interactive": interactive,
        "preflight": preflight,
    }
    print("Data Analyst Agent UAT", flush=True)
    print(json.dumps(header, indent=2), flush=True)
    if not all(preflight.values()):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": "active learning artifacts are missing",
                    "preflight": preflight,
                },
                indent=2,
            ),
            flush=True,
        )
        raise SystemExit(2)
    if not resolved_model.supports_tool_use:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": "selected model profile does not support tool use",
                    "profile_id": resolved_model.profile_id,
                    "model": resolved_model.model,
                },
                indent=2,
            ),
            flush=True,
        )
        raise SystemExit(2)

    trace_writer = _TraceWriter(args.trace_jsonl)
    query_engine = query_engine_from_settings(settings)
    thread_id = args.thread_id or f"uat-{uuid4().hex[:12]}"
    try:
        agent = create_data_analyst_agent(
            settings=settings,
            object_store=object_store,
            query_engine=query_engine,
        )
        turn_summaries = []
        final_state: dict[str, object] = {}
        turn_index = 0
        for turn_index, question in enumerate(questions, start=1):
            final_state = _run_turn(
                agent=agent,
                question=question,
                thread_id=thread_id,
                turn_index=turn_index,
                total_turns=len(questions),
                should_stream=should_stream,
                stream_modes=",".join(mode.value for mode in stream_modes),
                trace_writer=trace_writer,
                print_raw_events=args.print_raw_events,
            )
            turn_summaries.append(_turn_summary(turn_index, question, final_state))

        if interactive:
            print(
                "\nInteractive follow-up mode. Type 'exit' or 'quit' to end the session.",
                flush=True,
            )
            while True:
                try:
                    question = input("\nFollow-up> ").strip()
                except EOFError:
                    print("\nEOF received. Ending session.", flush=True)
                    break
                if not question:
                    continue
                if question.lower() in EXIT_COMMANDS:
                    print("Ending session.", flush=True)
                    break
                turn_index += 1
                final_state = _run_turn(
                    agent=agent,
                    question=question,
                    thread_id=thread_id,
                    turn_index=turn_index,
                    total_turns=None,
                    should_stream=should_stream,
                    stream_modes=",".join(mode.value for mode in stream_modes),
                    trace_writer=trace_writer,
                    print_raw_events=args.print_raw_events,
                )
                turn_summaries.append(_turn_summary(turn_index, question, final_state))
    finally:
        query_engine.close()
        trace_writer.close()

    final_answer = _final_answer(final_state)
    summary = {
        "status": "passed",
        "thread_id": thread_id,
        "turn_count": len(turn_summaries),
        "turns": turn_summaries,
        "final_answer": final_answer,
        "trace_jsonl": str(args.trace_jsonl) if args.trace_jsonl else None,
    }
    print("Final Answer", flush=True)
    print(final_answer, flush=True)
    print("UAT Summary", flush=True)
    print(json.dumps(summary, indent=2), flush=True)


def _settings_with_agent_overrides(
    settings: DiracDataSettings,
    args: argparse.Namespace,
) -> DiracDataSettings:
    updates = {}
    if args.catalog is not None:
        updates["catalog"] = args.catalog
    if args.database is not None:
        updates["database"] = args.database
    if args.schema is not None:
        updates["schema"] = args.schema
    if args.catalog_config is not None:
        updates["catalog_config"] = args.catalog_config
    if args.agent_model_profile is not None:
        updates["agent_model_profile"] = args.agent_model_profile or None
    elif args.agent_llm_provider is not None or args.agent_model is not None:
        updates["agent_model_profile"] = None
    if args.agent_llm_provider is not None:
        updates["agent_llm_provider"] = args.agent_llm_provider
    if args.agent_model is not None:
        updates["agent_llm_model"] = args.agent_model
    if args.agent_max_tokens is not None:
        updates["agent_llm_max_tokens"] = args.agent_max_tokens
    if args.agent_temperature is not None:
        updates["agent_llm_temperature"] = args.agent_temperature
    if args.bedrock_region is not None:
        updates["bedrock_region"] = args.bedrock_region or None
    return replace(settings, **updates) if updates else settings


def _print_model_profiles(settings: DiracDataSettings) -> None:
    factory = ChatModelFactory(settings=settings)
    rows = [
        {
            "profile_id": profile.profile_id,
            "provider": profile.provider.value,
            "model": profile.model,
            "region_name": profile.region_name,
            "supports_tool_use": profile.supports_tool_use,
            "supports_streaming": profile.supports_streaming,
            "is_moe": profile.is_moe,
        }
        for profile in factory.available_profiles()
    ]
    print(json.dumps({"model_profiles": rows}, indent=2), flush=True)


def _run_turn(
    *,
    agent: object,
    question: str,
    thread_id: str,
    turn_index: int,
    total_turns: int | None,
    should_stream: bool,
    stream_modes: str,
    trace_writer: "_TraceWriter",
    print_raw_events: bool,
) -> dict[str, object]:
    turn_label = f"{turn_index}/{total_turns}" if total_turns is not None else str(turn_index)
    print(f"Turn {turn_label}", flush=True)
    print(f"Question: {question}", flush=True)
    trace_writer.write(
        {
            "type": "turn_start",
            "turn_index": turn_index,
            "thread_id": thread_id,
            "question": question,
        }
    )
    if should_stream:
        final_state = _run_streaming(
            agent=agent,
            question=question,
            thread_id=thread_id,
            stream_modes=stream_modes,
            trace_writer=trace_writer,
            print_raw_events=print_raw_events,
        )
    else:
        result = agent.invoke(question, thread_id=thread_id)
        final_state = _result_value(result)
        trace_writer.write(
            {
                "type": "invoke_result",
                "turn_index": turn_index,
                "thread_id": thread_id,
                "data": _jsonable(final_state),
            }
        )
    final_answer = _final_answer(final_state)
    trace_writer.write(
        {
            "type": "turn_end",
            "turn_index": turn_index,
            "thread_id": thread_id,
            "final_answer": final_answer,
        }
    )
    print("Turn Final Answer", flush=True)
    print(final_answer, flush=True)
    return final_state


def _turn_summary(
    turn_index: int,
    question: str,
    final_state: dict[str, object],
) -> dict[str, object]:
    return {
        "turn_index": turn_index,
        "question": question,
        "final_answer": _final_answer(final_state),
    }


def _interactive_enabled(value: bool | None) -> bool:
    if value is not None:
        return value
    return sys.stdin.isatty()


def _run_streaming(
    *,
    agent: object,
    question: str,
    thread_id: str,
    stream_modes: str,
    trace_writer: "_TraceWriter",
    print_raw_events: bool,
) -> dict[str, object]:
    final_state: dict[str, object] = {}
    token_buffer: list[str] = []
    for chunk in agent.stream(
        question,
        thread_id=thread_id,
        stream_modes=stream_modes,
    ):
        event = _jsonable(chunk)
        trace_writer.write(event)
        if print_raw_events:
            print(json.dumps(event, sort_keys=True), flush=True)
        else:
            _print_compact_stream_event(chunk)
        if isinstance(chunk, dict) and chunk.get("type") in {"values", "updates"}:
            data = chunk.get("data")
            if isinstance(data, dict):
                if chunk["type"] == "values":
                    final_state = data
                else:
                    final_state = _state_from_update(final_state, data)
        elif isinstance(chunk, dict) and chunk.get("type") == "messages":
            data = chunk.get("data")
            if isinstance(data, tuple) and data:
                content = getattr(data[0], "content", "")
                text = _content_text(content)
                if text:
                    token_buffer.append(text)
    if not final_state and token_buffer:
        final_state = {"messages": [{"content": "".join(token_buffer)}]}
    return final_state


def _print_compact_stream_event(chunk: object) -> None:
    if not isinstance(chunk, dict):
        return
    chunk_type = chunk.get("type")
    data = chunk.get("data")
    if chunk_type == "messages" and isinstance(data, tuple) and data:
        message = data[0]
        name = getattr(message, "name", "")
        content = getattr(message, "content", "")
        text = _content_text(content)
        if name and text:
            print(f"\n[tool:{name}] {_truncate(text, 500)}", flush=True)
        elif text:
            print(text, end="", flush=True)
        return
    if chunk_type == "updates" and isinstance(data, dict):
        for node_update in data.values():
            if not isinstance(node_update, dict):
                continue
            for message in node_update.get("messages", []):
                tool_calls = getattr(message, "tool_calls", None)
                if tool_calls:
                    tool_names = ", ".join(str(call.get("name", "")) for call in tool_calls)
                    print(f"\n[agent tool calls] {tool_names}", flush=True)


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def _state_from_update(
    current: dict[str, object],
    update: dict[str, object],
) -> dict[str, object]:
    state = dict(current)
    for node_update in update.values():
        if isinstance(node_update, dict):
            state.update(node_update)
    return state


def _result_value(result: object) -> dict[str, object]:
    value = getattr(result, "value", result)
    return value if isinstance(value, dict) else {"result": value}


def _final_answer(state: dict[str, object]) -> str:
    messages = state.get("messages")
    if not isinstance(messages, list) or not messages:
        return ""
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if content:
            return _content_text(content)
        if isinstance(message, dict) and message.get("content"):
            return _content_text(message["content"])
    return ""


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content)


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    content = getattr(value, "content", None)
    if content is not None:
        payload = {
            "type": value.__class__.__name__,
            "content": _jsonable(content),
        }
        tool_calls = getattr(value, "tool_calls", None)
        if tool_calls:
            payload["tool_calls"] = _jsonable(tool_calls)
        name = getattr(value, "name", None)
        if name:
            payload["name"] = name
        return payload
    return str(value)


class _TraceWriter:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.handle = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = path.open("w", encoding="utf-8")

    def write(self, event: object) -> None:
        if self.handle is None:
            return
        self.handle.write(json.dumps(_jsonable(event), sort_keys=True) + "\n")
        self.handle.flush()

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()


if __name__ == "__main__":
    main()
