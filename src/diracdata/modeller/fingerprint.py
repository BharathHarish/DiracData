"""fingerprint_sql — canonicalise a SQL string into a shape dict.

Returns a plain dict of structural features the agent can reason about:
tables, joins, filter columns, aggregations, group_by, order_by, layer_mix,
has_cte, has_window.

Literal values are stripped out — so two runs of the same template with
different date bounds produce the same fingerprint. Table names are kept
qualified so the agent can see which raw/silver/gold layers a pattern hits.

This is a *tool*. It doesn't cluster, doesn't decide, doesn't judge — it
just returns the shape. The agent uses similarity.py to compare, and
decides what "similar enough" means.
"""
from __future__ import annotations
import re
from typing import Any, Dict, List
import sqlglot
from sqlglot import expressions as exp


def fingerprint_sql(sql: str, dialect: str = "duckdb") -> Dict[str, Any]:
    """Parse SQL → canonical shape dict. Returns {} on parse failure with 'parse_error' set."""
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except Exception as ex:
        return {"parse_error": str(ex)[:200], "sql_head": sql.strip()[:120]}
    if tree is None:
        return {"parse_error": "empty parse", "sql_head": sql.strip()[:120]}

    tables       = _extract_tables(tree)
    joins        = _extract_joins(tree)
    filters      = _extract_filters(tree)
    aggregations = _extract_aggregations(tree)
    group_by     = _extract_group_by(tree)
    order_by     = _extract_order_by(tree)
    layer_mix    = _layer_mix(tables)

    return {
        "tables":       tables,
        "joins":        joins,
        "filters":      filters,
        "aggregations": aggregations,
        "group_by":     group_by,
        "order_by":     order_by,
        "has_cte":      bool(list(tree.find_all(exp.CTE))),
        "has_window":   bool(list(tree.find_all(exp.Window))),
        "layer_mix":    layer_mix,
        "select_count": len(list(tree.find_all(exp.Select))),
    }


# ---------- extractors ----------

_S3_URI_RE = re.compile(r"s3://[^/]+/[^/]+/(raw|silver|gold|reference)/([^/'\"]+(?:/[^/'\"]+)?)")


def _extract_tables(tree) -> List[str]:
    """Return the set of qualified table refs the SQL reads.

    Priority: parse read_parquet('s3://...') → 'layer.name' (or 'raw.domain.table').
    Fallback: raw table names from FROM/JOIN clauses.
    """
    found: set[str] = set()
    # 1) read_parquet('s3://…') calls — most of our SQL uses this shape
    for lit in tree.find_all(exp.Literal):
        s = str(lit.this or "")
        m = _S3_URI_RE.search(s)
        if m:
            layer, name = m.group(1), m.group(2)
            # Normalise to layer.name (or layer.domain.table for raw)
            parts = [p for p in name.split("/") if p and p != "**" and not p.endswith(".parquet")]
            if layer == "raw" and len(parts) >= 2:
                found.add(f"raw.{parts[0]}.{parts[1]}")
            else:
                found.add(f"{layer}.{parts[0] if parts else name}")
    # 2) plain identifiers as fallback
    for t in tree.find_all(exp.Table):
        name = t.name
        if name and "read_parquet" not in name and name not in {"unnest", "generate_series"}:
            found.add(name)
    return sorted(found)


def _extract_joins(tree) -> List[Dict[str, str]]:
    """Return list of {left, right, type} for every JOIN in the tree.

    We report join TYPE (INNER/LEFT/RIGHT/FULL/CROSS) and the two side aliases.
    We deliberately don't extract the ON-columns literally — the agent can look
    at the SQL text if it needs that. This keeps fingerprints table-focused.
    """
    out = []
    for j in tree.find_all(exp.Join):
        kind = (j.args.get("kind") or "INNER").upper()
        side = (j.args.get("side") or "").upper()
        jtype = f"{side} {kind}".strip() if side else kind
        right = j.this
        right_alias = right.alias_or_name if right else "?"
        parent = j.parent
        left_alias = "?"
        if parent and hasattr(parent, "this") and parent.this:
            left_alias = parent.this.alias_or_name if hasattr(parent.this, "alias_or_name") else str(parent.this)
        out.append({"left": left_alias, "right": right_alias, "type": jtype})
    return out


def _extract_filters(tree) -> List[Dict[str, str]]:
    """Return WHERE predicates as {column, op}. Values stripped.

    Also captures BETWEEN and IN as their operator names.
    """
    out = []
    for where in tree.find_all(exp.Where):
        for pred in where.find_all(exp.Binary):
            left = pred.left
            col = _column_name(left)
            if col:
                out.append({"column": col, "op": pred.key.upper()})
        for btw in where.find_all(exp.Between):
            col = _column_name(btw.this)
            if col: out.append({"column": col, "op": "BETWEEN"})
        for inn in where.find_all(exp.In):
            col = _column_name(inn.this)
            if col: out.append({"column": col, "op": "IN"})
    # dedup while preserving order
    seen = set(); uniq = []
    for f in out:
        k = (f["column"], f["op"])
        if k in seen: continue
        seen.add(k); uniq.append(f)
    return uniq


def _extract_aggregations(tree) -> List[str]:
    """Return the set of aggregation functions used (SUM/COUNT/AVG/MIN/MAX/…)."""
    aggs = set()
    for a in tree.find_all(exp.AggFunc):
        aggs.add(a.__class__.__name__.upper())
    # Common function-call aggregates that sqlglot might not tag as AggFunc
    for f in tree.find_all(exp.Anonymous):
        name = (f.this or "").upper()
        if name in {"APPROX_QUANTILE", "COUNT_IF", "ARG_MAX", "ARG_MIN", "LIST_AGG"}:
            aggs.add(name)
    return sorted(aggs)


def _extract_group_by(tree) -> List[str]:
    """Return GROUP BY column names (or expression strings if not simple columns)."""
    out = []
    for g in tree.find_all(exp.Group):
        for expr in g.expressions:
            nm = _column_name(expr) or _stringify_short(expr)
            if nm: out.append(nm)
    return out


def _extract_order_by(tree) -> List[str]:
    out = []
    for o in tree.find_all(exp.Order):
        for expr in o.expressions:
            e = expr.this if hasattr(expr, "this") else expr
            nm = _column_name(e) or _stringify_short(e)
            if nm: out.append(nm)
    return out


def _column_name(node) -> str:
    if isinstance(node, exp.Column):
        return node.name or ""
    if isinstance(node, exp.Alias):
        return _column_name(node.this)
    return ""


def _stringify_short(node, cap: int = 40) -> str:
    try:
        s = node.sql()
    except Exception:
        s = str(node)
    return (s[:cap] + "…") if len(s) > cap else s


def _layer_mix(tables: List[str]) -> Dict[str, int]:
    mix = {"raw": 0, "silver": 0, "gold": 0, "reference": 0, "other": 0}
    for t in tables:
        first = t.split(".", 1)[0]
        mix[first if first in mix else "other"] += 1
    return mix
