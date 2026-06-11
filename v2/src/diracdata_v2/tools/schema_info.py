"""Exact schema lookup tools for v2 agents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class EmptyInput(BaseModel):
    pass


class TableInput(BaseModel):
    table_name: str = Field(description="Exact table name.")


class ColumnInput(BaseModel):
    table_name: str = Field(description="Exact table name.")
    column_name: str = Field(description="Exact column name.")


@dataclass
class SchemaInfoService:
    metadata_descriptions: dict[str, Any]

    @classmethod
    def from_file(cls, path: Path) -> "SchemaInfoService":
        return cls(
            metadata_descriptions=json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )

    def get_tables(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "tables": [
                {
                    "table_name": table,
                    "short_description": desc.get("short_description", ""),
                }
                for table, desc in sorted(self.metadata_descriptions.get("tables", {}).items())
                if isinstance(desc, dict)
            ],
        }

    def get_table_columns(self, *, table_name: str) -> dict[str, Any]:
        columns = self.metadata_descriptions.get("columns", {}).get(table_name)
        if not isinstance(columns, dict):
            return {"status": "not_found", "table_name": table_name, "columns": []}
        table_desc = self.metadata_descriptions.get("tables", {}).get(table_name, {})
        return {
            "status": "ok",
            "table_name": table_name,
            "table_description": {
                "short_description": table_desc.get("short_description", "")
                if isinstance(table_desc, dict)
                else ""
            },
            "columns": [
                {
                    "column_name": column,
                    "short_description": desc.get("short_description", ""),
                }
                for column, desc in sorted(columns.items())
                if isinstance(desc, dict)
            ],
        }

    def get_column_description(self, *, table_name: str, column_name: str) -> dict[str, Any]:
        desc = (
            self.metadata_descriptions.get("columns", {})
            .get(table_name, {})
            .get(column_name)
        )
        if not isinstance(desc, dict):
            return {
                "status": "not_found",
                "table_name": table_name,
                "column_name": column_name,
            }
        return {
            "status": "ok",
            "table_name": table_name,
            "column_name": column_name,
            "short_description": desc.get("short_description", ""),
            "long_description": desc.get("long_description", ""),
        }


def build_schema_info_tools(service: SchemaInfoService) -> list[object]:
    from langchain.tools import tool

    @tool("get_tables", args_schema=EmptyInput)
    def get_tables() -> dict[str, Any]:
        """Return all tables with short descriptions."""
        return service.get_tables()

    @tool("get_table_columns", args_schema=TableInput)
    def get_table_columns(table_name: str) -> dict[str, Any]:
        """Return columns and short descriptions for one exact table."""
        return service.get_table_columns(table_name=table_name)

    @tool("get_column_description", args_schema=ColumnInput)
    def get_column_description(table_name: str, column_name: str) -> dict[str, Any]:
        """Return short and long description for one exact table column."""
        return service.get_column_description(table_name=table_name, column_name=column_name)

    return [get_tables, get_table_columns, get_column_description]
