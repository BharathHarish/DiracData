"""Checkpointer — serialise conversation state to MinIO every N steps.

Enables resume-after-crash for long rounds. Writes a single JSON per round_id.
"""
from __future__ import annotations
import json
from typing import Any, Dict, List, Optional


class Checkpointer:
    def __init__(self, cfg, s3, round_id: str, every_steps: int = 5):
        self.cfg = cfg
        self.s3 = s3
        self.round_id = round_id
        self.every = every_steps
        self.key = f"{cfg.checkpoints_prefix}{round_id}.json"
        self._step_count = 0

    def maybe_save(self, phase: str, messages: List[Dict], extra: Optional[Dict] = None) -> None:
        self._step_count += 1
        if self._step_count % self.every != 0:
            return
        self.save(phase, messages, extra)

    def save(self, phase: str, messages: List[Dict], extra: Optional[Dict] = None) -> None:
        blob = {
            "round_id": self.round_id,
            "phase":    phase,
            "step":     self._step_count,
            "messages": _serialise_messages(messages),
            "extra":    extra or {},
        }
        self.s3.put_object(
            Bucket=self.cfg.bucket, Key=self.key,
            Body=json.dumps(blob, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )

    def load(self) -> Optional[Dict]:
        try:
            return json.loads(self.s3.get_object(Bucket=self.cfg.bucket, Key=self.key)["Body"].read())
        except Exception:
            return None


def _serialise_messages(messages: List[Dict]) -> List[Dict]:
    """Ensure everything is JSON-serialisable (dict-only)."""
    out = []
    for m in messages:
        if isinstance(m, dict):
            out.append(m)
        else:
            # openai message objects — convert
            out.append({
                "role":       getattr(m, "role", "unknown"),
                "content":    getattr(m, "content", ""),
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in (getattr(m, "tool_calls", None) or [])
                ] if getattr(m, "tool_calls", None) else None,
            })
    return out
