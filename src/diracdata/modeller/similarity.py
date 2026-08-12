"""similarity(fp_a, fp_b) — 0.0-1.0 structural similarity between two fingerprints.

Weighted Jaccard across feature sets. Weights are documented; the agent
can inspect these weights via the docstring but doesn't change them —
because "how similar is similar" is decided by the agent looking at the
returned score, not by tweaking weights.
"""
from __future__ import annotations
from typing import Any, Dict, List


# Feature weights. Documented, not agent-tunable. Sum to 1.0.
_WEIGHTS = {
    "tables":       0.40,   # patterns are most similar when they touch the same tables
    "joins":        0.15,   # join structure matters
    "group_by":     0.20,   # same grain = strong signal
    "filters":      0.15,   # same filter columns = same predicate shape
    "aggregations": 0.10,
}


def similarity(fp_a: Dict[str, Any], fp_b: Dict[str, Any]) -> float:
    """Weighted Jaccard similarity between two fingerprints. Returns 0.0-1.0.

    Returns 0.0 if either fingerprint is empty or has a parse_error.
    """
    if not fp_a or not fp_b or fp_a.get("parse_error") or fp_b.get("parse_error"):
        return 0.0

    tables_sim = _jaccard(set(fp_a.get("tables", [])),
                          set(fp_b.get("tables", [])))
    joins_sim  = _jaccard(_join_signatures(fp_a.get("joins", [])),
                          _join_signatures(fp_b.get("joins", [])))
    group_sim  = _jaccard(set(fp_a.get("group_by", [])),
                          set(fp_b.get("group_by", [])))
    filter_sim = _jaccard(_filter_signatures(fp_a.get("filters", [])),
                          _filter_signatures(fp_b.get("filters", [])))
    agg_sim    = _jaccard(set(fp_a.get("aggregations", [])),
                          set(fp_b.get("aggregations", [])))

    total = (
        _WEIGHTS["tables"]       * tables_sim +
        _WEIGHTS["joins"]        * joins_sim  +
        _WEIGHTS["group_by"]     * group_sim  +
        _WEIGHTS["filters"]      * filter_sim +
        _WEIGHTS["aggregations"] * agg_sim
    )
    return round(total, 4)


def similarity_matrix(fps: List[Dict[str, Any]]) -> List[List[float]]:
    """N×N similarity matrix over a list of fingerprints. Symmetric, diag=1.0.

    Agent can call this to see the full picture of pattern similarity
    across the workload, then decide clustering cutoff itself.
    """
    n = len(fps)
    m = [[0.0] * n for _ in range(n)]
    for i in range(n):
        m[i][i] = 1.0
        for j in range(i + 1, n):
            s = similarity(fps[i], fps[j])
            m[i][j] = s
            m[j][i] = s
    return m


# ---------- helpers ----------

def _jaccard(a: set, b: set) -> float:
    if not a and not b: return 1.0
    if not a or not b:  return 0.0
    return round(len(a & b) / len(a | b), 4)


def _join_signatures(joins: list) -> set:
    """A join contributes an unordered {(right, type), (left, type)} signature.
    We drop the ON-columns intentionally — join shape is what matters."""
    out = set()
    for j in joins or []:
        # order-independent — swap left/right so 'INNER a-b' == 'INNER b-a'
        pair = tuple(sorted([j.get("left", "?"), j.get("right", "?")]))
        out.add((pair, j.get("type", "INNER")))
    return out


def _filter_signatures(filters: list) -> set:
    return {(f.get("column", "?"), f.get("op", "?")) for f in (filters or [])}
