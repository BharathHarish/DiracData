"""The finish gate -- what v3's outer loop never had.

v3's orchestrator free-wrote its final answer with no editor: it reported a total that didn't match
its own rows and drifted from the framed intent. Here, finishing goes through a GATE that composes:

  1. plan    -- every plan item must be `verified` (a blocked/ambiguous item routes to ask_user);
  2. cited   -- any result_id cited as backing must actually exist;
  3. verify  -- an INDEPENDENT reviewer (a one-shot model call that did not build the analysis) judges
                HOW the answer was authored: did it bind the confirmed intent, are the joins/grain
                right, was data-health/sanity considered, is it internally consistent (a stated total
                equals the sum of its parts)? It is handed the AUTHORING ARTIFACTS -- the plan trail,
                the facts/DQ ledger, the queries, and a sample of the values those queries returned --
                so it reasons about the DERIVATION, not string-matches numbers. A genuine request
                ambiguity is flagged so the loop asks the user instead of guessing.

Faithfulness is deliberately NOT a separate deterministic gate: scraping figures out of prose with a
regex mis-tokenised money/percent/ranges/list-markers and dead-looped on false positives. Number
provenance is a judgement the reviewer makes with the evidence in front of it. As a backstop, a
verifier that rejects `verify_max_rejects` times in a row yields the best answer WITH its unresolved
concern surfaced -- a deadlocked judgement must never burn the whole step budget.
"""

from __future__ import annotations

import json
from typing import Any

from diracdata.utils.streaming import loads_json, null_sink

from diracdata.config import Config
from diracdata.memory.working_memory import WorkingMemory
from diracdata.prompts import load_prompt
from diracdata.streaming import collect

_DEFAULTS = Config()
_VERIFY_PROMPT = load_prompt("verify") + "\n\n" + load_prompt("sql_rules")


def build_verify_payload(answer: str, memory: WorkingMemory, workspace: Any = None,
                         config: Config = _DEFAULTS) -> dict:
    """The context the independent verifier judges against. It carries the AUTHORING ARTIFACTS (plan
    trail, facts/DQ ledger, queries, and a sample of the numbers the queries returned) so the reviewer
    judges HOW the answer was derived -- intent binding, joins/grain, DQ/sanity, internal consistency --
    rather than matching exact result values. MUST carry the user clarifications, or it re-litigates the
    raw (possibly corrected) question and never converges; grounded in defined terms + precedents when a
    workspace is given."""
    values = sorted(memory.seen_numbers, key=abs, reverse=True)[:config.verify_evidence_values]
    payload = {
        "question": memory.goal,
        "confirmed_intent": memory.confirmed_intent or "(not framed)",
        "user_clarifications": [{"asked": q, "answer": a} for q, a in memory.clarifications]
                               or "(none)",
        "answer": answer,
        "plan": memory.plan.render() or "(no plan)",
        "authoring_notes": memory.facts or "(none)",   # verified bindings + data-health/sanity findings
        "queries": [{"result_id": rid, "sql": r.get("sql"), "row_count": r.get("row_count")}
                    for rid, r in memory.results.items()],
        "values_returned_by_queries": values,          # EVIDENCE the headline figures should derive from
    }
    if workspace is not None:
        try:
            defs = workspace.definitions_index()
        except Exception:  # noqa: BLE001
            defs = ""
        if defs:
            payload["defined_terms"] = defs
        try:
            ex = workspace.find_examples(memory.goal, limit=2)
        except Exception:  # noqa: BLE001
            ex = []
        if ex:
            payload["reference_precedents"] = [{"question": e.question, "sql": e.sql} for e in ex]
    return payload


def estate_dialects_note(sources: Any, base: str = "") -> str:
    """The per-source dialects the verifier needs: in a MULTI-ENGINE estate each query runs in its
    source's dialect and combine_results runs on the DuckDB reconciler. Without this the verifier
    assumes one dialect and wrongly flags valid cross-source SQL as "won't execute / fabricated".
    Returns `base` unchanged for a single source (nothing to disambiguate)."""
    names = list(sources.names()) if sources is not None else []
    if len(names) <= 1:
        return base
    lines = [f"  - source `{nm}`: {getattr(sources.get(nm), 'dialect', '?')} dialect" for nm in names]
    note = ("ESTATE ENGINES (each query runs in ITS source's dialect; combine_results / query_result run "
            "on the DuckDB reconciler over stored result_ids):\n" + "\n".join(lines))
    return (base + "\n\n" + note) if base else note


def make_verifier(model: Any, sink: Any = null_sink, workspace: Any = None, dialect_note: str = "",
                  config: Any = None):
    """Return verify(answer, memory) -> (verdict dict, tokens). One independent model call, grounded
    in the customer's defined terms + proven precedents when a workspace is provided, and in the
    target engine's dialect specifics (so it can catch a wrong date fn or array-index base)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    system = _VERIFY_PROMPT + (("\n\n" + dialect_note) if dialect_note else "")

    def verify(answer: str, memory: WorkingMemory) -> tuple[dict, int]:
        payload = build_verify_payload(answer, memory, workspace=workspace, config=config or _DEFAULTS)
        out = collect(model=model, stage="verify", sink=sink, config=config,
                      messages=[SystemMessage(content=system),
                                HumanMessage(content=json.dumps(payload, default=str))])
        v = loads_json(out["text"])
        ok = str(v.get("ok")).lower() != "false" and v.get("ok") is not False
        return ({"ok": bool(ok), "reason": str(v.get("reason") or ""),
                 "ambiguity": bool(v.get("ambiguity"))}, out["tokens"])

    return verify


class FinishGate:
    def __init__(self, *, memory: WorkingMemory, verifier: Any, config: Config = _DEFAULTS) -> None:
        self.memory = memory
        self.verifier = verifier
        self.result: dict | None = None
        self.tokens = 0
        self._max_rejects = config.verify_max_rejects
        self._rejects = 0

    def submit(self, answer: str, result_ids: list[str] | None) -> str:
        answer = (answer or "").strip()
        if not answer:
            return "REJECTED: empty answer."
        plan = self.memory.plan
        if plan.items and not plan.all_verified():
            un = [i.id for i in plan.items if i.status != "verified"]
            return (f"REJECTED: plan items not verified: {un}. Verify each with evidence "
                    f"(or mark it blocked and ask_user), then finish.")
        missing = [r for r in (result_ids or []) if r not in self.memory.results]
        if missing:
            return f"REJECTED: cited result_id(s) not found: {missing}. Cite the run_sql results your numbers came from."
        # Faithfulness is the reviewer's job now (it sees the queries + returned values), not a regex.
        verdict, vtok = self.verifier(answer, self.memory)
        self.tokens += vtok
        if not verdict["ok"]:
            if verdict.get("ambiguity"):
                return (f"REJECTED (ambiguous): {verdict['reason']} Ask the user ONE question with "
                        f"ask_user to resolve it, then finish.")
            self._rejects += 1
            if self._rejects < self._max_rejects:
                return f"REJECTED: {verdict['reason']} Fix it and finish again."
            # Loop-breaker: a deadlocked judgement must not burn the step budget -- yield the best
            # answer with the reviewer's unresolved concern surfaced, honestly, for the user to weigh.
            answer = (answer + f"\n\n> ⚠︎ Reviewer note (unresolved after {self._rejects} revisions): "
                      f"{verdict['reason']}")
            verdict = {**verdict, "ok": True, "accepted_with_caveat": True}
        self.result = {"answer": answer, "result_ids": list(result_ids or []), "verdict": verdict}
        return "ACCEPTED"
