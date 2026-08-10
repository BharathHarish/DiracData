"""WorkingMemory -- the durable spine, re-injected into context every turn.

It is the single source of truth for an investigation: the goal, the confirmed intent (framed
bindings + user clarifications), the plan, the FACTS LEDGER (verified bindings the agent should
never re-derive), and the RESULT INDEX (handles to stored result sets, not their rows). The
narrative summarizer (Phase 4) compresses the conversation; it NEVER touches this object -- so a
binding, a number, or the north-star goal can never be summarized away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from diracdata.config import Config
from diracdata.runtime.plan import Plan

_DEFAULTS = Config()


@dataclass
class WorkingMemory:
    goal: str
    confirmed_intent: dict = field(default_factory=dict)
    plan: Plan = field(default_factory=Plan)
    facts: list[str] = field(default_factory=list)
    results: dict[str, dict] = field(default_factory=dict)   # result_id -> {columns, row_count, sql}
    open_questions: list[str] = field(default_factory=list)
    clarifications: list[tuple[str, str]] = field(default_factory=list)  # (question, user answer)
    seen_numbers: set[float] = field(default_factory=set)  # numeric cells returned by tools (faithfulness)

    def register_numbers(self, rows: Any) -> None:
        """Record the numeric cells a tool returned, so the finish gate can confirm a reported
        figure actually came from a query result (not free-typed)."""
        for row in rows or []:
            cells = row if isinstance(row, (list, tuple)) else [row]
            for cell in cells:
                v = as_number(cell)
                if v is not None:
                    self.seen_numbers.add(v)

    def add_fact(self, fact: str) -> None:
        fact = " ".join((fact or "").split())
        if fact and fact not in self.facts:
            self.facts.append(fact)

    def record_clarification(self, question: str, answer: str) -> None:
        self.clarifications.append((question, answer))

    def note_result(self, envelope: dict) -> None:
        rid = envelope.get("result_id")
        if not rid:
            return
        self.results[rid] = {
            "columns": envelope.get("columns", []),
            "row_count": envelope.get("row_count"),
            # FULL sql -- the verifier needs the complete query; truncating it here made verify
            # reject correct answers as "SQL incomplete". Display is truncated in render() instead.
            "sql": " ".join((envelope.get("sql") or "").split()),
        }

    def render(self) -> str:
        blocks = [f"GOAL: {self.goal}"]
        if self.confirmed_intent:
            intent = self.confirmed_intent.get("intent") or ""
            blocks.append(f"CONFIRMED INTENT: {intent}")
            for c in self.confirmed_intent.get("concepts", []) or []:
                blocks.append(f"  - {c.get('phrase')} -> {c.get('binds_to')} ({c.get('meaning')})")
        if self.facts:
            blocks.append("FACTS ESTABLISHED (do not re-derive):\n"
                          + "\n".join(f"  - {f}" for f in self.facts))
        if self.results:
            lines = [f"  - {rid}: {r['row_count']} rows {r['columns']} from `{r['sql'][:_DEFAULTS.sql_display_cap]}`"
                     for rid, r in self.results.items()]
            blocks.append("RESULTS AVAILABLE (slice with query_result(result_id, sql), FROM `result`):\n"
                          + "\n".join(lines))
        if self.clarifications:
            blocks.append("USER CLARIFICATIONS (authoritative -- bind to these):\n"
                          + "\n".join(f"  - Q: {q}\n    A: {a}" for q, a in self.clarifications))
        plan = self.plan.render()
        if plan:
            blocks.append("PLAN:\n" + plan)
        if self.open_questions:
            blocks.append("OPEN QUESTIONS:\n" + "\n".join(f"  - {q}" for q in self.open_questions))
        return "\n\n".join(blocks)


def as_number(cell: Any) -> float | None:
    """Best-effort numeric parse of a result cell ($, commas tolerated); None if not a number."""
    if isinstance(cell, bool) or cell is None:
        return None
    if isinstance(cell, (int, float)):
        return float(cell)
    try:
        from decimal import Decimal
        if isinstance(cell, Decimal):
            return float(cell)
    except Exception:  # noqa: BLE001
        pass
    try:
        return float(str(cell).strip().replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return None
