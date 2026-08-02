"""The plan / TODO -- the durable verification spine of a v4 investigation.

Each item carries its own EVIDENCE (the SQL + result_id + number that answers it) and a status.
`done` means a number exists; `verified` means the independent verify + deterministic gates passed
for THAT item. The finish gate (Phase 3) refuses to terminate until every item is `verified`; an
ambiguity that can't be resolved from the data becomes a `blocked` item that routes to ask_user.

Phase 1 ships the structure + rendering so WorkingMemory is stable; the plan_update tool and the
finish gate are layered on in Phase 3 without changing this shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field

STATES = ("pending", "in_progress", "done", "verified", "blocked")


@dataclass
class PlanItem:
    id: str
    goal: str
    status: str = "pending"
    evidence: dict = field(default_factory=dict)  # {sql, result_id, number}
    note: str = ""


@dataclass
class Plan:
    items: list[PlanItem] = field(default_factory=list)

    def add(self, goal: str, *, id: str | None = None) -> PlanItem:
        item = PlanItem(id=id or f"t{len(self.items) + 1}", goal=goal)
        self.items.append(item)
        return item

    def get(self, id: str) -> PlanItem | None:
        return next((i for i in self.items if i.id == id), None)

    def update(self, id: str, *, status: str | None = None, evidence: dict | None = None,
               note: str | None = None) -> PlanItem | None:
        item = self.get(id)
        if item is None:
            return None
        if status is not None and status in STATES:
            item.status = status
        if evidence is not None:
            item.evidence = {**item.evidence, **evidence}
        if note is not None:
            item.note = note
        return item

    def all_verified(self) -> bool:
        return bool(self.items) and all(i.status == "verified" for i in self.items)

    def blocked(self) -> list[PlanItem]:
        return [i for i in self.items if i.status == "blocked"]

    def render(self) -> str:
        if not self.items:
            return ""
        lines = []
        for i in self.items:
            ev = ""
            if i.evidence.get("number") is not None:
                ev = f"  (= {i.evidence['number']}"
                if i.evidence.get("result_id"):
                    ev += f", {i.evidence['result_id']}"
                ev += ")"
            note = f" -- {i.note}" if i.note else ""
            lines.append(f"- [{i.status}] {i.id}: {i.goal}{ev}{note}")
        return "\n".join(lines)
