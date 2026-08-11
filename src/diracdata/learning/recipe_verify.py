"""V3-S2: verified-runnable recipe snippets for COMPLEX columns.

Cursor's #1 gap: the recipe path (e.g. `UNNEST(UNNEST(fulfillment.shipments).items).sku`) is NOT
directly runnable in DuckDB -- nested UNNEST throws a Binder Error, and the alias plumbing has
quirks. So we take each column's recipe, generate a CTE-STAGED DuckDB SELECT that actually returns
the leaf, VERIFY it against the engine, and store the runnable form on the column so the analyst
(or the client LLM) can copy the working example verbatim.

Post-compile enrichment: measurement + generation, no LLM. Same pattern as join_facts (S1).
"""

from __future__ import annotations

from typing import Any

from diracdata.learning.nested import TypeNode, parse_type


def _deepest_leaf_stages(node: TypeNode, base: str, *, stages: list, path_parts: list,
                          max_depth: int = 6, depth: int = 0) -> list | None:
    """Walk the tree to a scalar leaf, staging every LIST as a CTE. Returns a description of the
    leaf: [(stage_num, unnest_expr), ...] + the final leaf column path from the last stage."""
    if depth > max_depth:
        return None
    if node.kind == "struct" and node.fields:
        # dive into a field that has depth (prefer the deepest to test the hard case)
        best = None
        for name, ft in node.fields:
            child_base = f"{base}.{name}" if base else name
            r = _deepest_leaf_stages(ft, child_base, stages=list(stages),
                                     path_parts=path_parts + [name], max_depth=max_depth, depth=depth + 1)
            if r is not None and (best is None or len(r[0]) >= len(best[0])):
                best = r
        return best
    if node.kind == "list":
        new_stage = (len(stages), base)                # (idx, unnest_expr)
        alias = f"s{new_stage[0]}"                    # alias for the new CTE
        # after unnesting, the child expression refers to the alias's implicit column
        # For DuckDB: SELECT UNNEST(x) AS elem FROM prev -> access elem in next stage.
        return _deepest_leaf_stages(node.element, alias + ".elem",
                                    stages=stages + [new_stage], path_parts=path_parts + ["[*]"],
                                    max_depth=max_depth, depth=depth + 1)
    # scalar / map / json => leaf
    return (stages, base, path_parts)


def build_runnable(table: str, column: str, type_str: str) -> str | None:
    """Emit a CTE-staged DuckDB SELECT for the deepest scalar leaf of a complex column.
    Returns None for scalar columns (no recipe needed)."""
    tree = parse_type(type_str)
    if tree.kind not in ("struct", "list"):
        return None
    walk = _deepest_leaf_stages(tree, column, stages=[], path_parts=[])
    if not walk:
        return None
    stages, leaf_expr, _ = walk
    if not stages:                                     # a struct with only scalar fields -> direct path
        return f'SELECT {leaf_expr} FROM "{table}" LIMIT 5'
    ctes = []
    prev = f'"{table}"'
    for idx, unnest_expr in stages:
        alias = f"s{idx}"
        # unnest_expr already refers to the previous alias's `.elem` when idx>0
        ctes.append(f'{alias} AS (SELECT UNNEST({unnest_expr}) AS elem FROM {prev})')
        prev = alias
    return "WITH " + ",\n     ".join(ctes) + f"\nSELECT {leaf_expr} FROM {prev} LIMIT 5"


def verify_runnable(engine: Any, sql: str) -> bool:
    try:
        engine.query(sql, 5)
        return True
    except Exception:  # noqa: BLE001
        return False


def enrich_recipes(*, model: Any, engine: Any) -> int:
    """For every COMPLEX column recorded in the model, generate a runnable SELECT for the deepest
    leaf and verify it against the engine; store the working form on the column dict as
    `runnable_example`. Returns the number of columns successfully verified."""
    from diracdata.learning.profiler import _column_type   # reuse the type sniffer
    ok = 0
    tables = set(engine.list_tables())
    for t, cmap in (model.columns or {}).items():
        if t not in tables:
            continue
        for c, cd in cmap.items():
            if not isinstance(cd, dict) or not cd.get("access_recipe"):
                continue
            try:
                ty = _column_type(engine, t, c)
                sql = build_runnable(t, c, ty)
                if sql and verify_runnable(engine, sql):
                    cd["runnable_example"] = sql
                    cd["runnable_dialect"] = "duckdb"
                    ok += 1
            except Exception as exc:  # noqa: BLE001
                cd["runnable_error"] = f"{type(exc).__name__}: {exc}"[:200]
    return ok
