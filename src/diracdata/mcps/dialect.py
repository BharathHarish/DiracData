"""Engine dialect cards for MCP hosts (tool + dialect:// resource)."""
from __future__ import annotations

from typing import Any

_CARDS: dict[str, dict[str, Any]] = {
    "duckdb": {
        "dialect": "duckdb",
        "indexing_base": 1,
        "cheat_sheet": (
            "DuckDB SQL. Lists/arrays are 1-INDEXED: list[1] is first; list[-1] is last. "
            "Dates: date_trunc('month', d), extract('year' FROM d) / date_part('year', d), "
            "date_diff('day', a, b), d + INTERVAL 1 DAY, strptime/strftime, current_date, year(d). "
            "Nested: UNNEST(list_col), struct.field, map['key'] / element_at(map, 'key'), "
            "json_extract(col, '$.key'), map_keys(col). Probe unknown functions with a tiny run_sql."
        ),
    },
    "sqlite": {
        "dialect": "sqlite",
        "indexing_base": 0,
        "cheat_sheet": (
            "SQLite (often via DuckDB ATTACH). Prefer portable SQL: date(d), strftime('%Y', d), "
            "json_extract. Avoid DuckDB-only UNNEST/LIST idioms unless the session engine is DuckDB "
            "over an attached SQLite file — then use the DuckDB cheat sheet."
        ),
    },
    "duckdb+sqlite": {
        "dialect": "duckdb",
        "engine_kind": "duckdb+sqlite",
        "indexing_base": 1,
        "cheat_sheet": (
            "DuckDB session with SQLite ATTACH. Write DuckDB SQL (1-based lists, UNNEST, date_trunc). "
            "Tables live in the attached SQLite schema; SHOW TABLES after USE."
        ),
    },
}


def dialect_card(dialect: str | None = None, *, engine_kind: str | None = None) -> dict[str, Any]:
    """Return a dialect card. Defaults to duckdb."""
    key = (engine_kind or dialect or "duckdb").lower().strip()
    if key in _CARDS:
        card = dict(_CARDS[key])
    elif "sqlite" in key and "duckdb" in key:
        card = dict(_CARDS["duckdb+sqlite"])
    elif "sqlite" in key:
        card = dict(_CARDS["sqlite"])
    else:
        card = dict(_CARDS["duckdb"])
        card["requested"] = key
    if engine_kind and "engine_kind" not in card:
        card["engine_kind"] = engine_kind
    return card


def dialect_from_engine(engine: Any) -> dict[str, Any]:
    d = getattr(engine, "dialect", None) or "duckdb"
    kind = getattr(engine, "engine_kind", None) or d
    return dialect_card(d, engine_kind=str(kind))
