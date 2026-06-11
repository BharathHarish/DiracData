"""Build long-context markdown documents from learned schema descriptions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DescriptionDocsResult:
    table_descriptions_path: Path
    table_column_descriptions_path: Path
    table_count: int
    column_count: int


def build_description_docs(
    *,
    descriptions_path: Path,
    data_root: Path,
    schema_name: str,
    output_dir: Path,
    sample_values_limit: int = 8,
) -> DescriptionDocsResult:
    """Create markdown context documents for a schema.

    The documents are intentionally dumb and inspectable:
    - table_descriptions.md is the compact semantic map.
    - table_column_descriptions.md is the larger column/value evidence document.
    """
    descriptions = _read_descriptions(descriptions_path)
    tables = _tables(descriptions)
    columns = _columns(descriptions)
    samples = _sample_values_by_column(
        data_root=data_root,
        schema_name=schema_name,
        tables=tables,
        sample_values_limit=sample_values_limit,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    table_doc_path = output_dir / "table_descriptions.md"
    column_doc_path = output_dir / "table_column_descriptions.md"
    table_doc_path.write_text(
        _table_descriptions_markdown(schema_name=schema_name, tables=tables, columns=columns),
        encoding="utf-8",
    )
    column_doc_path.write_text(
        _table_column_descriptions_markdown(
            schema_name=schema_name,
            tables=tables,
            columns=columns,
            samples=samples,
        ),
        encoding="utf-8",
    )
    return DescriptionDocsResult(
        table_descriptions_path=table_doc_path,
        table_column_descriptions_path=column_doc_path,
        table_count=len(tables),
        column_count=sum(len(table_columns) for table_columns in columns.values()),
    )


def _read_descriptions(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("metadata descriptions must be a JSON object")
    if not isinstance(payload.get("tables"), dict) or not isinstance(payload.get("columns"), dict):
        raise ValueError("metadata descriptions must contain tables and columns objects")
    return payload


def _tables(descriptions: dict[str, Any]) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for table_name, description in sorted(descriptions["tables"].items()):
        if not isinstance(description, dict):
            continue
        rows[str(table_name)] = {
            "short_description": str(description.get("short_description") or "").strip(),
            "long_description": str(description.get("long_description") or "").strip(),
        }
    return rows


def _columns(descriptions: dict[str, Any]) -> dict[str, dict[str, dict[str, str]]]:
    rows: dict[str, dict[str, dict[str, str]]] = {}
    for table_name, table_columns in sorted(descriptions["columns"].items()):
        if not isinstance(table_columns, dict):
            continue
        rows[str(table_name)] = {}
        for column_name, description in sorted(table_columns.items()):
            if not isinstance(description, dict):
                continue
            rows[str(table_name)][str(column_name)] = {
                "short_description": str(description.get("short_description") or "").strip(),
                "long_description": str(description.get("long_description") or "").strip(),
            }
    return rows


def _sample_values_by_column(
    *,
    data_root: Path,
    schema_name: str,
    tables: dict[str, dict[str, str]],
    sample_values_limit: int,
) -> dict[str, dict[str, list[Any]]]:
    parquet_root = data_root / schema_name / "parquet"
    if not parquet_root.exists():
        return {}
    try:
        import duckdb
    except ImportError:
        return {}

    con = duckdb.connect(":memory:")
    samples: dict[str, dict[str, list[Any]]] = {}
    for table_name in sorted(tables):
        path = parquet_root / f"{table_name}.parquet"
        if not path.exists():
            continue
        con.execute(
            f"CREATE OR REPLACE VIEW {_ident(table_name)} AS "
            f"SELECT * FROM read_parquet('{_sql_string(path.as_posix())}')"
        )
        samples[table_name] = {}
        column_rows = con.execute(f"DESCRIBE {_ident(table_name)}").fetchall()
        for column_name, *_ in column_rows:
            samples[table_name][str(column_name)] = _sample_column_values(
                con=con,
                table_name=table_name,
                column_name=str(column_name),
                limit=sample_values_limit,
            )
    con.close()
    return samples


def _sample_column_values(*, con: Any, table_name: str, column_name: str, limit: int) -> list[Any]:
    if limit <= 0:
        return []
    query = (
        f"SELECT {_ident(column_name)} AS value, COUNT(*) AS row_count "
        f"FROM {_ident(table_name)} "
        f"WHERE {_ident(column_name)} IS NOT NULL "
        f"GROUP BY 1 ORDER BY row_count DESC, value LIMIT {int(limit)}"
    )
    try:
        rows = con.execute(query).fetchall()
    except Exception:
        return []
    return [
        {"value": _plain_value(value), "row_count": int(row_count)}
        for value, row_count in rows
    ]


def _table_descriptions_markdown(
    *,
    schema_name: str,
    tables: dict[str, dict[str, str]],
    columns: dict[str, dict[str, dict[str, str]]],
) -> str:
    lines = [
        f"# Schema Table Descriptions: {schema_name}",
        "",
        "This document contains table-level long descriptions and compact column meanings.",
        "",
    ]
    for table_name, table_desc in tables.items():
        lines.extend(
            [
                f"## {table_name}",
                "",
                table_desc.get("long_description") or table_desc.get("short_description") or "No description available.",
                "",
                "| Column | Short description |",
                "|---|---|",
            ]
        )
        for column_name, column_desc in columns.get(table_name, {}).items():
            lines.append(
                f"| `{column_name}` | {_escape_table_cell(column_desc.get('short_description') or '')} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _table_column_descriptions_markdown(
    *,
    schema_name: str,
    tables: dict[str, dict[str, str]],
    columns: dict[str, dict[str, dict[str, str]]],
    samples: dict[str, dict[str, list[Any]]],
) -> str:
    lines = [
        f"# Schema Column Descriptions And Sample Values: {schema_name}",
        "",
        "This document contains per-table column semantics and sample value evidence.",
        "",
    ]
    for table_name, table_columns in columns.items():
        lines.extend(
            [
                f"## {table_name}",
                "",
                tables.get(table_name, {}).get("long_description")
                or tables.get(table_name, {}).get("short_description")
                or "No table description available.",
                "",
            ]
        )
        for column_name, column_desc in table_columns.items():
            lines.extend(
                [
                    f"### {table_name}.{column_name}",
                    "",
                    f"Short: {column_desc.get('short_description') or 'No short description available.'}",
                    "",
                    f"Long: {column_desc.get('long_description') or 'No long description available.'}",
                    "",
                    f"Sample values: {_format_sample_values(samples.get(table_name, {}).get(column_name, []))}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def _format_sample_values(values: list[Any]) -> str:
    if not values:
        return "No non-null sample values available."
    formatted = []
    for item in values:
        if isinstance(item, dict):
            formatted.append(f"{item.get('value')!r} ({item.get('row_count')})")
        else:
            formatted.append(repr(item))
    return ", ".join(formatted)


def _plain_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def _escape_table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_string(value: str) -> str:
    return value.replace("'", "''")
