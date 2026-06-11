"""Small SQL helpers used by local runtime utilities."""

from __future__ import annotations

from pathlib import Path


def quote_identifier(identifier: str) -> str:
    """Quote a SQL identifier for DuckDB-compatible SQL."""
    return '"' + identifier.replace('"', '""') + '"'


def sql_string(value: str | Path) -> str:
    """Quote a SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"

