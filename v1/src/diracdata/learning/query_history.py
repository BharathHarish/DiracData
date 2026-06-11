"""Query history loading utilities."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


JSON_COLUMNS = {"statement_parameters", "compute", "query_source", "query_parameters", "query_tags"}


@dataclass(frozen=True)
class QueryHistoryRecord:
    """A CSV-compatible representation of a Databricks-style query history row."""

    values: dict[str, Any]

    @property
    def statement_id(self) -> str:
        return str(self.values.get("statement_id", ""))

    @property
    def statement_text(self) -> str:
        return str(self.values.get("statement_text", ""))

    @property
    def execution_status(self) -> str:
        return str(self.values.get("execution_status", ""))

    @property
    def statement_type(self) -> str:
        return str(self.values.get("statement_type", ""))


def load_query_history_csv(path: str | Path) -> list[QueryHistoryRecord]:
    """Load Databricks-style query history CSV rows.

    Struct and map columns serialized as JSON strings are decoded into Python values.
    Empty strings are normalized to None.
    """
    csv_path = Path(path)
    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        return [QueryHistoryRecord(_decode_row(row)) for row in reader]


def query_history_fieldnames(path: str | Path) -> list[str]:
    csv_path = Path(path)
    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        return list(reader.fieldnames or [])


def _decode_row(row: dict[str, str]) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key, value in row.items():
        if value == "":
            decoded[key] = None
        elif key in JSON_COLUMNS:
            decoded[key] = json.loads(value)
        else:
            decoded[key] = value
    return decoded

