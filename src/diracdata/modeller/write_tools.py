"""Write-side tools — commit proposals, experiences, marks, deferrals, audit events.

All mechanical. Take dicts, write to MinIO, return status. Never judge.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from .config import ModellerConfig


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_proposal_id() -> str:
    """ULID-adjacent monotonic id: 20260812T144325_9f3a2b"""
    import uuid
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"prop_{ts}_{uuid.uuid4().hex[:6]}"


def _new_round_id() -> str:
    import uuid
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"round_{ts}_{uuid.uuid4().hex[:6]}"


# ---------- proposals ----------

def write_proposal(cfg: ModellerConfig, s3, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Commit a proposal JSON to lake/fintech/modeller/proposals/<id>.json.

    Fills in proposal_id + created_at if missing. Default status: 'pending_review'.
    Returns {status, proposal_id, key}.
    """
    p = dict(payload)
    p.setdefault("proposal_id", _new_proposal_id())
    p.setdefault("created_at",   _now())
    p.setdefault("status",       "pending_review")
    key = f"{cfg.proposals_prefix}{p['proposal_id']}.json"
    body = json.dumps(p, indent=2, default=str).encode("utf-8")
    s3.put_object(Bucket=cfg.bucket, Key=key, Body=body, ContentType="application/json")
    return {"status": "ok", "proposal_id": p["proposal_id"], "key": key}


def mark_proposal(cfg: ModellerConfig, s3, proposal_id: str,
                  decision: str, reason: str = "") -> Dict[str, Any]:
    """Update a proposal's status. decision ∈ {approved, rejected, superseded, withdrawn}."""
    if decision not in ("approved", "rejected", "superseded", "withdrawn"):
        return {"status": "error", "error": f"invalid decision: {decision}"}
    key = f"{cfg.proposals_prefix}{proposal_id}.json"
    try:
        obj = s3.get_object(Bucket=cfg.bucket, Key=key)
        p = json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as ex:
        return {"status": "error", "error": f"not found: {proposal_id} ({ex})"}
    p["status"] = decision
    p.setdefault("decisions", []).append({
        "at": _now(), "decision": decision, "reason": reason,
    })
    s3.put_object(Bucket=cfg.bucket, Key=key,
                  Body=json.dumps(p, indent=2, default=str).encode("utf-8"),
                  ContentType="application/json")
    return {"status": "ok", "proposal_id": proposal_id, "new_status": decision}


# ---------- deferrals (agent says "not now") ----------

def defer(cfg: ModellerConfig, s3, pattern_id: str, reason: str,
          reconsider_at: Optional[str] = None) -> Dict[str, Any]:
    """Record 'I looked at this pattern and decided not to propose (yet)'.

    Reconsider_at is optional ISO timestamp. Agent decides when to look again;
    this is just a note it made. Read via list_deferrals() during framing.
    """
    key = f"{cfg.state_prefix}deferrals.json"
    try:
        existing = json.loads(s3.get_object(Bucket=cfg.bucket, Key=key)["Body"].read())
    except Exception:
        existing = {}
    existing[pattern_id] = {
        "reason":         reason,
        "deferred_at":    _now(),
        "reconsider_at":  reconsider_at,
    }
    s3.put_object(Bucket=cfg.bucket, Key=key,
                  Body=json.dumps(existing, indent=2, default=str).encode("utf-8"),
                  ContentType="application/json")
    return {"status": "ok", "pattern_id": pattern_id}


def list_deferrals(cfg: ModellerConfig, s3) -> Dict[str, Dict[str, Any]]:
    """Read the deferral ledger. Agent reads this during framing and decides what to do."""
    try:
        return json.loads(s3.get_object(Bucket=cfg.bucket, Key=f"{cfg.state_prefix}deferrals.json")["Body"].read())
    except Exception:
        return {}


# ---------- experiences (long-term learned heuristics) ----------

def write_experience(cfg: ModellerConfig, s3, insight: str, evidence: str = "") -> Dict[str, Any]:
    """Append a learned heuristic to experiences.md. Curator sub-agent uses this."""
    try:
        current = s3.get_object(Bucket=cfg.bucket, Key=cfg.experiences_key)["Body"].read().decode("utf-8")
    except Exception:
        current = "# Modeller Experiences\n\nAgent-authored heuristics accumulated across rounds.\n"
    entry = f"\n\n## {_now()}\n\n- **Insight:** {insight}\n"
    if evidence:
        entry += f"- **Evidence:** {evidence}\n"
    s3.put_object(Bucket=cfg.bucket, Key=cfg.experiences_key,
                  Body=(current + entry).encode("utf-8"),
                  ContentType="text/markdown")
    return {"status": "ok"}


def read_experiences(cfg: ModellerConfig, s3) -> str:
    """Full experiences.md content (markdown)."""
    try:
        return s3.get_object(Bucket=cfg.bucket, Key=cfg.experiences_key)["Body"].read().decode("utf-8")
    except Exception:
        return ""


# ---------- audit log ----------

def write_audit_event(cfg: ModellerConfig, s3, round_id: str, event: Dict[str, Any]) -> None:
    """Append a JSONL event to lake/fintech/modeller/audit/<round_id>.jsonl.

    Read-modify-write is fine at our scale (single-runner enforced by lock).
    Called from middleware — not directly by the agent.
    """
    key = f"{cfg.audit_prefix}{round_id}.jsonl"
    try:
        current = s3.get_object(Bucket=cfg.bucket, Key=key)["Body"].read().decode("utf-8")
    except Exception:
        current = ""
    line = json.dumps({"ts": _now(), **event}, default=str) + "\n"
    s3.put_object(Bucket=cfg.bucket, Key=key,
                  Body=(current + line).encode("utf-8"),
                  ContentType="application/x-ndjson")
