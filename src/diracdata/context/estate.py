"""Render the ESTATE MAP injected into the prompt for a multi-source run: each data source, its SQL
dialect, and its tables/columns (from engine introspection) + the cross-source bindings the learning
pipeline verified. This is how the agent knows which store holds what, and how to combine across them.
"""

from __future__ import annotations

from typing import Any

_RULES = ("RULES: query a source with run_sql(sql, source='<name>') and write THAT source's SQL "
          "dialect; omit source for the default. To combine across sources, reduce each with run_sql "
          "first (aggregate at the source), then combine_results([ids], sql) in DuckDB dialect -- refer "
          "to each stored result by its result_id as a table. Mind freshness differences between "
          "sources (align as-of, or state the boundary).")


def render_estate(sources: Any, *, default_name: str | None = None, bindings: list | None = None,
                  max_cols: int = 40) -> str:
    names = sources.names()
    lines = [f"ESTATE ({len(names)} data sources):"]
    for nm in names:
        eng = sources.get(nm)
        tbls = []
        for t in eng.list_tables():
            cols = eng.list_columns(t)
            shown = ", ".join(cols[:max_cols]) + (", ..." if len(cols) > max_cols else "")
            tbls.append(f"{t}({shown})")
        tag = f"[{eng.dialect}]" + (" (default)" if nm == default_name else "")
        lines.append(f"- {nm} {tag}: " + "; ".join(tbls))
    if bindings:
        lines.append("CROSS-SOURCE BINDINGS (verified): " + "; ".join(
            f"{b['left']} = {b['right']}" + (f" ({b['overlap_pct']}% overlap)" if b.get("overlap_pct") else "")
            for b in bindings))
    lines.append(_RULES)
    return "\n".join(lines)
