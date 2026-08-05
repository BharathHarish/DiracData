"""Engine-backed RCA tools a metric-analysis specialist composes -- they turn the semantic layer's
PREDEFINED SQL into runnable queries so the agent never re-discovers the schema:

  - metric_series : evaluate one or more metrics/drivers over given periods (optionally sliced by a
                    dimension), assembling FROM + time-join + period filter + dimension-join from the layer.
  - attribute_change : the exact kernel as a calculator -- split a parent's change into its drivers
                    (additive / multiplicative / ratio), with the reconciliation residual.
  - rank_movers   : one grouped query + Adtributor -> the top slices of a dimension that carry the move.

Determinism lives only in the correct arithmetic; the specialist decides the walk. If a metric's SQL is
unusual (an alias/subquery leaf), the tool returns a clear error and the agent falls back to run_sql.
"""

from __future__ import annotations

import json
import re
from typing import Any

from diracdata.config import Config
from diracdata.rca.kernels import adtributor, attribute

_DEFAULTS = Config()
_TABLE_REF = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.[a-zA-Z_]")


def _layer(workspace: Any) -> dict:
    return getattr(workspace, "semantic_layer", None) or {}


def _clean_expr(sql: str) -> str:
    """Strip inline `-- comments` and collapse whitespace so a metric expression embeds safely into an
    assembled query (a stray `--` would comment out the rest of the line)."""
    no_comments = "\n".join(re.sub(r"--.*$", "", line) for line in (sql or "").splitlines())
    return " ".join(no_comments.split())


def _fact_of(metric_sql: str) -> str | None:
    m = _TABLE_REF.search(metric_sql or "")
    return m.group(1) if m else None


def _resolve_dim(layer: dict, name: str) -> tuple[str, dict] | str:
    """Resolve a dimension name tolerantly (product_category ~ category, billing_state ~ state). Returns
    (canonical_name, def) or a helpful 'did you mean' string listing the valid names."""
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


def build_rca_tools(*, workspace: Any, engine: Any, config: Config = _DEFAULTS) -> list[Any]:
    from langchain.tools import tool

    def _run(sql: str) -> dict:
        res = engine.query(sql, max_rows=config.query_max_rows)
        return {"columns": list(res.columns), "rows": [list(r) for r in res.rows]}

    def _resolve_metrics(names: list[str]) -> tuple[dict, str, dict]:
        """(name->sql, fact table, time binding) for a set of metrics that MUST share a fact table."""
        metrics = _layer(workspace).get("metrics") or {}
        exprs, fact = {}, None
        for nm in names:
            body = metrics.get(nm)
            if not body or not body.get("sql"):
                raise ValueError(f"metric '{nm}' has no directly-evaluable sql (it is derived -- "
                                 f"evaluate its depends_on drivers instead)")
            expr = _clean_expr(body["sql"])
            exprs[nm] = expr
            f = _fact_of(expr)
            if f is None:
                raise ValueError(f"could not determine the fact table for '{nm}' -- use run_sql for this one")
            fact = fact or f
            if f != fact:
                raise ValueError(f"metrics span different fact tables ({fact} vs {f}); call metric_series per fact")
        time = (_layer(workspace).get("time") or {}).get(fact)
        if not time:
            raise ValueError(f"no time binding for fact '{fact}' in the semantic layer's `time` section")
        return exprs, fact, time

    @tool("metric_series")
    def metric_series(metrics: list[str], periods: list, slice_by: str | None = None) -> str:
        """Evaluate one or more DEFINED metrics/drivers over PERIODS, assembling the query from the
        semantic layer (fact + time-join + period filter [+ dimension join]) -- so you never rediscover
        the schema. `metrics` share ONE fact table; `periods` is e.g. [2001, 2002] (any grain the layer's
        period_column supports); `slice_by` (optional) is a defined dimension name -> group by it too.
        Returns period (and slice) rows with each metric's value. Use this instead of hand-writing the
        driver queries; fall back to run_sql only for an unusual metric it rejects."""
        try:
            names = [metrics] if isinstance(metrics, str) else list(metrics)
            exprs, fact, time = _resolve_metrics(names)
            pcol = time["period_column"]
            select = [f"{pcol} AS period"]
            group = [pcol]
            joins = time["join"]
            if slice_by:
                resolved = _resolve_dim(_layer(workspace), slice_by)
                if isinstance(resolved, str):
                    return f"metric_series: {resolved}"       # 'did you mean' -- the agent retries
                _, dim = resolved
                select.append(f"{dim['sql']} AS slice")
                group.append(dim["sql"])
                joins += " " + dim["join"]
            select += [f"{sql} AS {nm}" for nm, sql in exprs.items()]
            q = (f"SELECT {', '.join(select)} FROM {fact} {joins} "
                 f"WHERE {pcol} IN ({_periods_sql(periods)}) GROUP BY {', '.join(group)} ORDER BY {', '.join(group)}")
            out = _run(q)
            return json.dumps({"metrics": names, "periods": periods, "slice_by": slice_by, **out}, default=str)
        except Exception as exc:  # noqa: BLE001 -- a rejected metric is feedback; the agent uses run_sql
            return f"metric_series error: {type(exc).__name__}: {exc}"

    @tool("attribute_change")
    def attribute_change(kind: str, children: list) -> str:
        """Split a parent metric's change into its drivers with the EXACT kernel -- no sloppy arithmetic.
        `kind`: 'additive' (M = sum of parts), 'multiplicative' (M = A x B, pass [A, B]), or 'ratio'
        (M = N / D, pass [numerator, denominator]). Each child is {"name","v0","v1"} (value in base and
        compared period). Returns each driver's signed contribution to the parent's delta, its % share,
        and the reconciliation `residual` (~0 for a valid split -- your cheap correctness check)."""
        try:
            triples = [(c["name"], float(c["v0"]), float(c["v1"])) for c in children]
            res = attribute(kind, triples)
            return json.dumps({
                "kind": kind, "parent_delta": res["parent_delta"], "residual": res["residual"],
                "contributions": [{"name": c.name, "v0": c.v0, "v1": c.v1, "delta": c.delta,
                                   "contribution": c.contribution, "pct": c.pct}
                                  for c in res["contributions"]]}, default=str)
        except Exception as exc:  # noqa: BLE001
            return (f"attribute_change error: {type(exc).__name__}: {exc}. children must be "
                    "[{name,v0,v1}]; ratio needs exactly [numerator, denominator].")

    @tool("rank_movers")
    def rank_movers(metric: str, dimension: str, period_a: Any, period_b: Any, top_k: int = 5) -> str:
        """Find WHERE a metric's change concentrated across a DIMENSION -- one grouped query + Adtributor
        (surprise x explanatory power). Returns the top_k slices carrying the move (base value, compared
        value, delta, impact). Use one call per dimension; no need to slice it by hand."""
        try:
            raw = metric_series.func([metric], [period_a, period_b], slice_by=dimension)  # type: ignore[attr-defined]
            data = json.loads(raw) if raw.startswith("{") else None
            if data is None:
                return raw                                       # surface the metric_series error
            a, b = str(period_a), str(period_b)
            per_slice: dict[str, list[float]] = {}
            cols = data["columns"]
            pi, si, vi = cols.index("period"), cols.index("slice"), cols.index(metric)
            for row in data["rows"]:
                key = str(row[si])
                bucket = per_slice.setdefault(key, [0.0, 0.0])
                if str(row[pi]) == a:
                    bucket[0] = float(row[vi] or 0)
                elif str(row[pi]) == b:
                    bucket[1] = float(row[vi] or 0)
            slices = [(k, v[0], v[1]) for k, v in per_slice.items()]
            ranked = adtributor(slices, top_k=int(top_k))
            return json.dumps({"metric": metric, "dimension": dimension,
                               "period_a": period_a, "period_b": period_b, "movers": ranked}, default=str)
        except Exception as exc:  # noqa: BLE001
            return f"rank_movers error: {type(exc).__name__}: {exc}"

    return [metric_series, attribute_change, rank_movers]
