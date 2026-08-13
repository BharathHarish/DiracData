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
    "semantic_model.yaml",
    "experiences.md",
    "ontology.yaml",
    "channel_maps.yaml",
)
_FABRIC_JSON_ARTIFACTS = (
    "metadata_descriptions.json",
    "join_facts.json",
    "gold_pairs.json",
    "database.json",
)

_DEFAULT_DESCRIBE_ROWS = 500


def _ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def bounded_query(engine: Any, sql: str, max_rows: int) -> Any:
    """engine.query with max_rows always supplied. DuckDBEngine requires it;
    the catalog SQLite wrapper accepts it as optional."""
    return engine.query(sql, int(max_rows))


class _DescribeResult:
    __slots__ = ("columns", "rows")

    def __init__(self, columns: List[str], rows: List[tuple]):
        self.columns = columns
        self.rows = rows


def describe_relation(engine: Any, table: str, *, max_rows: int = _DEFAULT_DESCRIBE_ROWS) -> Any:
    """Column names + types for `table`.

    DuckDBEngine.query wraps SQL as ``SELECT * FROM ({sql}) LIMIT n``, so a bare
    ``DESCRIBE "t"`` is not a valid subquery. Prefer engine.describe_columns when
    present; otherwise ``DESCRIBE SELECT * FROM t`` / information_schema.
    """
    if hasattr(engine, "describe_columns"):
        cols = engine.describe_columns(table) or []
        if cols:
            rows = [(c.get("column_name"), c.get("column_type")) for c in cols]
            return _DescribeResult(["column_name", "column_type"], rows)

    ident = _ident(table)
    last: Optional[BaseException] = None
    lit = str(table).replace("'", "''")
    for sql in (
        f"DESCRIBE SELECT * FROM {ident}",
        f"SELECT column_name, data_type FROM information_schema.columns "
        f"WHERE table_name = '{lit}' ORDER BY ordinal_position",
    ):
        try:
            return bounded_query(engine, sql, max_rows)
        except Exception as ex:  # noqa: BLE001
            last = ex
    raise last or RuntimeError(f"could not describe {table}")


def _is_complex_type(type_str: Optional[str]) -> bool:
    if not type_str:
        return False
    u = str(type_str).upper()
    return (
        "[]" in u or u.startswith("STRUCT") or u.startswith("MAP")
        or u.startswith("LIST") or u == "JSON" or "UNION" in u
    )


def load_column_fabric(cs: Any, catalog: str, db: str, table: str, column: str) -> dict:
    """Column metadata from metadata_descriptions.json (several historical shapes)."""
    md = cs.get(catalog, db, "metadata_descriptions.json", default={}) or {}
    if not isinstance(md, dict):
        return {}
    tmeta = (md.get("tables") or {}).get(table) or {}
    if isinstance(tmeta, dict):
        cols = tmeta.get("columns") or {}
        if isinstance(cols, dict) and column in cols:
            c = cols[column]
            return dict(c) if isinstance(c, dict) else {"description": str(c)}
    top = md.get("columns") or {}
    if isinstance(top, dict):
        by_table = top.get(table)
        if isinstance(by_table, dict) and column in by_table:
            c = by_table[column]
            return dict(c) if isinstance(c, dict) else {"description": str(c)}
        flat = top.get(f"{table}.{column}")
        if isinstance(flat, dict):
            return dict(flat)
        if isinstance(flat, str) and flat.strip():
            return {"description": flat}
    return {}


def _primary_access_recipe(leaves: List[dict]) -> Optional[str]:
    """Prefer a list/UNNEST leaf (most analyst-useful) else the first leaf access."""
    if not leaves:
        return None
    for leaf in leaves:
        acc = leaf.get("access") or ""
        if "UNNEST" in acc.upper() or "[*]" in (leaf.get("path") or ""):
            return acc
    return leaves[0].get("access")


def describe_column_enriched(
    cs: Any,
    catalog: str,
    db: str,
    engine: Any,
    table: str,
    column: str,
    *,
    max_rows: int = _DEFAULT_DESCRIBE_ROWS,
) -> dict:
    """Fabric metadata + live engine stats + derived access_recipe/runnable_example.

    Complex columns without authored fabric recipes get deterministic recipes from
    the DuckDB type tree (same substrate as learner nested/recipe_verify).
    """
    out: dict[str, Any] = {"database": db, "table": table, "column": column}
    fabric = load_column_fabric(cs, catalog, db, table, column)
    for k, v in fabric.items():
        if v not in (None, "", []):
            out[k] = v

    ctype: Optional[str] = None
    try:
        desc = describe_relation(engine, table, max_rows=int(max_rows))
        for row in desc.rows:
            if str(row[0]) == column:
                ctype = str(row[1]) if len(row) > 1 else None
                break
    except Exception as ex:  # noqa: BLE001
        out["warnings"] = [f"describe_relation: {ex}"]

    t_ident, c_ident = _ident(table), _ident(column)
    if _is_complex_type(ctype):
        try:
            r = bounded_query(
                engine, f"SELECT COUNT(*), COUNT({c_ident}) FROM {t_ident}", 1
            ).rows[0]
            out["row_count"], out["non_null"] = r[0], r[1]
        except Exception as ex:  # noqa: BLE001
            out.setdefault("warnings", []).append(f"stats: {ex}")
        try:
            trow = bounded_query(
                engine,
                f"SELECT typeof({c_ident}) FROM {t_ident} "
                f"WHERE {c_ident} IS NOT NULL LIMIT 1",
                1,
            ).rows
            if trow:
                ctype = ctype or str(trow[0][0])
        except Exception:  # noqa: BLE001
            pass
        out["type"] = ctype
        # Derive recipes when fabric omitted them
        try:
            from diracdata.learning.nested import leaf_paths, parse_type
            from diracdata.learning.recipe_verify import build_runnable, verify_runnable

            leaves = leaf_paths(parse_type(ctype or ""), column)[:24]
            if leaves:
                out.setdefault("access_recipes", leaves)
            if not out.get("access_recipe"):
                primary = _primary_access_recipe(leaves)
                if primary:
                    out["access_recipe"] = primary
                    out["access_recipe_source"] = "derived-from-type"
            else:
                out.setdefault("access_recipe_source", "fabric")
            if out.get("runnable_example"):
                out.setdefault("runnable_example_source", "fabric")
            elif ctype:
                sql = build_runnable(table, column, ctype)
                if sql:
                    if verify_runnable(engine, sql):
                        out["runnable_example"] = sql
                        out["runnable_dialect"] = "duckdb"
                        out["runnable_example_source"] = "verified"
                    else:
                        out["runnable_example_unverified"] = sql
                        out["runnable_example_source"] = "unverified"
        except Exception as ex:  # noqa: BLE001
            out.setdefault("warnings", []).append(f"recipe: {ex}")
        out["source"] = "fabric+engine" if fabric else "engine-live+derived-recipe"
        return out

    # Scalar path
    try:
        r = bounded_query(
            engine,
            f"SELECT COUNT(DISTINCT {c_ident}), COUNT(*), MIN({c_ident}), MAX({c_ident}) "
            f"FROM {t_ident}",
            1,
        ).rows[0]
        out.update({
            "distinct_count": r[0],
            "row_count": r[1],
            "min": str(r[2]),
            "max": str(r[3]),
            "type": ctype,
        })
    except Exception as ex:  # noqa: BLE001
        out.setdefault("warnings", []).append(f"stats: {ex}")
        if ctype:
            out["type"] = ctype
    out["source"] = "fabric+engine" if fabric else "engine-live (no fabric metadata yet)"
    return out


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
                  fabric_meta: Optional[dict] = None,
                  max_rows: int = _DEFAULT_DESCRIBE_ROWS) -> dict:
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
        try:
            schema = describe_relation(engine, table, max_rows=max_rows)
            name_i = 0
            type_i = 1 if len(schema.columns) > 1 else None
            for row in schema.rows:
                cname = row[name_i]
                ctype = row[type_i] if type_i is not None else None
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


def profile_target(engine: Any, target: str, *,
                   max_rows: int = _DEFAULT_DESCRIBE_ROWS) -> dict:
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
    ident = _ident(target)
    try:
        n = bounded_query(engine, f"SELECT COUNT(*) FROM {ident}", 1).rows[0][0]
        out["row_count"] = int(n)
    except Exception as ex:  # noqa: BLE001
        out["warnings"].append(f"count: {ex}")
    try:
        schema = describe_relation(engine, target, max_rows=max_rows)
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


def _normalize_join(raw: dict) -> Optional[dict]:
    """Normalize semantic_model relationships + join_facts.json into one shape."""
    if not isinstance(raw, dict):
        return None
    left = raw.get("left") or raw.get("left_table") or raw.get("from_table")
    right = raw.get("right") or raw.get("right_table") or raw.get("to_table")
    lkeys = raw.get("left_keys") or ([raw["left_col"]] if raw.get("left_col") else [])
    rkeys = raw.get("right_keys") or ([raw["right_col"]] if raw.get("right_col") else [])
    if not left or not right or not lkeys or not rkeys:
        return None
    out = {
        "left": left,
        "right": right,
        "left_keys": list(lkeys),
        "right_keys": list(rkeys),
        "cardinality": raw.get("cardinality") or raw.get("card") or raw.get("type") or "",
        "disposition": raw.get("disposition") or raw.get("join_type") or "",
        "verified_by": raw.get("verified_by") or "",
        "match_rate": raw.get("match_rate"),
        "fan_out_avg": raw.get("fan_out_avg"),
        "fan_out_max": raw.get("fan_out_max"),
        "children_per_parent_avg": raw.get("children_per_parent_avg"),
        "sampled": raw.get("sampled"),
    }
    return out


def load_join_facts(cs: Any, catalog: str, db: str) -> List[dict]:
    """Merge semantic_model.yaml relationships with join_facts.json (dedupe by endpoint)."""
    import yaml
    seen: set[tuple] = set()
    out: List[dict] = []

    def _add(raw: dict) -> None:
        j = _normalize_join(raw)
        if not j:
            return
        key = (j["left"], tuple(j["left_keys"]), j["right"], tuple(j["right_keys"]))
        rkey = (j["right"], tuple(j["right_keys"]), j["left"], tuple(j["left_keys"]))
        if key in seen or rkey in seen:
            # Prefer the record that already has behavioural fields.
            for i, prev in enumerate(out):
                pk = (prev["left"], tuple(prev["left_keys"]),
                      prev["right"], tuple(prev["right_keys"]))
                if pk in (key, rkey):
                    if prev.get("match_rate") is None and j.get("match_rate") is not None:
                        out[i] = j
                    elif not prev.get("verified_by") and j.get("verified_by"):
                        out[i] = {**prev, "verified_by": j["verified_by"]}
                    break
            return
        seen.add(key)
        out.append(j)

    sm_text = cs.get_text(catalog, db, "semantic_model.yaml") or ""
    if sm_text.strip():
        try:
            sm = yaml.safe_load(sm_text) or {}
        except Exception:  # noqa: BLE001
            sm = {}
        for raw in (sm.get("relationships") or []) + (sm.get("joins") or []):
            if isinstance(raw, dict):
                _add(raw)

    facts = cs.get(catalog, db, "join_facts.json", default=[]) or []
    if isinstance(facts, list):
        for raw in facts:
            if isinstance(raw, dict):
                _add(raw)
    return out


def _live_children_per_parent(engine: Any, j: dict) -> Optional[float]:
    """Avg many-side rows per parent key (dimension→fact amplification)."""
    left, right = j["left"], j["right"]
    lk, rk = j["left_keys"][0], j["right_keys"][0]
    try:
        ln = bounded_query(engine, f'SELECT COUNT(*), COUNT(DISTINCT "{lk}") FROM "{left}"', 1).rows[0]
        rn = bounded_query(engine, f'SELECT COUNT(*), COUNT(DISTINCT "{rk}") FROM "{right}"', 1).rows[0]
        l_uniq = (ln[1] / ln[0]) if ln[0] else 0
        r_uniq = (rn[1] / rn[0]) if rn[0] else 0
        if r_uniq >= l_uniq:
            fact, fkey = left, lk
        else:
            fact, fkey = right, rk
        cpp = bounded_query(
            engine,
            f'SELECT AVG(c) FROM (SELECT COUNT(*) AS c FROM "{fact}" '
            f'WHERE "{fkey}" IS NOT NULL GROUP BY "{fkey}")',
            1,
        ).rows[0][0]
        return round(float(cpp), 2) if cpp is not None else None
    except Exception:  # noqa: BLE001
        return None


def render_join_card(j: dict) -> str:
    """One-line join card (+ warning line when grain risk)."""
    lk = ",".join(j.get("left_keys") or [])
    rk = ",".join(j.get("right_keys") or [])
    head = f"{j.get('left')}({lk}) -> {j.get('right')}({rk}) : {j.get('cardinality') or '?'}"
    parts = []
    if j.get("match_rate") is not None:
        try:
            parts.append(f"match={float(j['match_rate'])*100:.1f}%")
        except (TypeError, ValueError):
            pass
    if j.get("fan_out_avg") is not None:
        parts.append(f"fanout avg={j['fan_out_avg']} max={j.get('fan_out_max')}")
    if j.get("children_per_parent_avg") is not None:
        parts.append(f"children/parent≈{j['children_per_parent_avg']}")
    if j.get("disposition"):
        parts.append(f"use {j['disposition']}")
    line = head + ("  " + " ".join(parts) if parts else "")
    if j.get("verified_by") and not parts:
        line += f"  [{j['verified_by']}]"
    warn = []
    mr = j.get("match_rate")
    if isinstance(mr, (int, float)) and mr < 0.99:
        warn.append(f"⚠ {(1-mr)*100:.1f}% orphan on INNER")
    cpp = j.get("children_per_parent_avg")
    if isinstance(cpp, (int, float)) and cpp > 1.5:
        warn.append(f"⚠ ~{cpp}x children/parent: aggregate-then-join before averaging parent measures")
    elif isinstance(j.get("fan_out_max"), (int, float)) and j["fan_out_max"] > 1:
        warn.append(f"⚠ fan-out risk (max {j['fan_out_max']}): aggregate before joining")
    return line + (("\n    " + " ; ".join(warn)) if warn else "")


def measure_join(engine: Any, left_table: str, left_col: str,
                 right_table: str, right_col: str) -> dict:
    """Execute a candidate join edge (learning-agent verify_join logic).

    Returns fact/dimension orientation, orphan_pct, fan_out, children_per_parent_avg,
    grain (1:1|1:many), and verdict accept|reject.
    """
    tables = set(engine.list_tables() or [])
    if left_table not in tables or right_table not in tables:
        return {"verdict": "reject", "reason": "unknown table"}
    try:
        lcols = set(engine.list_columns(left_table) or [])
        rcols = set(engine.list_columns(right_table) or [])
    except Exception as ex:  # noqa: BLE001
        return {"verdict": "reject", "reason": f"list_columns: {ex}"}
    if left_col not in lcols or right_col not in rcols:
        return {"verdict": "reject", "reason": "unknown column"}
    try:
        ln_row = bounded_query(
            engine, f'SELECT COUNT(*), COUNT(DISTINCT "{left_col}") FROM "{left_table}"', 1
        ).rows[0]
        rn_row = bounded_query(
            engine, f'SELECT COUNT(*), COUNT(DISTINCT "{right_col}") FROM "{right_table}"', 1
        ).rows[0]
        ln, ld = int(ln_row[0] or 0), int(ln_row[1] or 0)
        rn, rd = int(rn_row[0] or 0), int(rn_row[1] or 0)
    except Exception as ex:  # noqa: BLE001
        return {"verdict": "reject", "reason": str(ex)}
    if not ln or not rn:
        return {"verdict": "reject", "reason": "empty table"}
    if rd / rn >= ld / ln:
        (ft, fc, fn), (dt, dc), dim_uniq = (left_table, left_col, ln), (right_table, right_col), rd / rn
    else:
        (ft, fc, fn), (dt, dc), dim_uniq = (right_table, right_col, rn), (left_table, left_col), ld / ln
    on = f'F."{fc}" = D."{dc}"'
    try:
        orphan = int(bounded_query(
            engine,
            f'SELECT COUNT(*) FROM "{ft}" F LEFT JOIN "{dt}" D ON {on} WHERE D."{dc}" IS NULL',
            1,
        ).rows[0][0] or 0)
        joined = int(bounded_query(
            engine, f'SELECT COUNT(*) FROM "{ft}" F JOIN "{dt}" D ON {on}', 1
        ).rows[0][0] or 0)
        cpp = bounded_query(
            engine,
            f'SELECT AVG(c) FROM (SELECT COUNT(*) AS c FROM "{ft}" '
            f'WHERE "{fc}" IS NOT NULL GROUP BY "{fc}")',
            1,
        ).rows[0][0]
    except Exception as ex:  # noqa: BLE001
        return {"verdict": "reject", "reason": str(ex)}
    matched = fn - orphan
    orphan_pct = round(100 * orphan / fn, 2) if fn else None
    fan_out = round(joined / matched, 2) if matched else None
    children_per_parent_avg = round(float(cpp), 2) if cpp is not None else None
    grain = "1:1" if dim_uniq > 0.999 and (fan_out or 0) <= 1.001 else "1:many"
    match_rate = round(1.0 - (orphan / fn), 4) if fn else 0.0
    # Cardinality from fact→dim orientation
    if grain == "1:1":
        cardinality = "1-1"
    else:
        cardinality = "N-1"  # many fact rows per dim key
    return {
        "verdict": "reject" if matched == 0 else "accept",
        "fact": ft,
        "dimension": dt,
        "matched_rows": matched,
        "orphan_pct": orphan_pct,
        "fan_out": fan_out,
        "children_per_parent_avg": children_per_parent_avg,
        "grain": grain,
        "match_rate": match_rate,
        "cardinality_measured": cardinality,
        "disposition": "INNER" if match_rate >= 0.999 else "LEFT",
        "verified_by": (
            f"measured: matched={matched}, orphan_pct={orphan_pct}, "
            f"fan_out={fan_out}, children/parent≈{children_per_parent_avg}"
        ),
    }


def join_path_cards(cs: Any, catalog: str, db: str, table: str = "",
                    engine: Any = None) -> dict:
    """Join cards for one table (or all joins if table empty)."""
    joins = load_join_facts(cs, catalog, db)
    if table:
        joins = [j for j in joins if table in (j["left"], j["right"])]
    if engine is not None:
        for j in joins:
            if j.get("children_per_parent_avg") is None:
                cpp = _live_children_per_parent(engine, j)
                if cpp is not None:
                    j["children_per_parent_avg"] = cpp
    cards = [render_join_card(j) for j in joins]
    return {
        "ok": True,
        "database": db,
        "table": table or None,
        "n_joins": len(joins),
        "joins": joins,
        "cards": cards,
        "text": "\n".join(cards) if cards else (
            f"no joins recorded touching {table!r}" if table else "no joins recorded"
        ),
    }
