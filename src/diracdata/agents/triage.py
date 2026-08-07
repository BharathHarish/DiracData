"""Triage -- the recall + classify step that v5 runs BEFORE framing.

One cheap model call that reasons over the question, the schema's defined terms, and a few candidate
precedents (from the workspace example bank) to decide two things:

  - task_type: "rca" (a root-cause analysis of a defined/decomposable metric -> load the Metric-RCA
    skill) vs "analytics" (everything else -> the lean core loop).
  - lane: "fast" (a blessed precedent is a near-exact match -> adapt it and just verify) vs "cold".

It is a routing judgment, not the analysis -- kept small and agentic. Returns a validated dict; falls
back to (analytics, cold) on any parse failure so v5 degrades to the core loop rather than crashing.
"""

from __future__ import annotations

import json
from typing import Any

from diracdata.config import Config
from diracdata.prompts import load_prompt
from diracdata.streaming import collect
from diracdata.utils.streaming import loads_json, null_sink

_DEFAULTS = Config()
_PROMPT = load_prompt("triage")


def make_triage(model: Any, sink: Any = null_sink, config: Config = _DEFAULTS):
    """Return triage(goal, workspace) -> dict. One model call over the defined terms + candidate
    precedents; the workspace supplies both (definitions_index + find_examples)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    def triage(goal: str, workspace: Any, learned: str = "", recent: str = "") -> dict:
        metrics = ""
        candidates: list[dict] = []
        if workspace is not None:
            try:
                metrics = workspace.definitions_index()
            except Exception:  # noqa: BLE001
                metrics = ""
            try:
                for ex in workspace.find_examples(goal, limit=config.triage_precedents):
                    candidates.append({"question": ex.question, "sql": ex.sql})
            except Exception:  # noqa: BLE001
                candidates = []
        payload = {"question": goal,
                   # The running summary of the CONVERSATION SO FAR -- resolve a follow-up's pronouns
                   # ("there", "the same", "those") against it BEFORE classifying, else a follow-up looks
                   # vague/context-free and gets mis-routed as cold.
                   "recent_conversation": (recent or "").strip() or "(none -- first turn)",
                   "defined_terms": metrics or "(none)",
                   "candidate_precedents": candidates or "(none)",
                   # Learned experience (SQL PATTERNS / GOTCHAS / BINDINGS curated from prior runs) is a
                   # FIRST-CLASS recall source: a matching learned pattern justifies lane=fast + a seed.
                   "learned_experience": (learned or "").strip() or "(none)"}
        out = collect(model=model, stage="triage", sink=sink, config=config,
                      messages=[SystemMessage(content=_PROMPT),
                                HumanMessage(content=json.dumps(payload, default=str))])
        return {**_parse(out["text"]), "tokens": out["tokens"]}

    return triage


def _parse(text: str) -> dict:
    """Validate the triage JSON; default to (analytics, cold) so v5 never crashes on a bad reply."""
    v = loads_json(text) if text else {}
    if not isinstance(v, dict):
        v = {}
    task = "rca" if str(v.get("task_type", "")).strip().lower() == "rca" else "analytics"
    lane = "fast" if str(v.get("lane", "")).strip().lower() == "fast" else "cold"
    sql = (v.get("precedent_sql") or "").strip() or None
    q = (v.get("precedent_question") or "").strip() or None
    if lane == "fast" and not sql:            # a fast lane with no precedent SQL is meaningless
        lane = "cold"
    # RCA target -- the single interpretation step for the deterministic pre-compute: which DEFINED
    # metric, and the two periods to compare. Only meaningful for task=rca; a missing piece -> no
    # pre-compute (the harness falls back to the agentic RCA path), so keep it strictly optional.
    rca = None
    if task == "rca":
        m = (v.get("rca_metric") or "").strip()
        pa, pb = _period(v.get("period_a")), _period(v.get("period_b"))
        if m and pa is not None and pb is not None:
            rca = {"metric": m, "period_a": pa, "period_b": pb}
    return {"task_type": task, "lane": lane, "precedent_sql": sql, "precedent_q": q,
            "rca_target": rca, "reasoning": str(v.get("reasoning") or "")[:200]}


def _period(p: Any) -> Any:
    """Normalize a period literal: int if it is one (year 2001), else a non-empty string, else None."""
    if p is None:
        return None
    s = str(p).strip()
    if not s or s.lower() in ("null", "none", "unknown"):
        return None
    return int(s) if s.lstrip("-").isdigit() else s
