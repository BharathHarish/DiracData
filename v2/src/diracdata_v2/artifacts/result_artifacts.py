"""Object-store artifacts for SQL result snapshots.

Large SQL results should not be copied into LLM context. The tool-facing
contract returns a preview and profile, while full-enough rows are persisted as
an object-store artifact for later inspection and follow-up workflows.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Iterable


RESULT_ARTIFACT_VERSION = 1


@dataclass(frozen=True)
class ResultArtifactWriter:
    object_store: Any
    prefix: str = "query-results"

    def write_result(
        self,
        *,
        schema: str,
        conversation_id: str | None = None,
        thread_id: str | None = None,
        sql: str,
        columns: list[str],
        rows: list[tuple[Any, ...]],
        total_row_count: int | None,
        max_rows: int,
    ) -> dict[str, Any]:
        artifact_id = "res_" + uuid.uuid4().hex
        created_at = datetime.now(UTC).isoformat()
        resolved_conversation_id = _path_token(conversation_id, default="default_conversation")
        resolved_thread_id = _path_token(thread_id, default="default_thread")
        key_prefix = (
            f"{self.prefix.strip('/')}/{schema}/"
            f"{resolved_conversation_id}/{resolved_thread_id}/"
            f"{_day_path(created_at)}/{artifact_id}"
        )
        plain_rows = rows_to_dicts(columns, rows)
        profile = profile_result_rows(
            columns=columns,
            rows=plain_rows,
            total_row_count=total_row_count,
            max_rows=max_rows,
        )
        rows_jsonl = "\n".join(json.dumps(row, sort_keys=True, default=str) for row in plain_rows)
        if rows_jsonl:
            rows_jsonl += "\n"
        sql_sha = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        rows_sha = hashlib.sha256(rows_jsonl.encode("utf-8")).hexdigest()
        manifest = {
            "artifact_type": "sql_result_snapshot",
            "artifact_version": RESULT_ARTIFACT_VERSION,
            "artifact_id": artifact_id,
            "schema": schema,
            "conversation_id": resolved_conversation_id,
            "thread_id": resolved_thread_id,
            "created_at": created_at,
            "row_count": total_row_count,
            "stored_row_count": len(plain_rows),
            "max_stored_rows": max_rows,
            "truncated": bool(total_row_count is not None and total_row_count > len(plain_rows)),
            "columns": columns,
            "profile_key": f"{key_prefix}/profile.json",
            "rows_key": f"{key_prefix}/rows.jsonl",
            "sql_key": f"{key_prefix}/sql.sql",
            "hashes": {
                "sql_sha256": sql_sha,
                "rows_jsonl_sha256": rows_sha,
            },
        }
        self.object_store.write_text(manifest["rows_key"], rows_jsonl, content_type="application/x-ndjson")
        self.object_store.write_text(manifest["sql_key"], sql, content_type="text/sql")
        self.object_store.write_json(manifest["profile_key"], profile)
        manifest_key = f"{key_prefix}/manifest.json"
        self.object_store.write_json(manifest_key, manifest)
        return {
            "artifact_id": artifact_id,
            "conversation_id": resolved_conversation_id,
            "thread_id": resolved_thread_id,
            "manifest_key": manifest_key,
            "rows_key": manifest["rows_key"],
            "profile_key": manifest["profile_key"],
            "sql_key": manifest["sql_key"],
            "stored_row_count": len(plain_rows),
            "total_row_count": total_row_count,
            "truncated": manifest["truncated"],
        }


def rows_to_dicts(columns: list[str], rows: Iterable[Iterable[Any]]) -> list[dict[str, Any]]:
    return [_row_dict(columns, row) for row in rows]


def profile_result_rows(
    *,
    columns: list[str],
    rows: list[dict[str, Any]],
    total_row_count: int | None,
    max_rows: int,
) -> dict[str, Any]:
    normalized_rows = [
        {column: _json_value(row.get(column)) for column in columns}
        for row in rows
    ]
    column_profiles = []
    for column in columns:
        values = [row.get(column) for row in normalized_rows]
        non_null = [value for value in values if value is not None]
        profile: dict[str, Any] = {
            "name": column,
            "inferred_type": _infer_type(non_null),
            "null_count_in_profile": len(values) - len(non_null),
            "non_null_count_in_profile": len(non_null),
            "distinct_count_in_profile": len({_hashable(value) for value in non_null}),
        }
        numeric_values = [float(value) for value in non_null if isinstance(value, int | float) and not isinstance(value, bool)]
        if numeric_values:
            profile["numeric"] = {
                "min": min(numeric_values),
                "max": max(numeric_values),
                "avg": sum(numeric_values) / len(numeric_values),
            }
        profile["top_values_in_profile"] = _top_values(non_null)
        column_profiles.append(profile)
    return {
        "row_count": total_row_count,
        "profiled_row_count": len(rows),
        "max_profiled_rows": max_rows,
        "truncated": bool(total_row_count is not None and total_row_count > len(rows)),
        "columns": column_profiles,
        "sample_rows": normalized_rows[: min(10, len(normalized_rows))],
    }


def _row_dict(columns: list[str], row: Iterable[Any]) -> dict[str, Any]:
    return {
        column: _json_value(value)
        for column, value in zip(columns, row, strict=False)
    }


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def _infer_type(values: list[Any]) -> str:
    if not values:
        return "null"
    if all(isinstance(value, bool) for value in values):
        return "boolean"
    if all(isinstance(value, int | float) and not isinstance(value, bool) for value in values):
        return "number"
    return "string"


def _top_values(values: list[Any], *, limit: int = 5) -> list[dict[str, Any]]:
    counts = Counter(_hashable(value) for value in values)
    output = []
    for value, count in counts.most_common(limit):
        output.append({"value": value, "count": count})
    return output


def _hashable(value: Any) -> Any:
    if isinstance(value, list | dict):
        return json.dumps(value, sort_keys=True, default=str)
    return value


def _day_path(created_at: str) -> str:
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(UTC)
    return parsed.strftime("%Y/%m/%d")


def _path_token(value: str | None, *, default: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    clean = re.sub(r"[^A-Za-z0-9._=-]+", "_", raw).strip("._-")
    return clean or default
