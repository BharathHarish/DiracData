"""Deterministic metric-RCA pre-compute -- the fix for the delegation coin-flip.

When triage classifies a question as `rca` and resolves the target metric + two periods, this runs the
WHOLE attribution up-front instead of hoping the agent chooses to: it walks the metric's driver tree
(`decompose_metric`) and ranks every DEFINED dimension's slices (`rank_movers`) -- all of it assembled
from the semantic layer's PREDEFINED SQL and routed through the RESULT STORE, so every query becomes a
citable `result_id`. The reconciled, exact decomposition (residual ~0) plus the per-dimension movers are
injected into working memory as a narration brief. The analyst then NARRATES and sanity-checks it -- it
does not re-derive -- so the derivation reviewer passes first time (numbers carry provenance) and the
age/gender/income slices always run (they are DEFINED, not "remembered").

SQL does the data; the exact additive/multiplicative/ratio split is the tested Python kernels. The only
interpretation (which metric, which two periods) is folded into the triage call that already runs -- if
triage can't resolve it, pre-compute returns None and the harness falls back to the agentic RCA path.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from diracdata.config import Config
from diracdata.rca.tools import _layer, build_rca_tools
from diracdata.utils.streaming import null_sink

_DEFAULTS = Config()


def _sliceable_metric(workspace: Any, root: str) -> str | None:
    """The metric to slice dimensions on: `root` if it is directly-evaluable (has `sql`), else its
    TOP sql-bearing driver (breadth-first over depends_on). A formula-only top (e.g. net_revenue =
    revenue - refunds) can't be GROUP BY'd, so rank_movers must attribute its principal measured arm
    (revenue) -- which carries the move. None if nothing in the tree has sql."""
    metrics = _layer(workspace).get("metrics") or {}

    def has_sql(n: str) -> bool:
        return bool((metrics.get(n) or {}).get("sql"))

    seen: set[str] = set()
    q: deque[str] = deque([root])
    while q:
        n = q.popleft()
        if n in seen:
            continue
        seen.add(n)
        if has_sql(n):
            return n
        for d in (metrics.get(n) or {}).get("depends_on") or []:
            q.append(d)
    return None


def _fmt(v: float) -> str:
    """Compact number: thousands-separated integer for large magnitudes, else 4 sig-ish figures."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:,.0f}" if abs(f) >= 100 else f"{f:,.4g}"


def _render_tree(node: dict, lines: list[str], depth: int = 0) -> None:
    """Render the reconciled decomposition as an indented driver list -- each node's value move and its
    signed contribution to its parent's delta (and % share). This IS the answer's skeleton."""
    pct = node.get("pct")
    contrib = node.get("contribution")
    tail = ""
    if contrib is not None and pct is not None:
        tail = f"  -> contributes {_fmt(contrib)} ({pct*100:.0f}% of parent's move)"
    lines.append("  " * depth + f"- {node['name']}: {_fmt(node['v0'])} -> {_fmt(node['v1'])} "
                 f"(Δ {_fmt(node['delta'])}){tail}")
    for k in node.get("drivers", []):
        _render_tree(k, lines, depth + 1)


def precompute_rca(*, target: dict, workspace: Any, engine: Any, result_store: Any, memory: Any,
                   config: Config = _DEFAULTS, sink: Any = null_sink) -> dict | None:
    """Pre-run + inject the full metric-RCA. `target` = {metric, period_a, period_b} from triage.
    Returns {"metric", "result_ids", "brief"} on success (and injects a headline fact + the stored
    results into `memory`), or None to signal the agentic fallback (bad target / undefined metric /
    a tool rejection). Never raises -- a failure just means fall back."""
    metric = (target or {}).get("metric")
    pa, pb = (target or {}).get("period_a"), (target or {}).get("period_b")
    if not metric or pa in (None, "") or pb in (None, ""):
        return None
    try:
        tree = workspace.metric_tree(metric)
    except Exception:  # noqa: BLE001
        return None
    if not tree or not tree.get("defined") or not tree.get("drivers"):
        return None   # not a decomposable metric -> nothing to pre-compute

    captured: list[str] = []

    def runner(sql: str) -> dict:
        """Route every assembled attribution query through the result store: store it (citable
        result_id), register its numbers as faithful, and hand the rows back to the kernels."""
        env = result_store.run(sql)
        memory.note_result(env)
        memory.register_numbers(env.get("preview"))
        rid = env.get("result_id")
        if rid:
            captured.append(rid)
        return {"columns": env.get("columns", []), "rows": env.get("preview", [])}

    tools = {t.name: t for t in build_rca_tools(workspace=workspace, engine=engine, config=config,
                                                runner=runner)}
    dj = tools["decompose_metric"].func(metric, pa, pb)
    if not isinstance(dj, str) or not dj.startswith("{"):
        return None   # tool rejected (unusual metric SQL) -> agentic fallback
    decomp = json.loads(dj)

    # Rank dimensions on a directly-evaluable metric: the target if it has sql, else its top measured
    # arm (a formula-only top can't be grouped). So the demographic movers are ALWAYS computed, never
    # left to the agent to pick (which is what regressed to a bogus education_status "age proxy").
    slice_metric = _sliceable_metric(workspace, metric) or metric
    dims = list((_layer(workspace).get("dimensions") or {}).keys())
    movers: dict[str, list] = {}
    for d in dims:
        rj = tools["rank_movers"].func(slice_metric, d, pa, pb, config.rca_precompute_top_k)
        if isinstance(rj, str) and rj.startswith("{"):
            movers[d] = json.loads(rj).get("movers", [])

    # ---- assemble the narration brief (reconciled tree + where-it-concentrated) --------------------
    root = decomp["tree"]
    lines = [f"METRIC-RCA (PRE-COMPUTED + FULLY RECONCILED -- narrate this, do NOT recompute it):",
             f"Metric `{metric}` moved {_fmt(root['v0'])} -> {_fmt(root['v1'])} "
             f"(total change {_fmt(decomp['total_delta'])}) from {pa} to {pb}.",
             "",
             "EXACT DRIVER DECOMPOSITION (each node's contribution to its parent's move; residuals ~0):"]
    _render_tree(root, lines)
    if movers:
        on = f" (attributed on `{slice_metric}`, its principal measured arm)" if slice_metric != metric else ""
        lines += ["", f"WHERE IT CONCENTRATED{on} (top slices per DEFINED dimension, by surprise x impact):"]
        for d, ms in movers.items():
            if not ms:
                continue
            slug = "; ".join(f"{m['slice']} (Δ {_fmt(m['delta'])})" for m in ms[:config.rca_precompute_top_k])
            lines.append(f"  - {d}: {slug}")
    ids = ", ".join(captured) or "(none)"
    lines += ["",
              f"Every figure above comes from stored query results [{ids}] -- cite these result_ids in "
              "your plan/answer. Your job: SANITY-CHECK a couple of the headline figures (data_health "
              "where a number rests on a nullable join), then WRITE the business answer -- the drivers, "
              "the magnitudes, and which segments carried the move. Do NOT re-run the decomposition."]
    brief = "\n".join(lines)

    memory.add_fact(f"Metric-RCA for `{metric}` ({pa} to {pb}) was pre-computed and reconciled "
                    f"(total change {_fmt(decomp['total_delta'])}); driver contributions + per-dimension "
                    f"movers are in the task brief, all from stored results [{ids}].")
    sink("rca-precompute", "info",
         f"metric={metric} {pa}->{pb} | drivers reconciled | dims={len(movers)} | results=[{ids}]")
    return {"metric": metric, "result_ids": captured, "brief": brief, "decomposition": decomp,
            "movers": movers}
