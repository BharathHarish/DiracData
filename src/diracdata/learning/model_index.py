"""SemanticModelIndex -- the governed semantic_model.yaml as a QUERYABLE index the query agent greps on
demand, instead of a 100KB+ blob injected into every turn. Lookup by table, by `table.column`, or grep
across names + descriptions (glob/grep-style). This is what lets the governed model scale to a real
warehouse: retrieval scales, whole-model injection does not. Pairs with tiered-retrieval-verify-first."""

from __future__ import annotations

import difflib
import re
from typing import Any


class SemanticModelIndex:
    def __init__(self, sm: dict | None = None) -> None:
        sm = sm or {}
        self.schema: str = sm.get("schema", "")
        self.models: dict = sm.get("models") or {}
        self.relationships: list = sm.get("relationships") or []
        self.metrics: dict = sm.get("metrics") or {}
        self.dimensions: dict = sm.get("dimensions") or {}

    # -- shape helpers -------------------------------------------------------
    def empty(self) -> bool:
        return not self.models

    def header(self) -> str:
        """The tiny always-injected pointer: what exists + how to pull it (NOT the model itself)."""
        if self.empty():
            return ""
        return (f"A GOVERNED SEMANTIC MODEL exists for this schema: {len(self.models)} tables, "
                f"{len(self.relationships)} verified joins, {len(self.metrics)} metrics, "
                f"{len(self.dimensions)} dimensions. It is the source of truth for GRAIN (what one row "
                f"is), JOIN CARDINALITY (fan-out/chasm safety), and COMPLEX-COLUMN ACCESS RECIPES. Do "
                f"NOT re-derive these -- look them up: `model_search(pattern)` to grep tables/columns/"
                f"descriptions, `model_describe(table)` for one table's grain+columns+joins, "
                f"`model_lookup('table.column')` for a column's meaning + access recipe, "
                f"`model_joins(table)` for join cardinality, `model_metric`/`model_dimension` for "
                f"governed definitions. Honor whatever you retrieve.\n"
                f"RULE: before you write SQL against a COMPLEX/nested column (STRUCT / ARRAY / MAP / "
                f"JSON), FIRST `model_lookup('table.column')` to get its exact ACCESS RECIPE and copy it "
                f"verbatim -- do NOT trial-and-error the UNNEST/json_extract syntax. Before joining two "
                f"facts, `model_joins` first so you aggregate-then-join across a many_to_many.")

    # -- lookups -------------------------------------------------------------
    def tables(self) -> str:
        if self.empty():
            return "no governed model for this schema"
        out = []
        for t, md in self.models.items():
            out.append(f"- {t}: {md.get('grain', '?')}"
                       + (f" [{md.get('kind')}]" if md.get("kind") else "")
                       + f" ({len(md.get('columns') or {})} cols)")
        return f"{len(self.models)} tables:\n" + "\n".join(out)

    def describe(self, table: str) -> str:
        md = self.models.get(table)
        if md is None:
            near = _near(table, self.models)
            return f"no table '{table}' in the model" + (f"; did you mean: {', '.join(near)}" if near else "")
        lines = [f"TABLE {table}", f"  grain: {md.get('grain', '?')}",
                 f"  kind: {md.get('kind') or '?'}"]
        if md.get("long") or md.get("short"):
            lines.append(f"  desc: {md.get('long') or md.get('short')}")
        lines.append("  columns:")
        for c, cd in (md.get("columns") or {}).items():
            rec = f" | access_recipe: {cd['access_recipe']}" if cd.get("access_recipe") else ""
            dom = f" | domain: {_short(cd.get('value_domain'))}" if cd.get("value_domain") else ""
            lines.append(f"    - {c}: {cd.get('short') or cd.get('long') or ''}{rec}{dom}")
        if md.get("measures"):
            lines.append("  measures: " + ", ".join(f"{m.get('name')}({m.get('agg')})"
                                                     + ("" if m.get("additive", True) else " NON-ADDITIVE")
                                                     for m in md["measures"]))
        j = self._joins_for(table)
        if j:
            lines.append("  joins:")
            lines += [f"    - {e}" for e in j]
        return "\n".join(lines)

    def lookup(self, ref: str) -> str:
        ref = (ref or "").strip()
        if "." in ref:
            t, c = ref.split(".", 1)
            md = self.models.get(t)
            if md is None:
                return self.describe(t)
            cd = (md.get("columns") or {}).get(c)
            if cd is None:
                near = _near(c, md.get("columns") or {})
                return (f"no column '{c}' on {t}" + (f"; did you mean: {', '.join(near)}" if near else "")
                        + f"\n(use model_describe('{t}') for the full column list)")
            rec = f"\n  access_recipe: {cd['access_recipe']}" if cd.get("access_recipe") else ""
            dom = f"\n  value_domain: {_short(cd.get('value_domain'))}" if cd.get("value_domain") else ""
            return (f"{t}.{c}\n  short: {cd.get('short') or ''}\n  long: {cd.get('long') or ''}{rec}{dom}")
        return self.describe(ref)

    def search(self, pattern: str, limit: int = 40) -> str:
        """grep across table names, column names, and their descriptions + access recipes. Returns the
        matching `table` / `table.column` paths with a snippet. Regex if valid, else substring."""
        pat = (pattern or "").strip()
        if not pat:
            return "give a search pattern (a name fragment or regex)"
        try:
            rx = re.compile(pat, re.IGNORECASE)
            match = lambda s: bool(rx.search(s or ""))
        except re.error:
            low = pat.lower()
            match = lambda s: low in (s or "").lower()
        hits: list[str] = []
        for t, md in self.models.items():
            if match(t) or match(md.get("grain")) or match(md.get("short")) or match(md.get("long")):
                hits.append(f"{t}  [grain: {md.get('grain', '?')}]")
            for c, cd in (md.get("columns") or {}).items():
                if match(c) or match(cd.get("short")) or match(cd.get("long")) or match(cd.get("access_recipe")):
                    rec = f"  recipe: {cd['access_recipe']}" if cd.get("access_recipe") else ""
                    hits.append(f"{t}.{c}  {(cd.get('short') or '')[:70]}{rec}")
        for name, d in self.metrics.items():
            if match(name) or match(d.get("description")):
                hits.append(f"metric:{name}  {(d.get('description') or '')[:70]}")
        for name, d in self.dimensions.items():
            if match(name) or match(d.get("expr")):
                hits.append(f"dimension:{name}  expr={d.get('expr')}")
        if not hits:
            return f"no matches for '{pattern}'"
        extra = f"\n... (+{len(hits) - limit} more; refine the pattern)" if len(hits) > limit else ""
        return f"{len(hits)} matches for '{pattern}':\n" + "\n".join(f"  {h}" for h in hits[:limit]) + extra

    def joins(self, table: str) -> str:
        j = self._joins_for(table)
        if not j:
            return f"no joins recorded touching '{table}'"
        return f"joins touching {table} (aggregate additive measures BEFORE a many_to_many join):\n" + \
            "\n".join(f"  - {e}" for e in j)

    def metric(self, name: str) -> str:
        if not name:
            return "metrics: " + (", ".join(self.metrics) or "none")
        d = self.metrics.get(name)
        if d is None:
            near = _near(name, self.metrics)
            return f"no metric '{name}'" + (f"; did you mean: {', '.join(near)}" if near
                                            else f"; available: {', '.join(self.metrics) or 'none'}")
        return f"metric {name}: " + "; ".join(f"{k}={v}" for k, v in d.items() if v)

    def dimension(self, name: str) -> str:
        if not name:
            return "dimensions: " + (", ".join(self.dimensions) or "none")
        d = self.dimensions.get(name)
        if d is None:
            near = _near(name, self.dimensions)
            return f"no dimension '{name}'" + (f"; did you mean: {', '.join(near)}" if near
                                               else f"; available: {', '.join(self.dimensions) or 'none'}")
        return f"dimension {name}: " + "; ".join(f"{k}={v}" for k, v in d.items() if v)

    def _joins_for(self, table: str) -> list[str]:
        out = []
        for j in self.relationships:
            if j.get("left") == table or j.get("right") == table:
                lk = ",".join(j.get("left_keys") or [])
                rk = ",".join(j.get("right_keys") or [])
                out.append(f"{j.get('left')}({lk}) -> {j.get('right')}({rk}) : {j.get('cardinality', '?')}"
                           + (f"  [{j['verified_by']}]" if j.get("verified_by") else ""))
        return out


def _short(v: Any, cap: int = 120) -> str:
    s = str(v)
    return s if len(s) <= cap else s[:cap] + "…"


def _near(name: str, candidates: Any, limit: int = 5) -> list[str]:
    """Suggestions for a miss: substring hits first, then fuzzy (typo-tolerant) matches."""
    low = (name or "").lower()
    subs = [c for c in candidates if low and low in c.lower()]
    fuzzy = difflib.get_close_matches(name, list(candidates), n=limit, cutoff=0.6)
    seen, out = set(), []
    for c in subs + fuzzy:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:limit]


def build_model_lookup_tools(*, index: SemanticModelIndex) -> list[Any]:
    """READ tools over the governed model -- the query agent pulls only what a query touches, cited."""
    from langchain.tools import tool

    @tool("model_tables")
    def model_tables() -> str:
        """List every table in the governed semantic model with its verified grain, kind, and column
        count. The map -- start here to see what is modeled."""
        return index.tables()

    @tool("model_describe")
    def model_describe(table: str) -> str:
        """Full governed detail for ONE table: verified grain, kind, every column (with the access
        recipe for complex/nested columns + value domain), its measures, and the joins touching it."""
        return index.describe(table)

    @tool("model_lookup")
    def model_lookup(ref: str) -> str:
        """Look up one reference: 'table.column' returns that column's meaning + ACCESS RECIPE (copy it
        verbatim for nested fields) + value domain; a bare 'table' returns the table detail."""
        return index.lookup(ref)

    @tool("model_search")
    def model_search(pattern: str) -> str:
        """grep the governed model (glob/grep-style): matches `pattern` (regex or substring, case-
        insensitive) against table names, column names, descriptions, access recipes, and metric/
        dimension names. Returns matching `table` / `table.column` paths + a snippet. Use this to find
        where a concept lives before drilling in with model_describe / model_lookup."""
        return index.search(pattern)

    @tool("model_joins")
    def model_joins(table: str) -> str:
        """The join edges + verified CARDINALITY touching a table (many_to_one / one_to_one /
        many_to_many). Consult before joining facts so you aggregate-then-join across a many_to_many
        and never fan-out/chasm double-count."""
        return index.joins(table)

    @tool("model_metric")
    def model_metric(name: str = "") -> str:
        """A governed business metric's definition (sql/formula). Empty name lists all metric names."""
        return index.metric(name)

    @tool("model_dimension")
    def model_dimension(name: str = "") -> str:
        """A governed dimension's definition (expr + join path). Empty name lists all dimension names."""
        return index.dimension(name)

    return [model_tables, model_describe, model_lookup, model_search, model_joins,
            model_metric, model_dimension]
