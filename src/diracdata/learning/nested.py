"""Nested / complex-type profiling for the learning agent.

Scalar `column_facts` (COUNT DISTINCT, MIN/MAX) is meaningless on a STRUCT / LIST / MAP / JSON column --
worse, it can error. This module DESCENDS into a complex type: it parses the column's declared type into
a tree, emits the exact ACCESS RECIPE for every nested leaf (dot for struct, UNNEST/[i] for a list,
json_extract for JSON, map[] for a map), measures array-length distribution, samples JSON key sets, and
shallow-profiles the top leaves. The learning AGENT then describes the inner structure so the query agent
can actually reach `fulfillment.shipments[*].items[*].sku` instead of guessing.

Deterministic MEASUREMENT + shape derivation only; the agent still decides what it means.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


# ---- type parsing (DuckDB type strings) ---------------------------------------------------------
@dataclass
class TypeNode:
    kind: str                      # struct | list | map | json | scalar
    raw: str                       # the type string
    fields: list = field(default_factory=list)   # struct: [(name, TypeNode)]
    element: Any = None            # list: element TypeNode ; map: (key TypeNode, val TypeNode)

    @property
    def is_complex(self) -> bool:
        return self.kind in ("struct", "list", "map", "json")


def _split_top(s: str, sep: str = ",") -> list[str]:
    """Split on `sep` at paren-depth 0 (so STRUCT(a INT, b STRUCT(c INT)) splits into two fields)."""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "(":
            depth += 1
        elif ch in ")":
            depth -= 1
        if ch == sep and depth == 0:
            out.append(cur); cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def parse_type(s: str) -> TypeNode:
    """Parse a DuckDB type string into a TypeNode tree. Handles STRUCT(...), T[], MAP(K,V), JSON, scalars."""
    s = s.strip()
    if s.endswith("[]"):                                   # list: element is everything before the last []
        return TypeNode("list", s, element=parse_type(s[:-2]))
    up = s.upper()
    if up == "JSON":
        return TypeNode("json", s)
    if up.startswith("STRUCT(") and s.endswith(")"):
        inner = s[s.index("(") + 1:-1]
        fields = []
        for part in _split_top(inner):
            part = part.strip()
            # `name TYPE` -- name may be quoted; TYPE is the rest
            m = re.match(r'^("(?:[^"]|"")*"|\w+)\s+(.*)$', part, re.S)
            if m:
                name = m.group(1).strip('"').replace('""', '"')
                fields.append((name, parse_type(m.group(2))))
        return TypeNode("struct", s, fields=fields)
    if up.startswith("MAP(") and s.endswith(")"):
        inner = s[s.index("(") + 1:-1]
        parts = _split_top(inner)
        if len(parts) == 2:
            return TypeNode("map", s, element=(parse_type(parts[0]), parse_type(parts[1])))
    return TypeNode("scalar", s)


# ---- access recipes (type tree -> per-leaf access expression, no SQL) ----------------------------
def leaf_paths(node: TypeNode, base: str, *, label: str = "", depth: int = 0, max_depth: int = 6) -> list[dict]:
    """Every reachable leaf, with a human LABEL, a DuckDB ACCESS expression, and the leaf type.
    A `[*]` in the label marks a list traversal (needs UNNEST); json/map leaves are dynamic."""
    if depth > max_depth:
        return [{"path": label or base, "access": base, "type": node.raw, "note": "…(deeper; truncated)"}]
    if node.kind == "struct":
        out = []
        for name, ft in node.fields:
            out += leaf_paths(ft, f"{base}.{name}", label=f"{label}.{name}" if label else name,
                              depth=depth + 1, max_depth=max_depth)
        return out
    if node.kind == "list":
        # element reached via UNNEST(base) (or base[i]); mark the traversal with [*]
        return leaf_paths(node.element, f"UNNEST({base})", label=f"{label}[*]",
                          depth=depth + 1, max_depth=max_depth)
    if node.kind == "map":
        kt, vt = node.element
        return [{"path": f"{label} (map)", "access": f"{base}[<key>]", "type": f"MAP({kt.raw}->{vt.raw})",
                 "keys_via": f"map_keys({base})", "note": "dynamic keys; map_keys() to enumerate"}]
    if node.kind == "json":
        return [{"path": f"{label} (json)", "access": f"json_extract({base}, '$.<key>')", "type": "JSON",
                 "keys_via": f"json_keys({base})", "note": "dynamic keys; sample json_keys() to enumerate"}]
    return [{"path": label or base, "access": base, "type": node.raw}]


# ---- measurement (length dist, json keys, shallow leaf samples) ----------------------------------
def nested_shape(engine: Any, table: str, column: str, type_str: str, *, sample: int = 200) -> dict:
    """Profile a complex column: its parsed shape, per-leaf access recipes, array-length distribution,
    sampled JSON/map key sets, and a small sample of top leaves. All measured on `engine`."""
    tree = parse_type(type_str)
    q, t = _id(column), _id(table)
    out: dict = {"column": column, "type": type_str, "kind": tree.kind,
                 "shape": describe_shape(tree), "access_recipes": leaf_paths(tree, column)[:24]}
    # array-length distribution (top-level list, or a list nested one level in a struct)
    try:
        if tree.kind == "list":
            r = engine.query(f"SELECT MIN(len({q})), ROUND(AVG(len({q})),2), MAX(len({q})), "
                             f"COUNT(*) FILTER (WHERE {q} IS NULL OR len({q})=0) FROM {t}", 1).rows[0]
            out["array_length"] = {"min": r[0], "avg": r[1], "max": r[2], "empty_or_null": r[3]}
    except Exception:  # noqa: BLE001
        pass
    # JSON: sample the union of top-level keys actually present
    if tree.kind == "json" or (tree.kind == "list" and tree.element.kind == "json"):
        col_expr = f"UNNEST({q})" if tree.kind == "list" else q
        try:
            rows = engine.query(f"SELECT DISTINCT UNNEST(json_keys({col_expr})) AS k FROM {t} "
                                f"WHERE {col_expr} IS NOT NULL LIMIT 50", 50).rows
            out["json_keys_seen"] = sorted({r[0] for r in rows if r[0] is not None})
        except Exception:  # noqa: BLE001
            pass
    # shallow samples: for a list-of-struct, sample a few unnested field tuples; for struct, first row
    try:
        if tree.kind == "list" and tree.element.kind == "struct":
            fields = ", ".join(f"e.{_id(n)}" for n, _ in tree.element.fields[:6])
            rows = engine.query(f"SELECT {fields} FROM {t}, UNNEST({q}) AS u(e) "
                                f"WHERE {q} IS NOT NULL LIMIT 5", 5).rows
            out["sample_elements"] = [list(r) for r in rows]
        elif tree.kind == "struct":
            rows = engine.query(f"SELECT {q} FROM {t} WHERE {q} IS NOT NULL LIMIT 3", 3).rows
            out["sample"] = [str(r[0])[:200] for r in rows]
        elif tree.kind == "json":
            rows = engine.query(f"SELECT {q} FROM {t} WHERE {q} IS NOT NULL LIMIT 3", 3).rows
            out["sample"] = [str(r[0])[:200] for r in rows]
    except Exception:  # noqa: BLE001
        pass
    return out


def describe_shape(node: TypeNode, indent: int = 0) -> str:
    """A compact readable rendering of the nested schema (what the agent describes to users)."""
    pad = "  " * indent
    if node.kind == "struct":
        return "STRUCT{\n" + "\n".join(
            f"{pad}  {n}: {describe_shape(ft, indent + 1)}" for n, ft in node.fields) + f"\n{pad}}}"
    if node.kind == "list":
        return f"LIST<{describe_shape(node.element, indent)}>"
    if node.kind == "map":
        k, v = node.element
        return f"MAP<{k.raw} -> {v.raw}>"
    if node.kind == "json":
        return "JSON"
    return node.raw


def _id(v: str) -> str:
    return '"' + str(v).replace('"', '""') + '"'
