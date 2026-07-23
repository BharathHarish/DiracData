"""The orchestrator -- a thin decide-loop over focused skills.

Not a fixed pipeline. It holds state (goal, findings, resolved definitions) and each turn
DECIDES the single best next move, then dispatches a focused skill. No step has to
understand everything; context is pulled just-in-time by the skill that needs it. The loop
is durable and self-correcting: it keeps going -- resolving terms, authoring sub-queries,
reconciling -- until the answer is verifiably correct or a budget is hit.

Skills the orchestrator dispatches:
  author    (Analyst)        -- answer ONE concrete sub-question with verified SQL. The analyst
                                looks up any metric/term definitions itself (visible tool calls)
                                and probes the data before committing.
  reconcile (deterministic)  -- the "test": do the parts sum to the whole? A non-zero residual
                                IS the wrinkle -- it tells the loop a driver/segment is missing.
  clarify / finish           -- terminal moves.

`decide` is a small model call with NO tools and NO context dump -- it only picks the next
move from the state. The heavy tool use lives inside the analyst skill.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from diracdata_v3.streaming import Sink, null_sink, stream_and_collect

_DECIDE_PROMPT = """You are the ORCHESTRATOR of a long-running analytics agent. You do not write SQL. Each
turn you look at the goal and the findings so far and pick the single best NEXT MOVE, the way
a senior analyst drives an investigation to a verified answer.

You are told which business terms / metrics have DEFINED meanings available (an index) so you
know the driver tree to decompose. The analyst that answers each sub-question looks up the exact
definition itself and binds to it -- you do not need to resolve anything first.

Judge honestly whether the goal is FULLY and VERIFIABLY answered:
- A simple lookup is done once you have the number.
- A "why did X change / what drove it / root cause" question is NOT done until you have (a)
  quantified the change, (b) decomposed it to a SPECIFIC driver/segment (not a symptom), and
  (c) verified the parts reconcile to the whole. Don't stop on a symptom.

Return JSON only -- exactly one move:
{
  "action": "author" | "reconcile" | "clarify" | "finish",
  "question": "<one concrete plain-language sub-question>",  // action=author
  "total": <number>, "parts": [<number>, ...],               // action=reconcile
  "clarification": "<plain-language question for the user>", // action=clarify
  "final_answer": "<the complete answer, with numbers>",     // action=finish
  "reasoning": "<one line>"
}

Move guidance:
- author: ask ONE concrete sub-question; the analyst answers it with real SQL (looking up any
  metric/term definitions and probing the data itself) and it is independently checked. Decompose the biggest unexplained piece.
- reconcile: verify a decomposition adds up. Read the numbers straight from the findings and pass them:
    "total" = the overall change (ONE number); "parts" = the list of each driver/segment's contribution (numbers).
  The check -- do the parts sum to the total? -- is done deterministically for you. Only reconcile once you
  actually have the total and every part as numbers in the findings; don't reconcile placeholders.
  A non-zero residual is a signal, not a dead end: it means a driver/segment is MISSING -- author a
  sub-question to find it (e.g. an uncohorted bucket, churn, nulls), then finish honestly.
- clarify: only if two business meanings would give different answers and nothing resolves it. Plain language, no schema words.
- finish: only when genuinely answered and (for RCA) verified or the residual is explained. If a wrinkle couldn't be fully explained, finish honestly stating it -- do not loop forever.

If a sub-question in the findings came back UNANSWERED, do NOT re-ask the same thing -- either
rephrase it more simply / break it into a smaller piece, try a DIFFERENT decomposition, or
finish with what you have and say that part could not be computed. Never repeat an identical
sub-question that already failed.

DECOMPOSING A METRIC (when the goal is "why did metric X move"):
- A metric with `depends_on` breaks into its drivers. MULTIPLICATIVE (e.g. revenue = buyers x revenue_per_buyer):
  compute each driver's change and see which moved most. ADDITIVE (e.g. buyers = new + returning): the parts sum to the whole.
- Go MULTIPLE LEVELS: once you find the biggest driver, decompose IT via its own depends_on, until you reach a leaf or a clear cause.
- You may also decompose ACROSS a dimension (category, state, income_band, ...) to find where the change concentrated.

Be decisive; do not re-ask what a finding already shows."""

_FRAME_PROMPT = """You FRAME THE INTENT of a business data question BEFORE any SQL is written -- so the query
answers what was actually meant. The user is non-technical and does NOT know the tables or columns.
You are given the question, the DEFINED business terms/metrics available, and a one-line description
of each table (NOT columns -- this is about MEANING, not schema).

Do two things:
1. MAP every business concept in the question to a precise meaning and BIND it -- to a DEFINED term
   when one exists (name it), or to a clear derivation in business words. Cover cohorts/behaviours
   ("first-ever online purchase", "active buyer", "churn"), measures ("spend", "revenue"), filters,
   and time windows. Be explicit about SUBTLE distinctions that change the number: online-only vs
   any-channel; the amount customers PAID vs the gross/list price; a "current" attribute vs the one
   "at the time of purchase". Prefer a defined term over a look-alike.
2. Ask a clarifying question ONLY if a concept has TWO reasonable business meanings that would give
   MATERIALLY DIFFERENT numbers and nothing in the question settles which. It MUST be answerable by a
   non-technical person -- about MEANING, never tables/columns/SQL/joins/grain. If the intent is
   clear, do NOT ask -- bind it and proceed. Asking blocks the user, so ask sparingly.

Return JSON only:
{
  "intent": "<one-line plain restatement of what is being asked>",
  "concepts": [{"phrase": "<words from the question>", "meaning": "<precise business meaning>", "binds_to": "<defined term or derivation>"}, ...],
  "clarification": "<one plain-language question about MEANING, or empty if the intent is clear>"
}"""

_SYNTHESIZE_PROMPT = """You are the ORCHESTRATOR writing the final answer. Using ONLY the findings, answer the goal
in plain language WITH the numbers. For a root-cause question, state the change, the
driver/segment that explains most of it, and the attribution. If the findings are incomplete
or did not reconcile, say so plainly rather than overclaiming."""


@dataclass
class Investigation:
    goal: str
    answer: str
    findings: list = field(default_factory=list)
    steps: int = 0
    converged: bool = False
    needs_clarification: str = ""
    tokens: int = 0


class Investigator:
    def __init__(self, *, agent: Any, model: Any, sink: Sink = null_sink, max_steps: int = 8) -> None:
        self.agent = agent
        self.workspace = getattr(agent, "workspace", None)
        self.model = model
        self.sink = sink
        self.max_steps = max(1, max_steps)

    def investigate(self, goal: str, clarifications: list | None = None) -> Investigation:
        findings: list[dict[str, Any]] = []
        tokens = 0
        reconciled: set[str] = set()  # distinct reconciles done; a REPEAT is spin, distinct ones are fine
        unknown_reconciles = 0
        failed_qs: dict[str, int] = {}  # sub-questions that came back unanswered -> stop re-asking

        # INTENT FRAMING (before any SQL): map every business concept to a precise binding, using any
        # answer the user already gave to a clarifying question, and ask ONE more only if a concept is
        # still genuinely ambiguous. The framed meaning + the user's clarification become the
        # CONFIRMED INTENT -- authoritative, handed to every sub-question's analyst AND its steward.
        frame, ftok = self._frame_intent(goal, clarifications)
        tokens += ftok
        clar = str(frame.get("clarification") or "").strip()
        if clar:
            self.sink("orchestrator", "info", f"needs clarification before SQL: {clar}")
            return Investigation(goal, "", findings, steps=1, needs_clarification=clar, tokens=tokens)
        confirmed = _confirmed_intent(frame, clarifications)
        if confirmed:
            self.sink("orchestrator", "info", f"intent framed: {frame.get('intent', '')}")

        for step in range(self.max_steps):
            decision, dtok = self._decide(goal, findings)
            tokens += dtok
            action = str(decision.get("action") or "finish")
            self.sink("orchestrator", "info", f"move: {action} -- {decision.get('reasoning', '')}")

            if action == "finish":
                answer = str(decision.get("final_answer") or "").strip() or _fallback_answer(findings)
                return Investigation(goal, answer, findings, steps=step + 1, converged=True, tokens=tokens)

            if action == "clarify":
                return Investigation(goal, "", findings, steps=step + 1,
                                     needs_clarification=str(decision.get("clarification") or "Could you clarify?"),
                                     tokens=tokens)

            if action == "reconcile":
                rec = self._reconcile(findings, decision.get("total_finding"), decision.get("parts_finding"),
                                      total_value=decision.get("total"), part_values=decision.get("parts"))
                sig = rec.get("detail", "")
                # Anti-spin: a REPEAT of a reconcile already done, or a 2nd un-extractable one, is
                # spinning -- stop. But DISTINCT successful reconciles (e.g. a multiplicative total
                # then an additive sub-split) are legitimate and must not trip the guard.
                if rec.get("verdict") == "unknown":
                    unknown_reconciles += 1
                    if unknown_reconciles >= 2:
                        break
                elif sig in reconciled:
                    break
                reconciled.add(sig)
                self.sink("orchestrator", "info", f"reconciliation: {rec['verdict']} ({rec['detail']})")
                findings.append({"kind": "reconcile", **rec})
                continue

            # action == author
            sub_q = str(decision.get("question") or "").strip()
            if not sub_q:
                break
            # Anti-spin: if this sub-question already failed twice, stop re-asking the same thing
            # (the analyst can't answer it) -- synthesize what we have instead of burning the budget.
            key = _norm_q(sub_q)
            if failed_qs.get(key, 0) >= 2:
                self.sink("orchestrator", "info", f"giving up on repeatedly-failing sub-question: {sub_q}")
                break
            self.sink("orchestrator", "info", f"authoring: {sub_q}")
            ans = self.agent.answer(sub_q, confirmed_intent=confirmed)
            tokens += ans.tokens
            if ans.route == "clarify":
                return Investigation(goal, "", findings, steps=step + 1,
                                     needs_clarification=ans.clarify, tokens=tokens)
            if not ans.answered:
                failed_qs[key] = failed_qs.get(key, 0) + 1
            findings.append(_finding(sub_q, ans))

        answer, stok = self._synthesize(goal, findings)
        return Investigation(goal, answer, findings, steps=self.max_steps, converged=False, tokens=tokens + stok)

    # ---- intent framing (before any SQL): bind meanings, ask if genuinely ambiguous --
    def _frame_intent(self, goal: str, clarifications: list | None = None) -> tuple[dict, int]:
        from langchain_core.messages import HumanMessage, SystemMessage

        defined = self.workspace.definitions_index() if self.workspace else ""
        tables = "\n".join(f"- {n}: {d}" for n, d in (self.workspace.tables() if self.workspace else []))
        payload = {"question": goal, "defined_terms": defined or "(none configured)", "tables": tables}
        if clarifications:
            payload["user_already_clarified"] = [{"asked": q, "answered": a} for q, a in clarifications]
        out = stream_and_collect(
            model=self.model, stage="framing", sink=self.sink,
            messages=[SystemMessage(content=_FRAME_PROMPT), HumanMessage(content=json.dumps(payload, default=str))],
        )
        return _loads_json(out["text"]), out["tokens"]

    # ---- the thin orchestrator brain (no tools, no context dump) -----------------------
    def _decide(self, goal: str, findings: list) -> tuple[dict, int]:
        from langchain_core.messages import HumanMessage, SystemMessage

        index = self.workspace.definitions_index() if self.workspace else ""
        payload = {
            "goal": goal,
            "defined_terms_and_metrics_available": index or "(none configured)",
            "findings": _compact(findings),
        }
        out = stream_and_collect(
            model=self.model, stage="orchestrator", sink=self.sink,
            messages=[SystemMessage(content=_DECIDE_PROMPT), HumanMessage(content=json.dumps(payload, default=str))],
        )
        return _loads_json(out["text"]), out["tokens"]

    def _synthesize(self, goal: str, findings: list) -> tuple[str, int]:
        from langchain_core.messages import HumanMessage, SystemMessage

        out = stream_and_collect(
            model=self.model, stage="orchestrator", sink=self.sink,
            messages=[SystemMessage(content=_SYNTHESIZE_PROMPT),
                      HumanMessage(content=json.dumps({"goal": goal, "findings": _compact(findings)}, default=str))],
        )
        return out["text"].strip() or _fallback_answer(findings), out["tokens"]

    # ---- reconcile skill (deterministic -- the "test") --------------------------------
    def _reconcile(self, findings: list, total_idx: Any, parts_idx: Any,
                   total_value: Any = None, part_values: Any = None) -> dict:
        """The deterministic test: do the parts sum to the whole? The orchestrator reads the
        total and the parts straight out of the findings and passes them as numbers (robust to
        whatever shape the analyst returned); we only do the arithmetic. Falls back to extracting
        them positionally from finding rows when explicit numbers aren't supplied."""
        total = _num(total_value) if total_value is not None else _scalar_of(_finding_at(findings, total_idx))
        if part_values is not None:
            nums = [_num(v) for v in (part_values if isinstance(part_values, list) else [part_values])]
            nums = [n for n in nums if n is not None]
            parts = sum(nums) if nums else None
        else:
            parts = _numeric_sum(_finding_at(findings, parts_idx))
        if total is None or parts is None:
            return {"verdict": "unknown", "detail": "could not read a total and the parts as numbers"}
        residual = total - parts
        ok = abs(residual) <= max(1e-6, abs(total) * 1e-4)
        return {"verdict": "reconciles" if ok else "does NOT reconcile",
                "detail": f"total={round(total, 2)}, parts_sum={round(parts, 2)}, residual={round(residual, 2)}",
                "reconciles": ok, "residual": round(residual, 4)}


# ---- helpers --------------------------------------------------------------------------
def _finding(question: str, ans: Any) -> dict[str, Any]:
    rows = (ans.result or {}).get("rows") if getattr(ans, "result", None) else None
    return {"kind": "finding", "question": question, "route": ans.route,
            "answered": ans.answered, "rows": rows, "sql": ans.final_sql,
            "trust": getattr(ans, "trust", "")}


def _compact(findings: list) -> list:
    out = []
    for i, f in enumerate(findings):
        if f.get("kind") == "reconcile":
            out.append({"index": i, "kind": "reconcile", "verdict": f.get("verdict"), "detail": f.get("detail")})
        else:
            rows = f.get("rows")
            out.append({"index": i, "question": f.get("question"), "answered": f.get("answered"),
                        "rows": rows if (rows and len(rows) <= 25) else (f"{len(rows)} rows" if rows else None),
                        "sql": " ".join((f.get("sql") or "").split())[:300], "trust": f.get("trust") or None})
    return out


def _norm_q(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


def _render_bindings(frame: dict) -> str:
    lines = []
    for c in frame.get("concepts") or []:
        if not isinstance(c, dict):
            continue
        phrase, meaning = str(c.get("phrase") or "").strip(), str(c.get("meaning") or "").strip()
        binds = str(c.get("binds_to") or "").strip()
        if not phrase and not meaning:
            continue
        tail = f"  [{binds}]" if binds else ""
        lines.append(f'- "{phrase}" = {meaning}{tail}')
    return "\n".join(lines)


def _confirmed_intent(frame: dict, clarifications: list | None = None) -> str:
    """The authoritative meaning to build and verify against: the framed intent + concept bindings,
    plus VERBATIM any clarifying Q&A the user answered (the strongest signal in the whole turn)."""
    parts = []
    intent = str((frame or {}).get("intent") or "").strip()
    if intent:
        parts.append(f"Intent: {intent}")
    bindings = _render_bindings(frame or {})
    if bindings:
        parts.append("Meanings:\n" + bindings)
    for q, a in (clarifications or []):
        parts.append(f'The user was asked: "{q}"  and answered: "{a}"  (authoritative).')
    return "\n".join(parts)


def _finding_at(findings: list, idx: Any) -> dict | None:
    try:
        return findings[int(idx)]
    except (TypeError, ValueError, IndexError):
        return None


def _scalar_of(finding: dict | None) -> float | None:
    """The single number a finding stands for. A 1-row finding -> its last cell. A 2-row
    before/after series -> the CHANGE (later minus earlier) -- the canonical shape an author
    returns for "the total change in X", so the reconcile test can fire without re-authoring."""
    rows = (finding or {}).get("rows") or []
    if len(rows) == 1 and rows[0]:
        return _num(rows[0][-1])
    if len(rows) == 2 and rows[0] and rows[1]:
        earlier, later = _num(rows[0][-1]), _num(rows[1][-1])
        if earlier is not None and later is not None:
            return later - earlier
    return None


def _numeric_sum(finding: dict | None) -> float | None:
    rows = (finding or {}).get("rows") or []
    if not rows:
        return None
    total, found = 0.0, False
    for row in rows:
        v = _num(row[-1]) if row else None
        if v is not None:
            total += v
            found = True
    return total if found else None


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _fallback_answer(findings: list) -> str:
    for f in reversed(findings):
        if f.get("kind") == "finding" and f.get("rows"):
            return f"{f['question']} -> {f['rows']}"
    return "No answer could be established."


def _loads_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        s, e = raw.find("{"), raw.rfind("}")
        if s < 0 or e < s:
            return {}
        try:
            value = json.loads(raw[s : e + 1])
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}
