"""Learned experiences -- what the agent remembers from doing.

The policy that makes this useful (and that v2 got wrong by writing every run):

  Remember an experience only if you'd want it to be a free, deterministic replay next
  time. That means: it was VERIFIED (executed + Steward-passed), it is NOVEL (no gold pair
  or existing experience already covers it), and it came from the BRAIN (a deterministic
  route already had it, so there is nothing new to learn). Never remember a wrong answer --
  that would poison retrieval.

Remembered experiences join the same structure-indexed example bank as gold pairs, so
they are retrieved identically. They enter as PROVISIONAL (usable as precedent for the
Brain via find_examples / pattern_assisted) and are NOT yet used by the deterministic
replay routes -- an agent-generated answer verified once has not earned blind replay.
Graduation to a trusted tier (human approval / re-confirmation) is a later step.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _normalize(question: str) -> str:
    return " ".join((question or "").lower().split()).rstrip(".?! ")


class JoinStore:
    """Append-only store of join edges the agent discovered and verified.

    The base join graph is derived from the naming convention; anything the agent has to
    reason out (a non-convention join, a bridge) is recorded here so the graph learns and
    the next question finds the path for free. Deduped by the workspace on load."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _keys(self) -> set[str]:
        return {" = ".join(sorted((f"{r['left_table']}.{r['left_col']}", f"{r['right_table']}.{r['right_col']}")))
                for r in self.load()}

    def append(self, *, left: str, right: str) -> bool:
        lt, lc = left.split(".", 1)
        rt, rc = right.split(".", 1)
        key = " = ".join(sorted((left, right)))
        if key in self._keys():
            return False
        rec = {"left_table": lt, "left_col": lc, "right_table": rt, "right_col": rc,
               "created_at": datetime.now(UTC).isoformat()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(rec) + "\n")
        return True


class ExperienceStore:
    """A small append-only JSONL store -- local, inspectable, lossless."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def questions(self) -> set[str]:
        return {_normalize(str(rec.get("question") or "")) for rec in self.load()}

    def has_question(self, question: str) -> bool:
        return _normalize(question) in self.questions()

    def append(self, *, question: str, sql: str, route: str) -> dict[str, Any]:
        record = {
            "question": " ".join((question or "").split()),
            "sql": " ".join((sql or "").split()),
            "route": route,
            "source": "experience",
            "tier": "provisional",
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        return record
