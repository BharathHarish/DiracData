#!/usr/bin/env python3
"""Run the NL AST agent across model profiles and save traces."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.agent import create_nl_ast_agent  # noqa: E402
from diracdata_v2.settings import settings_from_env  # noqa: E402


DEFAULT_PROFILES = [
    "anthropic_haiku_45",
    "openai_gpt_5_4_mini",
    "bedrock_qwen3_next_80b_a3b_ap_south_1",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--question", required=True)
    parser.add_argument("--model-profile", action="append", dest="profiles")
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
        default=str(V2_ROOT / "learning" / "artifacts" / "retail_analytics_v2_20260610" / "sql_library.json"),
    )
    parser.add_argument("--retrieval-documents-path", default=None)
    parser.add_argument("--column-embeddings-path", default=None)
    parser.add_argument(
        "--table-descriptions-path",
        default=str(V2_ROOT / "context" / "retail_analytics" / "table_descriptions.md"),
    )
    parser.add_argument(
        "--table-column-descriptions-path",
        default=str(V2_ROOT / "context" / "retail_analytics" / "table_column_descriptions.md"),
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--stream-mode", default="updates")
    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else (
        V2_ROOT
        / "data"
        / "uat_runs"
        / f"nl_ast_agent_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    profiles = args.profiles or DEFAULT_PROFILES
    summary: list[dict[str, Any]] = []

    for profile in profiles:
        os.environ["DIRACDATA_AGENT_MODEL_PROFILE"] = profile
        os.environ["DIRACDATA_V2_METADATA_DESCRIPTIONS_PATH"] = args.metadata_descriptions_path
        os.environ["DIRACDATA_V2_SCHEMA_AST_PATH"] = args.schema_ast_path
        os.environ["DIRACDATA_V2_SQL_LIBRARY_PATH"] = args.sql_library_path
        if args.retrieval_documents_path:
            os.environ["DIRACDATA_V2_RETRIEVAL_DOCUMENTS_PATH"] = args.retrieval_documents_path
        if args.column_embeddings_path:
            os.environ["DIRACDATA_V2_COLUMN_EMBEDDINGS_PATH"] = args.column_embeddings_path
        settings = settings_from_env(args.env_file)
        trace_path = output_dir / f"{profile}.jsonl"
        result_path = output_dir / f"{profile}_result.json"
        row: dict[str, Any] = {"profile": profile, "trace": str(trace_path), "result": str(result_path)}
        try:
            runtime = create_nl_ast_agent(
                settings=settings,
                table_descriptions_path=Path(args.table_descriptions_path),
                table_column_descriptions_path=Path(args.table_column_descriptions_path),
            )
            last_event: Any = None
            with trace_path.open("w", encoding="utf-8") as trace:
                for event in runtime.stream(args.question, stream_mode=_stream_mode(args.stream_mode)):
                    last_event = event
                    trace.write(json.dumps(_plain(event), default=str) + "\n")
            result_path.write_text(json.dumps(_plain(last_event), indent=2, default=str), encoding="utf-8")
            row["ok"] = True
            row["final_preview"] = _final_preview(last_event)
        except Exception as exc:
            row["ok"] = False
            row["error"] = f"{type(exc).__name__}: {exc}"
            result_path.write_text(json.dumps(row, indent=2), encoding="utf-8")
        summary.append(row)

    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "summary": summary}, indent=2, default=str))
    return 0


def _stream_mode(value: str) -> str | list[str]:
    parts = [item.strip() for item in value.split(",") if item.strip()]
    return parts if len(parts) > 1 else parts[0] if parts else "updates"


def _final_preview(value: Any) -> str:
    text = json.dumps(_plain(value), default=str)
    return text[-3000:]


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    return value


if __name__ == "__main__":
    raise SystemExit(main())
