"""The ONE metric-attribution primitive.

`attribute(metric, period_a, period_b, dimensions)` returns a COMPLETE, reconciled, cited
`AttributionResult` in a single call -- the deterministic "reduce" an RCA needs, so the agent spends
tokens only on judgment (binding, hypothesis, verification, narration), never on enumeration or
re-derivation. Two declared graphs are walked with equal rigour:

  * the METRIC driver tree -- recurse `depends_on`, split each parent's change with the exact kernels
    (additive / multiplicative / ratio), residuals ~0;
  * the DIMENSION set -- the agent SELECTS which declared dimensions to attribute (judgment: bind the
    user's "age groups" -> the declared `age_band`); the engine ranks each one completely.

Complete-by-CONTRACT: every requested dimension is present in the result -- ranked, or explicitly
`{status: unavailable, reason}` after a retry -- NEVER silently dropped. Every value comes from SQL
assembled from the semantic layer's predefined SQL and run through the result store, so each figure
carries a citable `result_id`. The catalog (all metrics + all dimensions) rides along, so the agent can
always SEE what else exists and refine on the fly.

This module replaces the earlier metric_series / attribute_change / rank_movers / decompose_metric
tool sprawl, the precompute pass, and the specialist-delegate: one primitive, called once by the
harness (seed) and reusable by the agent (follow-ups).
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from diracdata.config import Config
from diracdata.rca.kernels import adtributor, attribute as attribute_kernel
from diracdata.utils.streaming import null_sink

_DEFAULTS = Config()
_TABLE_REF = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.[a-zA-Z_]")


# ---- semantic-layer helpers (pure) --------------------------------------------------------------
def _layer(workspace: Any) -> dict:
    return getattr(workspace, "semantic_layer", None) or {}


def _clean_expr(sql: str) -> str:
    """Strip inline `-- comments` + collapse whitespace so a metric expression embeds safely."""
    no_comments = "\n".join(re.sub(r"--.*$", "", line) for line in (sql or "").splitlines())
    return " ".join(no_comments.split())


def _fact_of(metric_sql: str) -> str | None:
    m = _TABLE_REF.search(metric_sql or "")
    return m.group(1) if m else None


def _resolve_dim(layer: dict, name: str):
    """Resolve a dimension name tolerantly (product_category ~ category, billing_state ~ state).
    Returns (canonical_name, def) or a helpful 'not defined' string listing the valid names."""
    dims = layer.get("dimensions") or {}
    if name in dims:
        return name, dims[name]
    hits = [d for d in dims if name and (name in d or d.endswith("_" + name) or d.split("_")[-1] == name)]
    if len(hits) == 1:
        return hits[0], dims[hits[0]]
    return (f"dimension '{name}' is not defined. Valid dimensions: {', '.join(sorted(dims))}."
            + (f" Did you mean: {', '.join(hits)}?" if hits else ""))


def _periods_sql(periods: list) -> str:
    vals = []
    for p in periods:
        vals.append(str(int(p)) if isinstance(p, (int, float)) or str(p).lstrip("-").isdigit()
                    else "'" + str(p).replace("'", "''") + "'")
    return ", ".join(vals)


def _sliceable_metric(workspace: Any, root: str) -> str | None:
    """The metric to slice a dimension on: `root` if directly-evaluable (has sql), else its TOP
    sql-bearing driver (BFS over depends_on). A formula-only top (net_revenue = revenue - refunds)
    can't be GROUP BY'd, so attribute its principal measured arm (revenue) -- which carries the move."""
    metrics = _layer(workspace).get("metrics") or {}
    seen: set[str] = set()
    q: deque[str] = deque([root])
    while q:
        n = q.popleft()
        if n in seen:
            continue
        seen.add(n)
        if (metrics.get(n) or {}).get("sql"):
            return n
        for d in (metrics.get(n) or {}).get("depends_on") or []:
            q.append(d)
    return None


def default_dimensions(workspace: Any) -> list[str]:
    """The dimensions to attribute when the caller names none: those flagged `primary` in the layer,
    else every declared dimension (so a generic 'break it down' still fans out)."""
    dims = _layer(workspace).get("dimensions") or {}
    primary = [d for d, b in dims.items() if (b or {}).get("primary")]
    return primary or list(dims)


# ---- SQL assembly (runner: sql -> {columns, rows, result_id}) ------------------------------------
def _resolve_metrics(layer: dict, names: list[str]):
    """(name->sql, fact table, time binding) for metrics that MUST share a fact table."""
    metrics = layer.get("metrics") or {}
    exprs, fact = {}, None
    for nm in names:
        body = metrics.get(nm)
        if not body or not body.get("sql"):
            raise ValueError(f"metric '{nm}' has no directly-evaluable sql (it is derived)")
        expr = _clean_expr(body["sql"])
        exprs[nm] = expr
        f = _fact_of(expr)
        if f is None:
            raise ValueError(f"could not determine the fact table for '{nm}'")
        fact = fact or f
        if f != fact:
            raise ValueError(f"metrics span different fact tables ({fact} vs {f})")
    time = (layer.get("time") or {}).get(fact)
    if not time:
        raise ValueError(f"no time binding for fact '{fact}' in the layer's `time` section")
    return exprs, fact, time


def _metric_series(runner: Any, layer: dict, names: list[str], periods: list, slice_by: str | None = None):
    """Assemble + run one grouped-by-period query for a set of same-fact metrics (optionally sliced by a
    dimension). Returns (columns, rows, result_id). Raises on an assembly/binding problem."""
    exprs, fact, time = _resolve_metrics(layer, names)
    pcol = time["period_column"]
    select = [f"{pcol} AS period"]
    group = [pcol]
    joins = time["join"]
    if slice_by:
        resolved = _resolve_dim(layer, slice_by)
        if isinstance(resolved, str):
            raise ValueError(resolved)
        _, dim = resolved
        select.append(f"{dim['sql']} AS slice")
        group.append(dim["sql"])
        joins += " " + dim["join"]
    select += [f"{sql} AS {nm}" for nm, sql in exprs.items()]
    q = (f"SELECT {', '.join(select)} FROM {fact} {joins} "
         f"WHERE {pcol} IN ({_periods_sql(periods)}) GROUP BY {', '.join(group)} ORDER BY {', '.join(group)}")
    out = runner(q)
    return out.get("columns", []), out.get("rows", []), out.get("result_id")


# ---- the metric driver-tree walk (reconciled) ---------------------------------------------------
def _node_values(runner: Any, layer: dict, tree: dict, periods: list) -> dict:
    """Evaluate every directly-computable node (has sql) over the periods, batched by fact table."""
    flat: dict[str, dict] = {}

    def walk(n):
        flat[n["name"]] = n
        for c in n.get("drivers", []):
            walk(c)
    walk(tree)
    metrics = layer.get("metrics") or {}
    by_fact: dict[str, list] = {}
    for name in flat:
        body = metrics.get(name) or {}
        if body.get("sql"):
            fact = _fact_of(_clean_expr(body["sql"]))
            if fact:
                by_fact.setdefault(fact, []).append(name)
    values: dict[str, dict] = {}
    for names in by_fact.values():
        cols, rows, _rid = _metric_series(runner, layer, names, periods)
        pi = cols.index("period")
        for row in rows:
            per = row[pi]
            for nm in names:
                if nm in cols:
                    values.setdefault(nm, {})[per] = float(row[cols.index(nm)] or 0)
    return values


def _decompose(runner: Any, workspace: Any, metric: str, pa: Any, pb: Any) -> dict:
    """The fully-reconciled decomposition of `metric`'s change pa->pb: every driver's exact contribution
    to its parent's delta, top-down, residuals ~0. Mirrors the metric graph (depends_on)."""
    layer = _layer(workspace)
    tree = workspace.metric_tree(metric)
    if not tree or not tree.get("defined"):
        raise ValueError(f"'{metric}' is not a defined metric tree")
    periods = [pa, pb]
    vals = _node_values(runner, layer, tree, periods)

    def value_of(node):
        nm = node["name"]
        if nm in vals:
            return vals[nm].get(pa, 0.0), vals[nm].get(pb, 0.0)
        kids = node.get("drivers", [])
        kv = [value_of(k) for k in kids]
        dec = (node.get("decomposition") or "additive").lower()
        if dec == "multiplicative" and len(kv) == 2:
            return kv[0][0] * kv[1][0], kv[0][1] * kv[1][1]
        if dec == "ratio" and len(kv) == 2 and kv[1][0] and kv[1][1]:
            return kv[0][0] / kv[1][0], kv[0][1] / kv[1][1]
        return sum(v[0] for v in kv), sum(v[1] for v in kv)

    def node_out(node) -> dict:
        v0, v1 = value_of(node)
        out = {"name": node["name"], "v0": v0, "v1": v1, "delta": v1 - v0}
        kids = node.get("drivers", [])
        if kids:
            kind = (node.get("decomposition") or "additive").lower()
            triples = [(k["name"], *value_of(k)) for k in kids]
            res = attribute_kernel(kind, triples)
            out["decomposition"] = kind
            out["residual"] = res["residual"]
            cmap = {c.name: c for c in res["contributions"]}
            out["drivers"] = []
            for k in kids:
                sub = node_out(k)
                c = cmap.get(k["name"])
                if c is not None:
                    sub["contribution"], sub["pct"] = c.contribution, c.pct
                out["drivers"].append(sub)
        return out

    return node_out(tree)


# ---- dimension attribution (complete-by-contract: retry, explicit status) ------------------------
def _rank_dimension(runner: Any, layer: dict, slice_metric: str, dim: str, pa: Any, pb: Any,
                    top_k: int, retries: int = 1) -> dict:
    """Rank a dimension's slices by surprise x impact. COMPLETE-BY-CONTRACT: on a transient failure it
    retries, and if it still cannot rank it returns {status: unavailable, reason} -- it NEVER vanishes."""
    last = ""
    for _ in range(max(1, retries + 1)):
        try:
            cols, rows, rid = _metric_series(runner, layer, [slice_metric], [pa, pb], slice_by=dim)
            pi, si, vi = cols.index("period"), cols.index("slice"), cols.index(slice_metric)
            per_slice: dict[str, list[float]] = {}
            for row in rows:
                bucket = per_slice.setdefault(str(row[si]), [0.0, 0.0])
                if str(row[pi]) == str(pa):
                    bucket[0] = float(row[vi] or 0)
                elif str(row[pi]) == str(pb):
                    bucket[1] = float(row[vi] or 0)
            movers = adtributor([(k, v[0], v[1]) for k, v in per_slice.items()], top_k=int(top_k))
            return {"status": "ranked", "movers": movers, "result_id": rid}
        except Exception as exc:  # noqa: BLE001 -- retry once, then report unavailable (never drop)
            last = f"{type(exc).__name__}: {exc}"
    return {"status": "unavailable", "reason": last}


# ---- the result object --------------------------------------------------------------------------
@dataclass
class AttributionResult:
    metric: str
    period_a: Any
    period_b: Any
    total_delta: float
    tree: dict
    slice_metric: str
    dimensions: dict           # dim -> {status: ranked|unavailable, movers?/reason?, result_id?}
    catalog: dict              # {metrics: [...], dimensions: {name: {group?, primary?}}}
    result_ids: list = field(default_factory=list)

    def _fmt(self, v: Any) -> str:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return str(v)
        return f"{f:,.0f}" if abs(f) >= 100 else f"{f:,.4g}"

    def _render_tree(self, node: dict, lines: list, depth: int = 0) -> None:
        pct, contrib = node.get("pct"), node.get("contribution")
        tail = (f"  -> contributes {self._fmt(contrib)} ({pct*100:.0f}% of parent's move)"
                if contrib is not None and pct is not None else "")
        lines.append("  " * depth + f"- {node['name']}: {self._fmt(node['v0'])} -> {self._fmt(node['v1'])} "
                     f"(Δ {self._fmt(node['delta'])}){tail}")
        for k in node.get("drivers", []):
            self._render_tree(k, lines, depth + 1)

    def to_brief(self) -> str:
        """The narration brief injected for the analyst: the reconciled tree + EVERY requested
        dimension (ranked or explicitly unavailable) + the instruction to narrate, not recompute."""
        lines = ["METRIC ATTRIBUTION (PRE-COMPUTED + FULLY RECONCILED -- narrate this, do NOT recompute):",
                 f"Metric `{self.metric}` moved {self._fmt(self.tree['v0'])} -> {self._fmt(self.tree['v1'])} "
                 f"(total change {self._fmt(self.total_delta)}) from {self.period_a} to {self.period_b}.",
                 "",
                 "EXACT DRIVER DECOMPOSITION (each node's contribution to its parent's move; residuals ~0):"]
        self._render_tree(self.tree, lines)
        on = f" (attributed on `{self.slice_metric}`, its principal measured arm)" if self.slice_metric != self.metric else ""
        lines += ["", f"WHERE IT CONCENTRATED{on} -- report the top movers for EACH dimension below:"]
        for d, r in self.dimensions.items():
            if r.get("status") == "ranked" and r.get("movers"):
                slug = "; ".join(f"{m['slice']} (Δ {self._fmt(m['delta'])})" for m in r["movers"])
                lines.append(f"  - {d}: {slug}")
            else:
                lines.append(f"  - {d}: (could not attribute -- {r.get('reason', 'no data')}; note this gap)")
        others = [d for d in self.catalog.get("dimensions", {}) if d not in self.dimensions]
        if others:
            lines.append(f"OTHER DEFINED DIMENSIONS you can attribute on the fly with `attribute`: {', '.join(others)}.")
        ids = ", ".join(self.result_ids) or "(none)"
        lines += ["",
                  f"Every figure above comes from stored query results [{ids}] -- cite these result_ids. "
                  "SANITY-CHECK a couple of headline figures (data_health where a number rests on a nullable "
                  "join), then WRITE the business answer: the drivers, the magnitudes, and the top movers for "
                  "EACH requested dimension. Do NOT re-run the decomposition."]
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({"metric": self.metric, "period_a": self.period_a, "period_b": self.period_b,
                           "total_delta": self.total_delta, "tree": self.tree,
                           "slice_metric": self.slice_metric, "dimensions": self.dimensions,
                           "catalog": self.catalog, "result_ids": self.result_ids}, default=str)


def _catalog(workspace: Any) -> dict:
    layer = _layer(workspace)
    dims = layer.get("dimensions") or {}
    return {"metrics": list((layer.get("metrics") or {}).keys()),
            "dimensions": {d: {k: (b or {}).get(k) for k in ("group", "primary") if (b or {}).get(k)}
                           for d, b in dims.items()}}


def attribute(*, workspace: Any, engine: Any, result_store: Any, memory: Any, metric: str,
              period_a: Any, period_b: Any, dimensions: list | None = None,
              config: Config = _DEFAULTS, sink: Any = null_sink) -> AttributionResult | None:
    """Run the COMPLETE attribution and return an AttributionResult (or None if `metric` is not a
    decomposable defined tree -> caller falls back to run_sql). SQL is routed through the result store,
    so `memory` gains a citable result_id per query and the numbers are registered faithful."""
    layer = _layer(workspace)
    if not layer or not (layer.get("metrics") or {}).get(metric):
        return None

    captured: list[str] = []

    def runner(sql: str) -> dict:
        env = result_store.run(sql)
        memory.note_result(env)
        memory.register_numbers(env.get("preview"))
        rid = env.get("result_id")
        if rid:
            captured.append(rid)
        return {"columns": env.get("columns", []), "rows": env.get("preview", []), "result_id": rid}

    try:
        tree = _decompose(runner, workspace, metric, period_a, period_b)
    except Exception as exc:  # noqa: BLE001 -- undecomposable metric -> agentic fallback
        sink("attribute", "info", f"metric='{metric}' not decomposable ({exc}); falling back")
        return None

    slice_metric = _sliceable_metric(workspace, metric) or metric
    # Resolve the REQUESTED (agent-selected) dimensions against the catalog; default to primary dims.
    requested = [d for d in (dimensions or []) if d] or default_dimensions(workspace)
    dim_out: dict[str, dict] = {}
    for d in requested:
        resolved = _resolve_dim(layer, d)
        canon = d if isinstance(resolved, str) else resolved[0]   # tolerant bind; keep the requested label if unresolved
        dim_out[canon] = _rank_dimension(runner, layer, slice_metric, canon, period_a, period_b,
                                         config.rca_precompute_top_k)
        rid = dim_out[canon].get("result_id")
        if rid and rid not in captured:
            captured.append(rid)

    res = AttributionResult(metric=metric, period_a=period_a, period_b=period_b,
                            total_delta=tree["delta"], tree=tree, slice_metric=slice_metric,
                            dimensions=dim_out, catalog=_catalog(workspace), result_ids=captured)
    ranked = sum(1 for r in dim_out.values() if r.get("status") == "ranked")
    sink("attribute", "info", f"metric={metric} {period_a}->{period_b} | tree reconciled | "
                              f"dims {ranked}/{len(dim_out)} ranked | results=[{', '.join(captured)}]")
    return res


def build_attribution_tool(*, workspace: Any, engine: Any, result_store: Any, memory: Any,
                           config: Config = _DEFAULTS, sink: Any = null_sink) -> list[Any]:
    """The single agent-facing RCA tool: `attribute`. The agent BINDS a defined metric + the declared
    dimensions the user asked about; the engine returns the complete, cited decomposition. Use for a
    follow-up cut ('now by state') without re-exploring."""
    from langchain.tools import tool

    @tool("attribute")
    def attribute_tool(metric: str, period_a: Any, period_b: Any, dimensions: list | None = None) -> str:
        """Root-cause a DEFINED metric's change from period_a to period_b in ONE call: the fully-reconciled
        driver-tree decomposition (each driver's exact contribution) PLUS the top movers for each DEFINED
        dimension you pass (bind the user's words to a defined dimension name, e.g. 'age groups'->age_band).
        Omit `dimensions` to use the primary ones. Every figure is a citable result_id; sanity-check +
        narrate, do not recompute. Returns the decomposition, per-dimension movers, and the catalog of
        other dimensions you can attribute next."""
        res = attribute(workspace=workspace, engine=engine, result_store=result_store, memory=memory,
                        metric=metric, period_a=period_a, period_b=period_b, dimensions=dimensions,
                        config=config, sink=sink)
        if res is None:
            r = _resolve_dim(layer := _layer(workspace), metric)  # noqa: F841 -- reuse resolver for a helpful hint
            metrics = ", ".join((_layer(workspace).get("metrics") or {}).keys())
            return (f"attribute: '{metric}' is not a decomposable defined metric. Defined metrics: {metrics}. "
                    "Use run_sql for an ad-hoc metric.")
        return res.to_json()

    return [attribute_tool]


def seed_attribution(*, target: dict, workspace: Any, engine: Any, result_store: Any, memory: Any,
                     config: Config = _DEFAULTS, sink: Any = null_sink) -> AttributionResult | None:
    """Harness pre-call: run `attribute` with triage-bound (metric, periods, dimensions), inject a
    headline fact into `memory`, and return the result (its .to_brief() becomes the analyst's task).
    None -> triage couldn't resolve a target, or the metric isn't decomposable -> agentic fallback."""
    metric = (target or {}).get("metric")
    pa, pb = (target or {}).get("period_a"), (target or {}).get("period_b")
    if not metric or pa in (None, "") or pb in (None, ""):
        return None
    res = attribute(workspace=workspace, engine=engine, result_store=result_store, memory=memory,
                    metric=metric, period_a=pa, period_b=pb, dimensions=(target or {}).get("dimensions"),
                    config=config, sink=sink)
    if res is None:
        return None
    ids = ", ".join(res.result_ids) or "(none)"
    memory.add_fact(f"Metric attribution for `{metric}` ({pa} to {pb}) was pre-computed and reconciled "
                    f"(total change {res._fmt(res.total_delta)}); driver contributions + per-dimension "
                    f"movers are in the task brief, all from stored results [{ids}].")
    return res
