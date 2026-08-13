"""Catalog MCP harness primitives — search / profile / sql_diff.

Coding-harness style helpers for the catalog MCP. Pure functions over CatalogStore
+ engine; no curated recipes. Used by catalog_tools() so observation stays
search → profile → try/diff → run_sql instead of browsing every markdown file.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, List, Optional


_FABRIC_TEXT_ARTIFACTS = (
    "database.md",
    "semantic_layer.yaml",
)
_FABRIC_JSON_ARTIFACTS = (
    "metadata_descriptions.json",
    "join_facts.json",
    "gold_pairs.json",
    "database.json",
)


def _tokens(q: str) -> List[str]:
    return [t for t in re.split(r"[^\w]+", (q or "").lower()) if len(t) >= 2]


def _snippet(text: str, needle: str, radius: int = 80) -> str:
    low = text.lower()
    i = low.find(needle.lower()) if needle else -1
    if i < 0:
        return text[: radius * 2].replace("\n", " ")
    a, b = max(0, i - radius), min(len(text), i + len(needle) + radius)
    return text[a:b].replace("\n", " ")


def search_fabric(cs: Any, catalog: str, query: str, *,
                  db: Optional[str] = None, limit: int = 25) -> dict:
    """Grep learned fabric text (md/json/yaml) for query tokens. db=None → all DBs."""
    toks = _tokens(query)
    if not toks:
        return {"ok": False, "error": "empty query", "hits": []}

    dbs: Iterable[str]
    if db:
        dbs = [db]
    else:
        dbs = cs.list_databases(catalog)

    hits: List[dict] = []

    # Catalog-level once
    cat_md = cs.get_catalog_text(catalog, "catalog.md") or ""
    if cat_md:
        score = sum(1 for t in toks if t in cat_md.lower())
        if score:
            hits.append({
                "score": score, "scope": "catalog", "database": None,
                "artifact": "catalog.md",
                "snippet": _snippet(cat_md, next(t for t in toks if t in cat_md.lower())),
            })

    for d in dbs:
        for name in _FABRIC_TEXT_ARTIFACTS:
            text = cs.get_text(catalog, d, name) or ""
            if not text:
                continue
            low = text.lower()
            score = sum(1 for t in toks if t in low)
            if not score:
                continue
            needle = next(t for t in toks if t in low)
            hits.append({
                "score": score, "scope": "database", "database": d,
                "artifact": name, "snippet": _snippet(text, needle),
            })
        for name in _FABRIC_JSON_ARTIFACTS:
            obj = cs.get(catalog, d, name, default=None)
            if obj is None:
                continue
            text = json.dumps(obj, default=str)
            low = text.lower()
            score = sum(1 for t in toks if t in low)
            if not score:
                continue
            needle = next(t for t in toks if t in low)
            hits.append({
                "score": score, "scope": "database", "database": d,
                "artifact": name, "snippet": _snippet(text, needle),
            })

    hits.sort(key=lambda h: (-h["score"], h.get("database") or "", h["artifact"]))
    return {
        "ok": True, "catalog": catalog, "query": query,
        "databases_scanned": list(dbs) if not isinstance(dbs, list) else dbs,
        "hit_count": min(len(hits), limit),
        "hits": hits[:limit],
    }


def _glob_match(name: str, pattern: str) -> bool:
    """Simple substring / *glob* match (case-insensitive)."""
    p = (pattern or "").strip().lower()
    n = (name or "").lower()
    if not p:
        return False
    if "*" in p or "?" in p:
        rx = re.escape(p).replace(r"\*", ".*").replace(r"\?", ".")
        return re.fullmatch(rx, n) is not None
    return p in n


def search_schema(engine: Any, pattern: str, *, limit: int = 50,
                  fabric_meta: Optional[dict] = None) -> dict:
    """Structural find over live tables/columns (and optional fabric metadata)."""
    hits: List[dict] = []
    warnings: List[str] = []

    tables: List[str] = []
    try:
        tables = list(engine.list_tables() or [])
    except Exception as ex:  # noqa: BLE001
        warnings.append(f"list_tables: {ex}")

    for table in tables:
        if _glob_match(table, pattern):
            hits.append({"table": table, "column": None, "type": None, "match": "table"})
        cols: List[tuple] = []
        try:
            schema = engine.query(f'DESCRIBE "{table}"')
            # DuckDB DESCRIBE: column_name, column_type, ...
            name_i = 0
            type_i = 1 if len(schema.columns) > 1 else None
            for row in schema.rows:
                cname = row[name_i]
                ctype = row[type_i] if type_i is not None else None
                cols.append((cname, ctype))
                if _glob_match(str(cname), pattern):
                    hits.append({
                        "table": table, "column": cname, "type": ctype, "match": "column",
                    })
        except Exception as ex:  # noqa: BLE001
            warnings.append(f"describe {table}: {ex}")
            # Fabric fallback for this table
            if fabric_meta:
                tmeta = ((fabric_meta.get("tables") or {}).get(table) or {})
                for cname in (tmeta.get("columns") or {}):
                    if _glob_match(cname, pattern):
                        hits.append({
                            "table": table, "column": cname, "type": None,
                            "match": "column", "source": "fabric",
                        })

    # Fabric-only tables not listed by engine
    if fabric_meta:
        for table, tmeta in (fabric_meta.get("tables") or {}).items():
            if table in tables:
                continue
            if _glob_match(table, pattern):
                hits.append({
                    "table": table, "column": None, "type": None,
                    "match": "table", "source": "fabric",
                })
            for cname in (tmeta.get("columns") or {}):
                if _glob_match(cname, pattern):
                    hits.append({
                        "table": table, "column": cname, "type": None,
                        "match": "column", "source": "fabric",
                    })

    return {
        "ok": True, "pattern": pattern,
        "hit_count": min(len(hits), limit),
        "hits": hits[:limit],
        "warnings": warnings,
    }


def profile_target(engine: Any, target: str) -> dict:
    """Profile `table` or `table.column` via learning.profiler.column_facts."""
    from diracdata.learning.profiler import column_facts

    target = (target or "").strip()
    if not target:
        return {"ok": False, "error": "target required (table or table.column)"}
    if "." in target:
        table, column = target.split(".", 1)
        try:
            facts = column_facts(engine, table, column)
            return {"ok": True, "target": target, **facts}
        except Exception as ex:  # noqa: BLE001
            return {"ok": False, "target": target, "error": str(ex)}

    # Table-level: row count + column list (soft)
    out: dict = {"ok": True, "target": target, "table": target, "warnings": []}
    try:
        n = engine.query(f'SELECT COUNT(*) FROM "{target}"').rows[0][0]
        out["row_count"] = int(n)
    except Exception as ex:  # noqa: BLE001
        out["warnings"].append(f"count: {ex}")
    try:
        schema = engine.query(f'DESCRIBE "{target}"')
        out["columns"] = [
            {"name": r[0], "type": r[1] if len(r) > 1 else None} for r in schema.rows
        ]
    except Exception as ex:  # noqa: BLE001
        out["warnings"].append(f"describe: {ex}")
    return out


def _run_side(engine: Any, sql: str, max_rows: int) -> dict:
    clean = (sql or "").strip().rstrip(";")
    if not clean:
        return {"ok": False, "error": "empty sql"}
    try:
        res = engine.query(clean, max_rows)
        rows = [list(r) for r in res.rows]
        numeric_sums: dict = {}
        for i, col in enumerate(res.columns):
            vals = []
            for row in rows:
                v = row[i] if i < len(row) else None
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    vals.append(float(v))
            if vals:
                numeric_sums[col] = {"sum": sum(vals), "n": len(vals),
                                     "mean": sum(vals) / len(vals)}
        return {
            "ok": True, "columns": list(res.columns), "row_count": len(rows),
            "rows": rows, "numeric": numeric_sums,
        }
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": f"{type(ex).__name__}: {ex}"}


def sql_diff(engine: Any, sql_a: str, sql_b: str, *, max_rows: int = 100) -> dict:
    """Run two SELECTs and compare rowcounts + numeric column aggregates."""
    a = _run_side(engine, sql_a, max_rows)
    b = _run_side(engine, sql_b, max_rows)
    delta: dict = {}
    if a.get("ok") and b.get("ok"):
        delta["row_count_delta"] = b["row_count"] - a["row_count"]
        shared = set(a.get("numeric") or {}) & set(b.get("numeric") or {})
        sum_deltas = {}
        for col in shared:
            sum_deltas[col] = {
                "sum_a": a["numeric"][col]["sum"],
                "sum_b": b["numeric"][col]["sum"],
                "delta": b["numeric"][col]["sum"] - a["numeric"][col]["sum"],
            }
        delta["numeric_sum_deltas"] = sum_deltas
    return {"ok": True, "a": a, "b": b, "delta": delta}
