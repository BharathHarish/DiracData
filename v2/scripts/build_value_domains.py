#!/usr/bin/env python3
"""Profile each column's value domain -- a deterministic learning/context step.

The column descriptions mention values only in partial prose ("such as Books, Shoes,
..."), which forces the agent to run SELECT DISTINCT at query time to learn the real,
complete domain. This profiles it ONCE, offline: the COMPLETE distinct set for
low-cardinality columns (so 'jewellry' resolves to 'Jewelry' straight from the workspace),
and a sample + count/range for high-cardinality ones. Output is a value_domains.json the
harness reads -- no runtime SQL, no LLM.

Belongs alongside description generation in the learning pipeline; kept standalone so it
can enrich an existing schema without regenerating descriptions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "v2" / "src"))

from diracdata_v2.query import DuckDBEngine  # noqa: E402

_NUMERIC = {"BIGINT", "INTEGER", "DOUBLE", "DECIMAL", "FLOAT", "HUGEINT", "SMALLINT", "TINYINT", "REAL"}


def profile_column(engine: DuckDBEngine, table: str, column: str, *, max_values: int) -> dict:
    """Return the value domain for one column: complete set if small, else sample + stats."""
    q = column.replace('"', '""')
    t = table.replace('"', '""')
    # value + frequency, most common first; N+1 so we know if it's complete
    try:
        rows = engine.query(
            f'SELECT "{q}" AS v, COUNT(*) AS c FROM "{t}" WHERE "{q}" IS NOT NULL '
            f'GROUP BY "{q}" ORDER BY c DESC LIMIT {max_values + 1}',
            max_rows=max_values + 1,
        ).rows
    except Exception:  # noqa: BLE001 -- unprofilable column (e.g. blob) is skipped
        return {}
    distinct = len(rows)
    complete = distinct <= max_values
    values = [r[0] for r in rows[:max_values]]
    out: dict = {"distinct_at_least": distinct, "complete": complete, "values": _jsonable(values)}
    # numeric range is useful when the set is large
    if not complete:
        try:
            mn, mx = engine.query(f'SELECT MIN("{q}"), MAX("{q}") FROM "{t}"', max_rows=1).rows[0]
            out["min"], out["max"] = _one(mn), _one(mx)
        except Exception:  # noqa: BLE001
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="retail_analytics")
    ap.add_argument("--data-root", default=str(ROOT / "v2" / "data"))
    ap.add_argument("--out", default=None, help="Defaults to v2/context/<schema>_value_domains.json")
    ap.add_argument("--max-values", type=int, default=40, help="List ALL values up to this many; else sample.")
    args = ap.parse_args()

    engine = DuckDBEngine(data_root=Path(args.data_root), schema_name=args.schema)
    tables = engine.list_tables()
    domains: dict[str, dict[str, dict]] = {}
    for table in sorted(tables):
        cols = {}
        for column in engine.list_columns(table):
            dom = profile_column(engine, table, column, max_values=args.max_values)
            if dom:
                cols[column] = dom
        domains[table] = cols
        n_complete = sum(1 for d in cols.values() if d.get("complete"))
        print(f"  {table:<24} {len(cols)} cols profiled, {n_complete} with complete domains", file=sys.stderr)

    out_path = Path(args.out) if args.out else ROOT / "v2" / "context" / f"{args.schema}_value_domains.json"
    out_path.write_text(json.dumps(domains, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


def _jsonable(values: list) -> list:
    return [_one(v) for v in values]


def _one(value):
    from decimal import Decimal

    if isinstance(value, Decimal):
        return float(value)
    return value if isinstance(value, (int, float, str, bool)) or value is None else str(value)


if __name__ == "__main__":
    raise SystemExit(main())
