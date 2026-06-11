"""Generate semantic metadata descriptions from learning profiles."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from diracdata.config.settings import DiracDataSettings
from diracdata.llms import ChatModelClient, ChatModelMessage
from diracdata.learning.models import LearningCollection
from diracdata.learning.paths import learning_artifact_key
from diracdata.storage.object_store import ObjectStore


PROMPT_PATH = Path(__file__).parent / "prompts" / "schema_descriptions.md"


class MetadataDescriptionGenerator:
    """Prompt-driven metadata description generator."""

    def __init__(
        self,
        *,
        settings: DiracDataSettings,
        object_store: ObjectStore,
        llm_client: ChatModelClient,
        prompt_path: Path = PROMPT_PATH,
        column_batch_size: int | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.object_store = object_store
        self.llm_client = llm_client
        self.prompt_template = prompt_path.read_text(encoding="utf-8")
        self.progress_callback = progress_callback
        self.column_batch_size = (
            column_batch_size
            if column_batch_size is not None
            else settings.learning_description_column_batch_size
        )

    def generate(
        self,
        collection: LearningCollection,
        *,
        business_grounding: dict[str, object] | None = None,
    ) -> str:
        context = self.object_store.read_json(collection.llm_context_artifact_key)
        if business_grounding is not None:
            if not isinstance(context, dict):
                raise ValueError("LLM context artifact must be a JSON object")
            context = {
                **context,
                "business_grounding": business_grounding,
            }
        expected_tables = _expected_columns_by_table(context)
        descriptions: dict[str, dict[str, Any]] = {"tables": {}, "columns": {}}

        for table_context in _iter_description_contexts(
            context,
            column_batch_size=self.column_batch_size,
        ):
            batch_index = table_context["description_batch"]["index"]
            expected_batch_tables = {
                table["table_name"]: table["column_names"]
                for table in table_context["description_batch"]["tables"]
            }
            self._emit(
                "describe batch "
                f"{batch_index}/{table_context['description_batch']['total_batches']}: "
                f"{table_context['description_batch']['total_active_columns']} columns, "
                f"{len(expected_batch_tables)} tables"
            )
            context_json = json.dumps(table_context, indent=2, sort_keys=True)
            prompt = self.prompt_template.replace("{{learning_context_json}}", context_json)
            response = self.llm_client.complete([ChatModelMessage(role="user", content=prompt)])
            table_descriptions = _validate_descriptions(
                _parse_json_object(response),
                expected_tables=expected_batch_tables,
            )
            for table_name, table_description in table_descriptions["tables"].items():
                descriptions["tables"].setdefault(table_name, table_description)
            for table_name, table_columns in table_descriptions["columns"].items():
                descriptions["columns"].setdefault(table_name, {})
                descriptions["columns"][table_name].update(table_columns)
            self.object_store.write_json(
                learning_artifact_key(
                    self.settings,
                    run_id=collection.run_id,
                    relative_path=f"descriptions/batches/batch_{batch_index:03d}.json",
                ),
                table_descriptions,
            )

        descriptions = _validate_descriptions(descriptions, expected_tables=expected_tables)

        output_key = learning_artifact_key(
            self.settings,
            run_id=collection.run_id,
            relative_path="descriptions/metadata_descriptions.json",
        )
        self.object_store.write_json(output_key, descriptions)
        return output_key

    def _emit(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)


def _validate_descriptions(
    payload: object,
    *,
    expected_tables: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("metadata description response must be a JSON object")
    tables = payload.get("tables")
    columns = payload.get("columns")
    if not isinstance(tables, dict) or not isinstance(columns, dict):
        raise ValueError("metadata description response must contain tables and columns objects")

    for description in _iter_description_objects(payload):
        short = description.get("short_description")
        long = description.get("long_description")
        if not isinstance(short, str) or not short.strip():
            raise ValueError("each description must contain non-empty short_description")
        if not isinstance(long, str) or not long.strip():
            raise ValueError("each description must contain short_description and long_description")
        if len(short.split()) > 50:
            raise ValueError("short_description exceeds 50 words")
        if len(long.split()) > 300:
            raise ValueError("long_description exceeds 300 words")

    if expected_tables is not None:
        _validate_description_coverage(
            tables=tables,
            columns=columns,
            expected_tables=expected_tables,
        )
    return payload


def _iter_description_objects(payload: dict[str, object]) -> list[dict[str, object]]:
    descriptions: list[dict[str, object]] = []
    for description in payload["tables"].values():
        if not isinstance(description, dict):
            raise ValueError("table description must be an object")
        descriptions.append(description)
    for table_columns in payload["columns"].values():
        if not isinstance(table_columns, dict):
            raise ValueError("columns entry must be an object")
        for description in table_columns.values():
            if not isinstance(description, dict):
                raise ValueError("column description must be an object")
            descriptions.append(description)
    return descriptions


def _parse_json_object(text: str) -> object:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}") + 1
        if start < 0 or end <= start:
            raise
        return json.loads(stripped[start:end])


def _iter_description_contexts(
    context: object,
    *,
    column_batch_size: int,
) -> list[dict[str, Any]]:
    if not isinstance(context, dict):
        raise ValueError("LLM context artifact must be a JSON object")
    if column_batch_size <= 0:
        raise ValueError("column_batch_size must be greater than zero")

    tables = _context_tables(context)
    if not tables:
        raise ValueError("LLM context artifact must contain at least one table")

    available_tables = [
        {
            "table_name": table["table_name"],
            "row_count": table.get("row_count"),
            "column_names": [
                column["column_name"]
                for column in _context_columns(table)
            ],
        }
        for table in tables
    ]

    table_chunks: list[dict[str, Any]] = []
    for table in tables:
        columns = _context_columns(table)
        for batch_columns in _column_batches(columns, batch_size=column_batch_size):
            table_chunks.append(
                {
                    "table": {
                        **table,
                        "columns": batch_columns,
                    },
                    "column_count": len(batch_columns),
                }
            )

    prompt_batches = _prompt_batches(table_chunks, batch_size=column_batch_size)
    table_contexts: list[dict[str, Any]] = []
    for batch_index, batch in enumerate(prompt_batches, start=1):
        active_tables = [item["table"] for item in batch]
        table_contexts.append(
            {
                "run_id": context.get("run_id"),
                "scope": context.get("scope"),
                "business_context": context.get("business_context", {}),
                "business_grounding": context.get("business_grounding", {}),
                "available_tables": available_tables,
                "description_batch": {
                    "index": batch_index,
                    "total_batches": len(prompt_batches),
                    "total_active_columns": sum(item["column_count"] for item in batch),
                    "tables": [
                        {
                            "table_name": table["table_name"],
                            "column_names": [
                                column["column_name"]
                                for column in _context_columns(table)
                            ],
                        }
                        for table in active_tables
                    ],
                },
                "tables": active_tables,
            }
        )
    return table_contexts


def _expected_columns_by_table(context: object) -> dict[str, list[str]]:
    return {
        table["table_name"]: [
            column["column_name"]
            for column in _context_columns(table)
        ]
        for table in _context_tables(context)
    }


def _context_tables(context: object) -> list[dict[str, Any]]:
    if not isinstance(context, dict):
        raise ValueError("LLM context artifact must be a JSON object")
    tables = context.get("tables")
    if not isinstance(tables, list):
        raise ValueError("LLM context artifact must contain a tables list")

    table_contexts: list[dict[str, Any]] = []
    for table in tables:
        if not isinstance(table, dict) or not isinstance(table.get("table_name"), str):
            raise ValueError("each LLM context table must contain table_name")
        table_contexts.append(table)
    return table_contexts


def _context_columns(table: dict[str, Any]) -> list[dict[str, Any]]:
    columns = table.get("columns")
    if not isinstance(columns, list):
        raise ValueError(f"LLM context table must contain columns list: {table['table_name']}")
    column_contexts: list[dict[str, Any]] = []
    for column in columns:
        if not isinstance(column, dict) or not isinstance(column.get("column_name"), str):
            raise ValueError(f"each column must contain column_name: {table['table_name']}")
        column_contexts.append(column)
    return column_contexts


def _column_batches(
    columns: list[dict[str, Any]],
    *,
    batch_size: int,
) -> list[list[dict[str, Any]]]:
    return [
        columns[index : index + batch_size]
        for index in range(0, len(columns), batch_size)
    ]


def _prompt_batches(
    table_chunks: list[dict[str, Any]],
    *,
    batch_size: int,
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current_batch: list[dict[str, Any]] = []
    current_columns = 0

    for chunk in table_chunks:
        column_count = chunk["column_count"]
        if current_batch and current_columns + column_count > batch_size:
            batches.append(current_batch)
            current_batch = []
            current_columns = 0

        current_batch.append(chunk)
        current_columns += column_count

    if current_batch:
        batches.append(current_batch)
    return batches


def _validate_description_coverage(
    *,
    tables: dict[str, object],
    columns: dict[str, object],
    expected_tables: dict[str, list[str]],
) -> None:
    expected_table_names = set(expected_tables)
    table_names = set(tables)
    column_table_names = set(columns)
    if table_names != expected_table_names:
        raise ValueError(
            "metadata descriptions must exactly cover expected tables; "
            f"missing={sorted(expected_table_names - table_names)}, "
            f"unexpected={sorted(table_names - expected_table_names)}"
        )
    if column_table_names != expected_table_names:
        raise ValueError(
            "metadata column descriptions must exactly cover expected tables; "
            f"missing={sorted(expected_table_names - column_table_names)}, "
            f"unexpected={sorted(column_table_names - expected_table_names)}"
        )

    for table_name, expected_columns in expected_tables.items():
        table_columns = columns[table_name]
        if not isinstance(table_columns, dict):
            raise ValueError(f"metadata columns entry must be an object: {table_name}")
        expected_column_names = set(expected_columns)
        column_names = set(table_columns)
        if column_names != expected_column_names:
            raise ValueError(
                f"metadata descriptions must exactly cover expected columns for {table_name}; "
                f"missing={sorted(expected_column_names - column_names)}, "
                f"unexpected={sorted(column_names - expected_column_names)}"
            )
