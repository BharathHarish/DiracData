"""Validated SQL reference extraction for learned context artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JoinPair:
    left_column: str
    right_column: str

    @property
    def tables(self) -> tuple[str, str]:
        return (
            self.left_column.split(".", 1)[0],
            self.right_column.split(".", 1)[0],
        )

    @property
    def sql_condition(self) -> str:
        return f"{self.left_column} = {self.right_column}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_column": self.left_column,
            "right_column": self.right_column,
            "tables": list(self.tables),
            "sql_condition": self.sql_condition,
        }


@dataclass(frozen=True)
class SQLReferenceAnalysis:
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    join_pairs: tuple[JoinPair, ...]
    parser: str


def analyze_sql_references(sql: str, table_columns: dict[str, list[str]]) -> SQLReferenceAnalysis:
    """Extract schema-scoped tables, columns, and equality join pairs.

    The function is intentionally conservative: only references that validate
    against the supplied table/column map are returned.
    """

    try:
        return _analyze_with_sqlglot(sql=sql, table_columns=table_columns)
    except Exception:  # noqa: BLE001
        return _analyze_with_regex(sql=sql, table_columns=table_columns)


def _analyze_with_sqlglot(sql: str, table_columns: dict[str, list[str]]) -> SQLReferenceAnalysis:
    import sqlglot
    from sqlglot import expressions as exp

    expression = sqlglot.parse_one(sql, read="duckdb")
    aliases = _sqlglot_aliases(expression=expression, table_columns=table_columns)
    tables = set(aliases.values())
    columns: set[str] = set()
    join_pairs: set[tuple[str, str]] = set()

    for column in expression.find_all(exp.Column):
        ref = _column_ref(column=column, aliases=aliases, table_columns=table_columns)
        if ref:
            columns.add(ref)

    for equality in expression.find_all(exp.EQ):
        left = equality.left
        right = equality.right
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue
        left_ref = _column_ref(column=left, aliases=aliases, table_columns=table_columns)
        right_ref = _column_ref(column=right, aliases=aliases, table_columns=table_columns)
        if not left_ref or not right_ref:
            continue
        if left_ref.split(".", 1)[0] == right_ref.split(".", 1)[0]:
            continue
        join_pairs.add(tuple(sorted((left_ref, right_ref))))

    return SQLReferenceAnalysis(
        tables=tuple(sorted(tables)),
        columns=tuple(sorted(columns)),
        join_pairs=tuple(JoinPair(left, right) for left, right in sorted(join_pairs)),
        parser="sqlglot",
    )


def _sqlglot_aliases(*, expression: Any, table_columns: dict[str, list[str]]) -> dict[str, str]:
    from sqlglot import expressions as exp

    table_lookup = {table.lower(): table for table in table_columns}
    aliases: dict[str, str] = {}
    for table in expression.find_all(exp.Table):
        actual = table_lookup.get(table.name.lower())
        if actual is None:
            continue
        aliases[actual.lower()] = actual
        alias = table.alias
        if alias:
            aliases[alias.lower()] = actual
    return aliases


def _column_ref(*, column: Any, aliases: dict[str, str], table_columns: dict[str, list[str]]) -> str | None:
    qualifier = str(column.table or "").lower()
    column_name = str(column.name or "")
    if not qualifier:
        return _unqualified_column_ref(column_name=column_name, table_columns=table_columns)
    table = aliases.get(qualifier)
    if table is None:
        return None
    column_lookup = {item.lower(): item for item in table_columns.get(table, [])}
    actual_column = column_lookup.get(column_name.lower())
    if actual_column is None:
        return None
    return f"{table}.{actual_column}"


def _unqualified_column_ref(*, column_name: str, table_columns: dict[str, list[str]]) -> str | None:
    matches = []
    for table, columns in table_columns.items():
        column_lookup = {item.lower(): item for item in columns}
        actual_column = column_lookup.get(column_name.lower())
        if actual_column:
            matches.append(f"{table}.{actual_column}")
    return matches[0] if len(matches) == 1 else None


def _analyze_with_regex(sql: str, table_columns: dict[str, list[str]]) -> SQLReferenceAnalysis:
    aliases = _regex_aliases(sql=sql, table_columns=table_columns)
    tables = set(aliases.values())
    columns = _regex_qualified_columns(sql=sql, aliases=aliases, table_columns=table_columns)
    join_pairs = _regex_join_pairs(sql=sql, aliases=aliases, table_columns=table_columns)
    return SQLReferenceAnalysis(
        tables=tuple(sorted(tables)),
        columns=tuple(sorted(columns)),
        join_pairs=tuple(JoinPair(left, right) for left, right in sorted(join_pairs)),
        parser="regex",
    )


def _regex_aliases(*, sql: str, table_columns: dict[str, list[str]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    table_lookup = {table.lower(): table for table in table_columns}
    reserved = {"on", "where", "group", "order", "join", "left", "right", "inner", "full", "cross"}
    for table, alias in re.findall(
        r"\b(?:from|join)\s+([a-zA-Z_][\w]*)(?:\s+(?:as\s+)?([a-zA-Z_][\w]*))?",
        sql,
        flags=re.IGNORECASE,
    ):
        actual = table_lookup.get(table.lower())
        if actual is None:
            continue
        aliases[actual.lower()] = actual
        if alias and alias.lower() not in reserved:
            aliases[alias.lower()] = actual
    return aliases


def _regex_qualified_columns(
    *,
    sql: str,
    aliases: dict[str, str],
    table_columns: dict[str, list[str]],
) -> set[str]:
    output: set[str] = set()
    for qualifier, column in re.findall(r"\b([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)\b", sql):
        table = aliases.get(qualifier.lower())
        if table is None:
            continue
        column_lookup = {item.lower(): item for item in table_columns.get(table, [])}
        actual_column = column_lookup.get(column.lower())
        if actual_column:
            output.add(f"{table}.{actual_column}")
    return output


def _regex_join_pairs(
    *,
    sql: str,
    aliases: dict[str, str],
    table_columns: dict[str, list[str]],
) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for left_alias, left_col, right_alias, right_col in re.findall(
        r"\b([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)\s*=\s*([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)\b",
        sql,
        flags=re.IGNORECASE,
    ):
        left_table = aliases.get(left_alias.lower())
        right_table = aliases.get(right_alias.lower())
        if not left_table or not right_table or left_table == right_table:
            continue
        left_lookup = {item.lower(): item for item in table_columns.get(left_table, [])}
        right_lookup = {item.lower(): item for item in table_columns.get(right_table, [])}
        left_column = left_lookup.get(left_col.lower())
        right_column = right_lookup.get(right_col.lower())
        if not left_column or not right_column:
            continue
        pairs.add(tuple(sorted((f"{left_table}.{left_column}", f"{right_table}.{right_column}"))))
    return pairs
