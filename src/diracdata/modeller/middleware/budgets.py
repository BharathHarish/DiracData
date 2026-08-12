"""Budget tracker — hard limits for tokens + wall-clock + proposal count.

Kills the loop when exceeded. Not judgement — pure safety.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    pass


@dataclass
class BudgetTracker:
    max_tokens: int
    max_seconds: int
    max_proposals: int
    max_steps: int

    tokens_used:    int = 0
    proposals_written: int = 0
    steps_taken:    int = 0
    started_at:     float = field(default_factory=time.time)

    def elapsed_s(self) -> float:
        return time.time() - self.started_at

    def add_tokens(self, n: int) -> None:
        self.tokens_used += n
        if self.tokens_used > self.max_tokens:
            raise BudgetExceeded(f"token budget: used {self.tokens_used} > cap {self.max_tokens}")

    def tick_step(self) -> None:
        self.steps_taken += 1
        if self.steps_taken > self.max_steps:
            raise BudgetExceeded(f"step budget: {self.steps_taken} > cap {self.max_steps}")
        if self.elapsed_s() > self.max_seconds:
            raise BudgetExceeded(f"wall-clock: {self.elapsed_s():.0f}s > cap {self.max_seconds}s")

    def note_proposal_written(self) -> None:
        self.proposals_written += 1
        if self.proposals_written > self.max_proposals:
            raise BudgetExceeded(f"proposal cap: {self.proposals_written} > {self.max_proposals}")

    def snapshot(self) -> dict:
        return {
            "tokens_used": self.tokens_used,
            "steps_taken": self.steps_taken,
            "proposals_written": self.proposals_written,
            "elapsed_s": round(self.elapsed_s(), 1),
        }
