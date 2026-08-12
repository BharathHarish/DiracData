"""ledger — compact indexing over proposals + decisions + deferrals.

Every function returns plain data. Nothing here decides — the agent reads
the index, correlates against what it's about to draft, and decides itself
whether to skip, supersede, defer, or proceed. No thresholds anywhere.

The point of this module: keep list_prior_proposals from returning 20KB
of JSON blobs the agent has to plough through. Give it a compact view
first; the agent drills into full detail with read_proposal(id) when needed.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import ModellerConfig


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> Optional[datetime]:
    if not s: return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _days_ago(ts: str) -> Optional[float]:
    d = _parse_iso(ts)
    if not d: return None
    return round((_now() - d).total_seconds() / 86400.0, 2)


def _grain_key(grain: Any) -> str:
    """Normalise grain to a stable string key so 2 proposals with same grain match."""
    if isinstance(grain, list):
        return ",".join(str(g) for g in grain)
    if isinstance(grain, str):
        return grain
    return json.dumps(grain, sort_keys=True, default=str)


def proposal_index(cfg: ModellerConfig, s3) -> List[Dict[str, Any]]:
    """Compact view of every prior proposal.

    Returns one row per proposal with only the fields the agent needs to
    reason about dedup + supersession — NOT the full SQL/evidence blob.
    Sorted newest-first.
    """
    from .read_tools import list_prior_proposals
    props = list_prior_proposals(cfg, s3)
    out = []
    for p in props:
        out.append({
            "proposal_id":    p.get("proposal_id"),
            "target_name":    p.get("target_name"),
            "grain_key":      _grain_key(p.get("grain")),
            "engine":         p.get("engine"),
            "status":         p.get("status"),
            "confidence":     p.get("confidence"),
            "created_at":     p.get("created_at"),
            "days_ago":       _days_ago(p.get("created_at", "")),
            "n_decisions":    len(p.get("decisions") or []),
            "matched_templates": (p.get("evidence") or {}).get("matched_query_templates", []),
            "projected_saving_ms": (p.get("evidence") or {}).get("projected_daily_saving_ms"),
        })
    return sorted(out, key=lambda x: x.get("created_at") or "", reverse=True)


def recent_decisions(cfg: ModellerConfig, s3, since_days: Optional[float] = None) -> List[Dict[str, Any]]:
    """List human decisions on prior proposals (approved / rejected / superseded / withdrawn).

    since_days optional filter — the agent decides what "recent" means.
    Each decision includes the proposal_id, target_name, decision, reason, timestamp,
    and days_ago so the agent can weight relevance.
    """
    from .read_tools import list_prior_proposals
    props = list_prior_proposals(cfg, s3)
    out = []
    for p in props:
        for d in (p.get("decisions") or []):
            days = _days_ago(d.get("at", ""))
            if since_days is not None and days is not None and days > since_days:
                continue
            out.append({
                "proposal_id":  p.get("proposal_id"),
                "target_name":  p.get("target_name"),
                "grain_key":    _grain_key(p.get("grain")),
                "decision":     d.get("decision"),
                "reason":       d.get("reason", ""),
                "decided_at":   d.get("at"),
                "days_ago":     days,
                "matched_templates": (p.get("evidence") or {}).get("matched_query_templates", []),
            })
    return sorted(out, key=lambda x: x.get("decided_at") or "", reverse=True)


def deferral_index(cfg: ModellerConfig, s3) -> List[Dict[str, Any]]:
    """Compact view of the deferral ledger with expiry status.

    Each entry gets a `days_ago` field + an `is_reconsider_due` bool derived from
    the timestamps. The agent reads these and decides whether to revisit — the
    boolean is a fact, not a judgement.
    """
    from . import write_tools as W
    defs = W.list_deferrals(cfg, s3)
    out = []
    now = _now()
    for pattern_id, d in defs.items():
        deferred_days = _days_ago(d.get("deferred_at", ""))
        reconsider_at = _parse_iso(d.get("reconsider_at") or "")
        is_due = (reconsider_at is not None) and (reconsider_at <= now)
        out.append({
            "pattern_id":         pattern_id,
            "reason":             d.get("reason"),
            "deferred_at":        d.get("deferred_at"),
            "days_ago":           deferred_days,
            "reconsider_at":      d.get("reconsider_at"),
            "is_reconsider_due":  is_due,
        })
    return sorted(out, key=lambda x: x.get("deferred_at") or "", reverse=True)
