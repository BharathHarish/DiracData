"""Cross-source binding discovery -- the learned MAP OF THE ESTATE.

Find columns in DIFFERENT sources that refer to the same entity (e.g.
orders_pg.customer_id == fintech_lake.customers.customer_id) by sampling each key's values and
measuring overlap. A single engine cannot find this; it is exactly what a warehouse-native tool
structurally can't do. Sampling is deterministic (ORDER BY LIMIT), so the same key space is compared
on both sides and disjoint ids produce NO binding.
"""

from __future__ import annotations

from typing import Any

from diracdata.config import Config

_DEFAULTS = Config()


def _is_key(col: str) -> bool:
    c = col.lower()
    return c == "id" or c.endswith("_id") or c.endswith("key") or c.endswith("_key")


def _sample(engine: Any, table: str, col: str, n: int) -> set[str]:
    q = '"' + col.replace('"', '""') + '"'
    t = '"' + table.replace('"', '""') + '"'
    try:
        rows = engine.query(
            f"SELECT DISTINCT {q} AS v FROM {t} WHERE {q} IS NOT NULL ORDER BY 1 LIMIT {int(n)}", n).rows
    except Exception:  # noqa: BLE001  -- a key we can't sample simply yields no binding
        return set()
    return {str(r[0]) for r in rows}


def discover_bindings(registry: Any, *, sample: int = _DEFAULTS.binding_sample,
                      min_overlap: float = _DEFAULTS.binding_min_overlap) -> list[dict]:
    """Sample every id-like column across the estate and pair up columns (in different sources) whose
    sampled values overlap enough. Returns [{left, right, overlap_pct}, ...]."""
    occ: dict[str, list] = {}
    for src in registry.names():
        eng = registry.get(src)
        for table in eng.list_tables():
            for col in eng.list_columns(table):
                if _is_key(col):
                    occ.setdefault(col.lower(), []).append((src, table, col, _sample(eng, table, col, sample)))
    out: list[dict] = []
    for places in occ.values():
        for i in range(len(places)):
            for j in range(i + 1, len(places)):
                (sa, ta, ca, va), (sb, tb, cb, vb) = places[i], places[j]
                if sa == sb or not va or not vb:
                    continue
                overlap = len(va & vb) / min(len(va), len(vb))
                if overlap >= min_overlap:
                    out.append({"left": f"{sa}.{ta}.{ca}", "right": f"{sb}.{tb}.{cb}",
                                "overlap_pct": round(overlap * 100, 1)})
    return out
