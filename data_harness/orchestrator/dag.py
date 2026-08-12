"""Topological sort of transforms — same-layer dependencies only.

Cross-layer dependencies (silver reads raw, gold reads silver) are stage-ordered
in orchestrator/run.py, not through this DAG.
"""
from __future__ import annotations
from typing import Dict, List


def topo_sort(nodes: List[str], edges: List[tuple[str, str]]) -> List[str]:
    """Kahn's algorithm — returns nodes in order (deps first)."""
    in_deg = {n: 0 for n in nodes}
    graph: Dict[str, List[str]] = {n: [] for n in nodes}
    for (dep, node) in edges:
        if dep in graph:
            graph[dep].append(node)
            in_deg[node] = in_deg.get(node, 0) + 1
    ready = [n for n, d in in_deg.items() if d == 0]
    out = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for m in graph.get(n, []):
            in_deg[m] -= 1
            if in_deg[m] == 0:
                ready.append(m)
    if len(out) != len(nodes):
        raise ValueError("cycle in DAG")
    return out
