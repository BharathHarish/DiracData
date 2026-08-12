"""AuditSink — log every tool call + LLM call to lake/fintech/modeller/audit/<round_id>.jsonl.

Observation only. Never consulted by any logic. Enables post-hoc analysis of what
the agent did and why.
"""
from __future__ import annotations
import json
import io
from datetime import datetime, timezone
from typing import Any, Dict, List


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditSink:
    """Buffered append-to-S3 audit log. Flushes on demand + on close."""

    def __init__(self, cfg, s3, round_id: str):
        self.cfg = cfg
        self.s3 = s3
        self.round_id = round_id
        self.key = f"{cfg.audit_prefix}{round_id}.jsonl"
        self._buffer: List[str] = []

    def emit(self, **event: Any) -> None:
        line = json.dumps({"ts": _now(), "round_id": self.round_id, **event}, default=str)
        self._buffer.append(line)
        # Auto-flush every ~10 events to bound memory
        if len(self._buffer) >= 10:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        # Read-modify-write is fine at our scale (single-runner enforced)
        try:
            current = self.s3.get_object(Bucket=self.cfg.bucket, Key=self.key)["Body"].read().decode("utf-8")
        except Exception:
            current = ""
        body = current + "\n".join(self._buffer) + "\n"
        self.s3.put_object(Bucket=self.cfg.bucket, Key=self.key,
                           Body=body.encode("utf-8"),
                           ContentType="application/x-ndjson")
        self._buffer = []

    def close(self) -> None:
        self.emit(event="round.closed")
        self.flush()
