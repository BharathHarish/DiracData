#!/usr/bin/env python3
"""Run the primitive v2 data-agent harness."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.agent import create_primitive_data_agent  # noqa: E402
from diracdata_v2.settings import settings_from_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--model-profile", default=None)
    parser.add_argument("--question", required=True)
    parser.add_argument("--workflow", choices=["gated", "outer"], default=None)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--max-clarifications", type=int, default=3)
    parser.add_argument(
        "--clarification",
        default=None,
        help="Provide a clarification answer non-interactively and resume the same question.",
    )
    parser.add_argument(
        "--resume-from-output-file",
        default=None,
        help=(
            "Read previous clarification context from a prior JSON output file. "
            "Defaults to --output-file when --clarification is provided."
        ),
    )
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--stream-format", choices=["jsonl", "text"], default="jsonl")
    parser.add_argument("--output-file", default=None)
    parser.add_argument(
        "--metadata-descriptions-path",
        default=str(V2_ROOT / "context" / "retail_analytics_metadata_descriptions.json"),
    )
    parser.add_argument(
        "--schema-ast-path",
        default=str(V2_ROOT / "learning" / "artifacts" / "retail_analytics_v2_20260610" / "schema_ast.json"),
    )
    parser.add_argument(
        "--sql-library-path",
        default=str(V2_ROOT / "learning" / "artifacts" / "retail_analytics_patterns_v2_20260610" / "sql_library.json"),
    )
    parser.add_argument("--semantic-catalog-path", default=None)
    parser.add_argument("--retrieval-documents-path", default=None)
    parser.add_argument("--column-embeddings-path", default=None)
    args = parser.parse_args()

    if args.model_profile:
        os.environ["DIRACDATA_AGENT_MODEL_PROFILE"] = args.model_profile
    os.environ["DIRACDATA_V2_METADATA_DESCRIPTIONS_PATH"] = args.metadata_descriptions_path
    os.environ["DIRACDATA_V2_SCHEMA_AST_PATH"] = args.schema_ast_path
    os.environ["DIRACDATA_V2_SQL_LIBRARY_PATH"] = args.sql_library_path
    if args.semantic_catalog_path:
        os.environ["DIRACDATA_V2_SEMANTIC_CATALOG_PATH"] = args.semantic_catalog_path
    if args.retrieval_documents_path:
        os.environ["DIRACDATA_V2_RETRIEVAL_DOCUMENTS_PATH"] = args.retrieval_documents_path
    if args.column_embeddings_path:
        os.environ["DIRACDATA_V2_COLUMN_EMBEDDINGS_PATH"] = args.column_embeddings_path

    workflow = args.workflow or "gated"
    if args.interactive and args.clarification:
        raise SystemExit("--clarification is for non-interactive runs; use the prompt in --interactive mode instead")
    settings = settings_from_env(args.env_file)
    runtime = create_primitive_data_agent(settings=settings)
    if args.interactive:
        if workflow != "gated":
            raise SystemExit("--interactive requires --workflow gated")
        event_sink = _event_printer(args.stream_format) if args.stream else None
        turns = run_interactive_session(
            runtime=runtime,
            question=args.question,
            max_clarifications=args.max_clarifications,
            input_func=input,
            output_func=print,
            event_sink=event_sink,
        )
        if args.output_file:
            _write_json(Path(args.output_file), {"turns": turns})
        return 0

    if args.stream:
        events = []
        if workflow == "outer":
            for event in runtime.stream(args.question):
                row = event.to_dict()
                events.append(row)
                _print_stream_event(row, args.stream_format)
        else:
            previous_context = _resume_context_for_args(args)
            result = runtime.invoke(
                args.question,
                clarification=args.clarification,
                previous_context=previous_context,
                event_sink=lambda event: _collect_and_print_event(
                    event=event,
                    events=events,
                    stream_format=args.stream_format,
                ),
            )
            payload = result.to_dict()
            if args.output_file:
                _write_json(Path(args.output_file), payload)
            print(json.dumps({"final_output": result.output_text}, default=str), flush=True)
            _print_clarification_hint_if_needed(result, args)
            return 0
        if args.output_file:
            _write_json(Path(args.output_file), {"trace_events": events})
        return 0

    previous_context = _resume_context_for_args(args)
    result = (
        runtime.invoke_outer(args.question)
        if workflow == "outer"
        else runtime.invoke(
            args.question,
            clarification=args.clarification,
            previous_context=previous_context,
        )
    )
    payload = result.to_dict()
    if args.output_file:
        _write_json(Path(args.output_file), payload)
    print(json.dumps(payload, indent=2, default=str))
    _print_clarification_hint_if_needed(result, args)
    return 0


def run_interactive_session(
    *,
    runtime: Any,
    question: str,
    max_clarifications: int,
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
    event_sink: Callable[[Any], None] | None = None,
) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    clarification: str | None = None
    previous_context: str | None = None
    for turn_index in range(max(0, max_clarifications) + 1):
        result = runtime.invoke(
            question,
            clarification=clarification,
            previous_context=previous_context,
            event_sink=event_sink,
        )
        payload = result.to_dict()
        turns.append(payload)
        output_func(result.output_text)
        if result.stop_reason != "needs_clarification":
            return turns
        previous_context = _latest_clarification_context(payload)
        if turn_index >= max_clarifications:
            output_func("Maximum clarification turns reached.")
            return turns
        clarification = input_func("Clarification> ").strip()
        if clarification.lower() in {"exit", "quit"}:
            output_func("Exiting clarification session.")
            return turns
    return turns


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def _latest_clarification_context(payload: dict[str, Any]) -> str | None:
    turns = payload.get("turns")
    if isinstance(turns, list):
        for turn in reversed(turns):
            if isinstance(turn, dict):
                context = _latest_clarification_context(turn)
                if context:
                    return context
    for event in reversed(payload.get("trace_events", [])):
        if not isinstance(event, dict):
            continue
        if event.get("event_type") != "clarification_required":
            continue
        event_payload = event.get("payload")
        if isinstance(event_payload, dict):
            context = event_payload.get("previous_context")
            if isinstance(context, str):
                return context
    return None


def _resume_context_for_args(args: argparse.Namespace) -> str | None:
    if not args.clarification:
        return None
    resume_file = args.resume_from_output_file or args.output_file
    if not resume_file:
        return None
    path = Path(resume_file)
    if not path.exists():
        print(
            f"Warning: resume file not found: {path}. Continuing with clarification only.",
            file=sys.stderr,
            flush=True,
        )
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(
            f"Warning: could not parse resume file {path}: {exc}. Continuing with clarification only.",
            file=sys.stderr,
            flush=True,
        )
        return None
    if not isinstance(payload, dict):
        return None
    context = _latest_clarification_context(payload)
    if context is None:
        print(
            f"Warning: no clarification context found in {path}. Continuing with clarification only.",
            file=sys.stderr,
            flush=True,
        )
    return context


def _print_clarification_hint_if_needed(result: Any, args: argparse.Namespace) -> None:
    if getattr(result, "stop_reason", None) != "needs_clarification":
        return
    if args.interactive:
        return
    print(
        "\nClarification needed. To answer in the same terminal, rerun with --interactive. "
        "For a non-interactive resume, rerun with --clarification \"...\" "
        "and --resume-from-output-file pointing to this run's JSON output.",
        file=sys.stderr,
        flush=True,
    )


def _event_printer(stream_format: str) -> Callable[[Any], None]:
    def _sink(event: Any) -> None:
        row = event.to_dict() if hasattr(event, "to_dict") else event
        _print_stream_event(row, stream_format)

    return _sink


def _collect_and_print_event(
    *,
    event: Any,
    events: list[dict[str, Any]],
    stream_format: str,
) -> None:
    row = event.to_dict() if hasattr(event, "to_dict") else event
    events.append(row)
    _print_stream_event(row, stream_format)


def _print_stream_event(row: dict[str, Any], stream_format: str) -> None:
    if stream_format == "text":
        _print_text_stream_event(row)
    else:
        print(json.dumps(row, default=str), flush=True)


def _print_text_stream_event(row: dict[str, Any]) -> None:
    event_type = row.get("event_type")
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    if event_type == "model_delta":
        print(str(payload.get("text") or ""), end="", flush=True)
        return
    if event_type == "tool_call":
        print(f"\n[tool_call:{payload.get('name')}]", file=sys.stderr, flush=True)
        args = payload.get("args")
        if args:
            print(json.dumps(args, default=str), file=sys.stderr, flush=True)
        return
    if event_type == "tool_result":
        print(f"\n[tool_result:{payload.get('name')}]", file=sys.stderr, flush=True)
        preview = payload.get("preview")
        if preview:
            suffix = "\n...[tool result truncated]" if payload.get("truncated") else ""
            print(f"{preview}{suffix}", file=sys.stderr, flush=True)
        return
    if event_type in {"agent_start", "agent_done", "agent_stopped"}:
        agent = row.get("agent_name")
        print(f"\n[{event_type}:{agent}]", file=sys.stderr, flush=True)
        return
    if event_type in {"gated_start", "gated_done", "subagent_start", "subagent_done", "clarification_required"}:
        agent = row.get("agent_name")
        status = payload.get("status") or payload.get("stop_reason") or ""
        name = payload.get("name") or payload.get("source") or ""
        suffix = f":{name}" if name else ""
        if status:
            suffix += f":{status}"
        print(f"\n[{event_type}:{agent}{suffix}]", file=sys.stderr, flush=True)
        return
    if event_type == "context_compiled":
        print(
            "\n[context_compiled:"
            f"needs_clarification={payload.get('needs_clarification')}:"
            f"candidates={payload.get('candidate_count')}:"
            f"patterns={payload.get('pattern_count')}:"
            f"joins={payload.get('join_edge_count')}]",
            file=sys.stderr,
            flush=True,
        )
        unresolved = payload.get("unresolved_terms")
        if unresolved:
            print(json.dumps({"unresolved_terms": unresolved}, default=str), file=sys.stderr, flush=True)
        return
    if event_type == "value_grounding_blocked":
        print(f"\n[value_grounding_blocked:{payload.get('reason')}]", file=sys.stderr, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
