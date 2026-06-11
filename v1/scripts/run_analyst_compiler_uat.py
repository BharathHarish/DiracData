"""Run the stage-gated analyst compiler UAT for a business question."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.agents import create_analyst_compiler
from diracdata.agents.artifacts import LearnedArtifactRepository
from diracdata.agents.settings import AgentRuntimeSettings, AgentStreaming, parse_stream_modes
from diracdata.config import DiracDataSettings, settings_from_env
from diracdata.llms import ChatModelFactory
from diracdata.query_engines import query_engine_from_settings
from diracdata.storage import object_store_from_settings


DEFAULT_QUESTION = (
    "For verified users in Maharashtra, compare May 2026 TPV and payment success "
    "rate by checkout surface and payment rail for checkout product orders."
)
EXIT_COMMANDS = {"exit", "quit"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--follow-up", action="append", default=[])
    parser.add_argument("--thread-id", default=None)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--catalog", default=None)
    parser.add_argument("--database", default=None)
    parser.add_argument("--schema", default=None)
    parser.add_argument("--catalog-config", type=Path, default=None)
    parser.add_argument("--agent-model-profile", default=None)
    parser.add_argument("--agent-llm-provider", default=None)
    parser.add_argument("--agent-model", "--model", dest="agent_model", default=None)
    parser.add_argument("--agent-max-tokens", type=int, default=None)
    parser.add_argument("--agent-temperature", type=float, default=None)
    parser.add_argument("--bedrock-region", default=None)
    parser.add_argument("--list-model-profiles", action="store_true")
    parser.add_argument("--stream", dest="stream", action="store_true", default=None)
    parser.add_argument("--no-stream", dest="stream", action="store_false")
    parser.add_argument("--stream-modes", default=None)
    parser.add_argument("--trace-jsonl", type=Path, default=None)
    parser.add_argument("--print-raw-events", action="store_true")
    parser.add_argument("--interactive", dest="interactive", action="store_true", default=None)
    parser.add_argument("--no-interactive", dest="interactive", action="store_false")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = _settings_with_agent_overrides(settings_from_env(args.env_file), args)
    if args.list_model_profiles:
        _print_model_profiles(settings)
        return

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
    thread_id = args.thread_id or f"compiler-uat-{uuid4().hex[:12]}"
    questions = [args.question, *args.follow_up]

    object_store = object_store_from_settings(settings, create_bucket_if_missing=False)
    repository = LearnedArtifactRepository(settings=settings, object_store=object_store)
    preflight = repository.preflight()
    header = {
        "runtime": "analyst_compiler",
        "question": args.question,
        "follow_ups": args.follow_up,
        "thread_id": thread_id,
        "catalog": settings.catalog,
        "database": settings.database,
        "schema": settings.schema,
        "query_engine": settings.query_engine,
        "sql_dialect": settings.sql_dialect,
        "object_store": settings.object_store,
        "agent_model_profile": settings.agent_model_profile,
        "resolved_agent_llm_provider": resolved_model.provider.value,
        "resolved_agent_llm_model": resolved_model.model,
        "resolved_agent_region": resolved_model.region_name,
        "resolved_agent_is_moe": resolved_model.is_moe,
        "stream": should_stream,
        "stream_modes": [mode.value for mode in stream_modes],
        "preflight": preflight,
    }
    print("Analyst Compiler UAT", flush=True)
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

    query_engine = query_engine_from_settings(settings)
    trace_writer = _TraceWriter(args.trace_jsonl)
    try:
        runtime = create_analyst_compiler(
            settings=settings,
            object_store=object_store,
            query_engine=query_engine,
        )
        turn_summaries: list[dict[str, object]] = []
        final_state: dict[str, object] = {}
        turn_index = 0
        for turn_index, question in enumerate(questions, start=1):
            final_state = _run_turn(
                runtime=runtime,
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
            print("\nInteractive follow-up mode. Type 'exit' or 'quit' to end.", flush=True)
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
                    runtime=runtime,
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

    summary = {
        "status": "passed",
        "thread_id": thread_id,
        "turn_count": len(turn_summaries),
        "turns": turn_summaries,
        "final_answer": final_state.get("final_answer", ""),
        "trace_jsonl": str(args.trace_jsonl) if args.trace_jsonl else None,
    }
    print("Final Answer", flush=True)
    print(final_state.get("final_answer", ""), flush=True)
    print("Compiler UAT Summary", flush=True)
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
    runtime: object,
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
        state = _run_streaming(
            runtime=runtime,
            question=question,
            thread_id=thread_id,
            stream_modes=stream_modes,
            trace_writer=trace_writer,
            print_raw_events=print_raw_events,
        )
    else:
        state = runtime.invoke(question, thread_id=thread_id)
        trace_writer.write({"type": "invoke_result", "data": _jsonable(state)})
        _print_trace(state)
    print("Turn Final Answer", flush=True)
    print(state.get("final_answer", ""), flush=True)
    trace_writer.write(
        {
            "type": "turn_end",
            "turn_index": turn_index,
            "thread_id": thread_id,
            "final_answer": state.get("final_answer", ""),
        }
    )
    return state


def _run_streaming(
    *,
    runtime: object,
    question: str,
    thread_id: str,
    stream_modes: str,
    trace_writer: "_TraceWriter",
    print_raw_events: bool,
) -> dict[str, object]:
    final_state: dict[str, object] = {}
    for chunk in runtime.stream(question, thread_id=thread_id, stream_modes=stream_modes):
        event = _jsonable(chunk)
        trace_writer.write(event)
        if print_raw_events:
            print(json.dumps(event, sort_keys=True), flush=True)
        else:
            _print_compact_stream_event(event)
        if isinstance(event, dict) and event.get("type") == "values":
            data = event.get("data")
            if isinstance(data, dict):
                final_state = data
        elif isinstance(event, dict) and event.get("type") == "updates":
            data = event.get("data")
            if isinstance(data, dict):
                final_state = _state_from_update(final_state, data)
    _print_trace(final_state)
    return final_state


def _print_trace(state: dict[str, object]) -> None:
    trace = state.get("trace")
    if not isinstance(trace, list):
        return
    print("Compiler Trace", flush=True)
    for item in trace:
        if not isinstance(item, dict):
            continue
        node = item.get("node")
        details = item.get("details", {})
        print(f"- {node}: {json.dumps(details, sort_keys=True)}", flush=True)


def _print_compact_stream_event(event: object) -> None:
    if not isinstance(event, dict):
        return
    if event.get("type") == "updates":
        data = event.get("data")
        if not isinstance(data, dict):
            return
        for node, update in data.items():
            if isinstance(update, dict):
                keys = ", ".join(sorted(update.keys()))
                print(f"[{node}] {keys}", flush=True)


def _turn_summary(
    turn_index: int,
    question: str,
    state: dict[str, object],
) -> dict[str, object]:
    return {
        "turn_index": turn_index,
        "question": question,
        "route": state.get("route"),
        "verification_status": (
            state.get("truth_report", {}).get("verification_status")
            if isinstance(state.get("truth_report"), dict)
            else None
        ),
        "final_answer": state.get("final_answer", ""),
    }


def _interactive_enabled(value: bool | None) -> bool:
    if value is not None:
        return value
    return sys.stdin.isatty()


def _state_from_update(
    current: dict[str, object],
    update: dict[str, object],
) -> dict[str, object]:
    state = dict(current)
    for node_update in update.values():
        if isinstance(node_update, dict):
            state.update(node_update)
    return state


def _jsonable(value: object) -> object:
    value = getattr(value, "value", value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    content = getattr(value, "content", None)
    if content is not None:
        return {"type": value.__class__.__name__, "content": _jsonable(content)}
    return str(value)


class _TraceWriter:
    def __init__(self, path: Path | None) -> None:
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
