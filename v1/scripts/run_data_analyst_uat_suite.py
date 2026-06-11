"""Run CSV-defined data analyst UAT cases across one or more model profiles."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.evals.uat_suite import (
    UatConversation,
    evaluate_trace,
    extract_trace_turns,
    load_uat_conversations,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UAT_CSV = ROOT / "tests" / "harness" / "data_analyst_uat.csv"
DEFAULT_OUTPUT_ROOT = Path("/private/tmp/diracdata_uat_reports")
DEFAULT_MODEL_PROFILES = ("anthropic_sonnet_46", "anthropic_haiku_45")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uat-csv", type=Path, default=DEFAULT_UAT_CSV)
    parser.add_argument(
        "--model-profile",
        action="append",
        default=[],
        help="Model profile to test. May be repeated. Defaults to Sonnet and Haiku.",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="UAT case id to run. May be repeated. Defaults to every case in the CSV.",
    )
    parser.add_argument("--catalog", default="retail_pod")
    parser.add_argument("--database", default="analytics")
    parser.add_argument("--schema", default="retail_analytics")
    parser.add_argument(
        "--catalog-config",
        type=Path,
        default=ROOT / "conf" / "catalogs" / "retail_analytics.minio.json",
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--stream-modes", default="updates,messages")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--fail-fast", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conversations = _selected_conversations(
        load_uat_conversations(args.uat_csv),
        case_ids=set(args.case_id),
    )
    profiles = tuple(args.model_profile) or DEFAULT_MODEL_PROFILES
    output_dir = args.output_dir or _default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_csv_path = output_dir / f"UAT_run_{output_dir.name}.csv"

    rows = []
    turn_rows = []
    for profile in profiles:
        for conversation in conversations:
            result = _run_one(
                args=args,
                profile=profile,
                conversation=conversation,
                output_dir=output_dir,
            )
            rows.append(result)
            turn_rows.extend(result.pop("turn_rows"))
            _print_case_result(result)
            if args.fail_fast and result["status"] != "passed":
                _write_summary(output_dir / "summary.csv", rows)
                _write_summary(run_csv_path, turn_rows)
                _print_summary(output_dir, rows, run_csv_path=run_csv_path)
                raise SystemExit(1)

    _write_summary(output_dir / "summary.csv", rows)
    _write_summary(run_csv_path, turn_rows)
    _print_summary(output_dir, rows, run_csv_path=run_csv_path)
    if any(row["status"] != "passed" for row in rows):
        raise SystemExit(1)


def _selected_conversations(
    conversations: list[UatConversation],
    *,
    case_ids: set[str],
) -> list[UatConversation]:
    if not case_ids:
        return conversations
    selected = [conversation for conversation in conversations if conversation.case_id in case_ids]
    missing = sorted(case_ids - {conversation.case_id for conversation in selected})
    if missing:
        raise ValueError(f"Unknown UAT case ids: {', '.join(missing)}")
    return selected


def _run_one(
    *,
    args: argparse.Namespace,
    profile: str,
    conversation: UatConversation,
    output_dir: Path,
) -> dict[str, object]:
    case_prefix = f"{profile}__{conversation.case_id}"
    trace_path = output_dir / f"{case_prefix}.trace.jsonl"
    stdout_path = output_dir / f"{case_prefix}.stdout.txt"
    stderr_path = output_dir / f"{case_prefix}.stderr.txt"
    command = [
        sys.executable,
        "scripts/run_data_analyst_agent_uat.py",
        "--env-file",
        args.env_file,
        "--catalog",
        args.catalog,
        "--database",
        args.database,
        "--schema",
        args.schema,
        "--catalog-config",
        str(args.catalog_config),
        "--agent-model-profile",
        profile,
        "--question",
        conversation.question,
        "--stream",
        "--stream-modes",
        args.stream_modes,
        "--no-interactive",
        "--trace-jsonl",
        str(trace_path),
    ]
    for follow_up in conversation.follow_ups:
        command.extend(["--follow-up", follow_up])

    process = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=args.timeout_seconds,
        check=False,
    )
    stdout_path.write_text(process.stdout, encoding="utf-8")
    stderr_path.write_text(process.stderr, encoding="utf-8")

    failures = []
    if process.returncode != 0:
        failures.append(f"CLI exited with status {process.returncode}")
    if not trace_path.exists():
        failures.append("trace file was not written")
        evaluation_passed = False
        turn_rows = _missing_trace_turn_rows(
            profile=profile,
            conversation=conversation,
            trace_path=trace_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            failure="trace file was not written",
        )
    else:
        evaluation = evaluate_trace(trace_path=trace_path, conversation=conversation)
        evaluation_passed = evaluation.passed
        failures.extend(evaluation.failures)
        turn_rows = _turn_report_rows(
            profile=profile,
            conversation=conversation,
            trace_path=trace_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            evaluation=evaluation,
        )

    status = "passed" if process.returncode == 0 and evaluation_passed and not failures else "failed"
    return {
        "model_profile": profile,
        "case_id": conversation.case_id,
        "status": status,
        "failures": " | ".join(failures),
        "turn_count": str(len(conversation.turns)),
        "trace_jsonl": str(trace_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "turn_rows": turn_rows,
    }


def _print_case_result(row: dict[str, object]) -> None:
    payload = {
        "model_profile": row["model_profile"],
        "case_id": row["case_id"],
        "status": row["status"],
        "failures": row["failures"],
    }
    print(json.dumps(payload, indent=2), flush=True)


def _turn_report_rows(
    *,
    profile: str,
    conversation: UatConversation,
    trace_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    evaluation: object,
) -> list[dict[str, str]]:
    trace_turns = extract_trace_turns(trace_path)
    turn_results = {
        result.turn_index: result
        for result in getattr(evaluation, "turn_results", ())
    }
    rows = []
    for expected in conversation.turns:
        actual = trace_turns.get(expected.turn_index)
        result = turn_results.get(expected.turn_index)
        failures = tuple(getattr(result, "failures", ())) if result is not None else ()
        status = "passed" if result is not None and result.passed else "failed"
        rows.append(
            {
                "model_profile": profile,
                "case_id": conversation.case_id,
                "turn_index": str(expected.turn_index),
                "turn_status": status,
                "category": expected.category,
                "question": expected.question,
                "expected_behavior": expected.expected_behavior.value,
                "expected_result": expected.expected_result or "",
                "failures": " | ".join(failures),
                "required_tools": ";".join(expected.required_tools),
                "required_grounding_ids": ";".join(expected.required_grounding_ids),
                "required_tables": ";".join(expected.required_tables),
                "required_columns": ";".join(expected.required_columns),
                "required_sql_contains": ";".join(expected.required_sql_contains),
                "forbidden_sql_contains": ";".join(expected.forbidden_sql_contains),
                "tool_calls": ";".join(_unique_preserve(actual.tool_calls if actual else [])),
                "executed_sql": "\n\n".join(actual.executed_sql if actual else []),
                "run_sql_rows_json": _run_sql_rows_json(actual.run_sql_outputs if actual else []),
                "final_answer": actual.final_answer if actual else "",
                "trace_jsonl": str(trace_path),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            }
        )
    return rows


def _missing_trace_turn_rows(
    *,
    profile: str,
    conversation: UatConversation,
    trace_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    failure: str,
) -> list[dict[str, str]]:
    rows = []
    for expected in conversation.turns:
        rows.append(
            {
                "model_profile": profile,
                "case_id": conversation.case_id,
                "turn_index": str(expected.turn_index),
                "turn_status": "failed",
                "category": expected.category,
                "question": expected.question,
                "expected_behavior": expected.expected_behavior.value,
                "expected_result": expected.expected_result or "",
                "failures": failure,
                "required_tools": ";".join(expected.required_tools),
                "required_grounding_ids": ";".join(expected.required_grounding_ids),
                "required_tables": ";".join(expected.required_tables),
                "required_columns": ";".join(expected.required_columns),
                "required_sql_contains": ";".join(expected.required_sql_contains),
                "forbidden_sql_contains": ";".join(expected.forbidden_sql_contains),
                "tool_calls": "",
                "executed_sql": "",
                "run_sql_rows_json": "",
                "final_answer": "",
                "trace_jsonl": str(trace_path),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            }
        )
    return rows


def _run_sql_rows_json(outputs: list[dict[str, object]]) -> str:
    rows = [output.get("rows", []) for output in outputs]
    return json.dumps(rows, sort_keys=True)


def _unique_preserve(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _write_summary(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _print_summary(
    output_dir: Path,
    rows: list[dict[str, object]],
    *,
    run_csv_path: Path,
) -> None:
    passed = sum(1 for row in rows if row["status"] == "passed")
    failed = len(rows) - passed
    print(
        json.dumps(
            {
                "status": "passed" if failed == 0 else "failed",
                "passed": passed,
                "failed": failed,
                "output_dir": str(output_dir),
                "summary_csv": str(output_dir / "summary.csv"),
                "uat_run_csv": str(run_csv_path),
            },
            indent=2,
        ),
        flush=True,
    )


def _default_output_dir() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return DEFAULT_OUTPUT_ROOT / timestamp


if __name__ == "__main__":
    main()
