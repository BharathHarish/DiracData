"""ModellerAgent — orchestrates a single round.

Round shape:
  1. FRAMING       — small LLM loop: form a hypothesis about what to focus on this round
  2. MAIN          — main ReAct loop with all ~28 tools, agent proposes + defers
  3. CURATOR       — small LLM loop: write experiences from what happened

Each phase gets its own bounded budget + system prompt. The audit sink + checkpointer
span the whole round. Middleware injects prior experiences into the framing + main prompts.
"""
from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config      import ModellerConfig, load_config
from .connections import make_s3, make_duckdb
from .llm         import make_llm, resolve_model
from .tool_registry import build_registry
from .loop        import run_react
from .middleware.budgets  import BudgetTracker, BudgetExceeded
from .middleware.audit    import AuditSink
from .middleware.retrieval import inject_experiences
from .middleware.checkpoint import Checkpointer
from . import write_tools as W


_PROMPTS = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS / name).read_text()


def _new_round_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"round_{ts}_{uuid.uuid4().hex[:6]}"


def run_round(cfg: Optional[ModellerConfig] = None, *, resume: bool = False,
              round_id: Optional[str] = None) -> Dict[str, Any]:
    """One round of the modeller. Returns a summary dict."""
    cfg = cfg or load_config()
    s3  = make_s3(cfg)
    con = make_duckdb(cfg)
    client = make_llm(cfg)
    model = resolve_model(cfg.chat_model_profile)

    round_id = round_id or _new_round_id()
    audit = AuditSink(cfg, s3, round_id)
    checkpoint = Checkpointer(cfg, s3, round_id, every_steps=5)
    audit.emit(event="round.start", model=model, model_profile=cfg.chat_model_profile,
               resume=resume)

    # Load experiences for retrieval middleware
    experiences_md = W.read_experiences(cfg, s3)
    experiences_block = inject_experiences(experiences_md)

    summary = {
        "round_id":       round_id,
        "started_at":     datetime.now(timezone.utc).isoformat(),
        "framing":        None,
        "main":           None,
        "curator":        None,
        "proposals":      [],
        "deferrals":      [],
        "experiences_written": 0,
        "status":         "in_progress",
    }

    # ---------- 1) FRAMING ----------
    try:
        hypothesis = _phase_framing(cfg, s3, con, client, model, audit, checkpoint,
                                    experiences_block, round_id)
        summary["framing"] = hypothesis
    except BudgetExceeded as ex:
        summary["status"] = "budget_exceeded_in_framing"
        summary["error"] = str(ex)
        audit.close(); return summary

    # ---------- 2) MAIN ReAct loop ----------
    try:
        main_result, main_budget = _phase_main(
            cfg, s3, con, client, model, audit, checkpoint,
            experiences_block, hypothesis, round_id)
        summary["main"] = main_result
        summary["proposals"] = _collect_new_proposals(cfg, s3, round_id)
    except BudgetExceeded as ex:
        summary["status"] = "budget_exceeded_in_main"
        summary["error"] = str(ex)
        summary["proposals"] = _collect_new_proposals(cfg, s3, round_id)
        audit.close(); return summary

    # ---------- 3) CURATOR ----------
    try:
        curator_result = _phase_curator(cfg, s3, con, client, model, audit, checkpoint,
                                         experiences_block, summary["proposals"], round_id)
        summary["curator"] = curator_result
    except BudgetExceeded as ex:
        summary["curator"] = {"status": "budget_exceeded", "error": str(ex)}

    summary["status"] = "ok"
    summary["ended_at"] = datetime.now(timezone.utc).isoformat()
    audit.emit(event="round.end", summary_len=len(json.dumps(summary, default=str)))
    audit.close()
    return summary


# ==================== phases ====================

def _phase_framing(cfg, s3, con, client, model, audit, checkpoint,
                    experiences_block: str, round_id: str) -> Dict:
    budget = BudgetTracker(
        max_tokens=cfg.max_run_tokens // 5,        # framing ~20%
        max_seconds=cfg.max_run_seconds // 4,
        max_proposals=cfg.max_proposals_per_run,
        max_steps=cfg.max_react_steps // 3,
    )
    budget._tool_cap = cfg.tool_result_cap_chars  # framing
    hypothesis_box: Dict[str, Any] = {}

    def finish_framing(hypothesis: Dict) -> Dict:
        hypothesis_box["hypothesis"] = hypothesis
        return {"status": "captured"}

    reg = build_registry(cfg, s3, con, control_tools={"finish_framing": finish_framing})

    sys_prompt = _load_prompt("system.md") + experiences_block
    framing_prompt = _load_prompt("framing.md")
    user_prompt = (
        f"[round_id={round_id}]\n\n"
        f"You are in the FRAMING phase. Follow the framing instructions below, "
        f"then call `finish_framing(hypothesis)` when done.\n\n"
        f"---\n\n{framing_prompt}"
    )

    result = run_react(
        client=client, model=model,
        system_prompt=sys_prompt, user_prompt=user_prompt,
        registry=reg, budget=budget, audit=audit, checkpoint=checkpoint,
        sentinels={"finish_framing"},
        phase="framing",
        max_iters=budget.max_steps,
    )

    hypothesis = hypothesis_box.get("hypothesis") or {
        "focus_patterns": [], "round_intent": "no hypothesis captured", "engine_focus": "duckdb"
    }
    return {"result": result, "hypothesis": hypothesis, "budget": budget.snapshot()}


def _phase_main(cfg, s3, con, client, model, audit, checkpoint,
                 experiences_block: str, framing: Dict, round_id: str) -> tuple:
    budget = BudgetTracker(
        max_tokens=int(cfg.max_run_tokens * 0.6),   # main ~60%
        max_seconds=cfg.max_run_seconds // 2,
        max_proposals=cfg.max_proposals_per_run,
        max_steps=cfg.max_react_steps,
    )
    budget._tool_cap = cfg.tool_result_cap_chars    # main
    finish_box: Dict[str, Any] = {}

    def finish(reason: str) -> Dict:
        finish_box["reason"] = reason
        return {"status": "captured", "reason": reason}

    def ask_user(question: str) -> Dict:
        # For MVP we just record — no actual human loop yet. Human-in-loop CLI is Phase 7E.
        return {"status": "queued", "question": question,
                "note": "Human review not yet wired — recorded in audit only."}

    reg = build_registry(cfg, s3, con,
                        control_tools={"finish": finish, "ask_user": ask_user})

    hypo = framing.get("hypothesis") or {}
    sys_prompt = _load_prompt("system.md") + experiences_block
    user_prompt = (
        f"[round_id={round_id}]\n\n"
        f"You are in the MAIN phase.\n\n"
        f"Framing hypothesis from the previous phase:\n{json.dumps(hypo, indent=2)}\n\n"
        f"Follow it as guidance, not as constraint. Investigate the focus_patterns, but if you "
        f"observe something more important, you may pivot.\n\n"
        f"**HOW THIS PHASE ENDS**: There are exactly three ways to end this phase:\n"
        f"  1. Call `write_proposal(payload)` for each pattern worth committing (max "
        f"{cfg.max_proposals_per_run}), THEN call `finish(reason)`.\n"
        f"  2. Call `defer(pattern_id, reason)` for patterns you decided not to propose, "
        f"THEN call `finish(reason)`.\n"
        f"  3. Just call `finish(reason)` if you concluded nothing this round warrants a proposal.\n\n"
        f"**Critical**: If you respond with plain text (no tool call), the round ends with "
        f"nothing committed and your work is wasted. Every response MUST include a tool call, "
        f"and you MUST call `finish(reason)` before the phase ends.\n\n"
        f"**Do not stop after only validating SQL**. Validation is a check, not a commit. If "
        f"the SQL is valid AND the projected saving is material AND the grain fits — you "
        f"MUST call `write_proposal(payload)`. Don't be shy; a `pending_review` status means "
        f"a human will look at it, not that it's live in production."
    )

    result = run_react(
        client=client, model=model,
        system_prompt=sys_prompt, user_prompt=user_prompt,
        registry=reg, budget=budget, audit=audit, checkpoint=checkpoint,
        sentinels={"finish"},
        phase="main",
        max_iters=budget.max_steps,
    )
    return {"result": result, "finish_reason": finish_box.get("reason"),
            "budget": budget.snapshot()}, budget


def _phase_curator(cfg, s3, con, client, model, audit, checkpoint,
                    experiences_block: str, this_round_proposals: List[Dict], round_id: str) -> Dict:
    budget = BudgetTracker(
        max_tokens=cfg.max_run_tokens // 5,         # curator ~20%
        max_seconds=cfg.max_run_seconds // 4,
        max_proposals=cfg.max_proposals_per_run,    # not used but tracked
        max_steps=cfg.max_react_steps // 3,
    )
    budget._tool_cap = cfg.tool_result_cap_chars    # curator
    finish_box: Dict[str, Any] = {}

    def finish_curation(reason: str) -> Dict:
        finish_box["reason"] = reason
        return {"status": "captured", "reason": reason}

    reg = build_registry(cfg, s3, con, control_tools={"finish_curation": finish_curation})
    sys_prompt = _load_prompt("system.md") + experiences_block
    curate_prompt = _load_prompt("curate.md")

    round_summary = "\n".join(
        f"- proposal {p.get('proposal_id')}: target={p.get('target_name')}, "
        f"confidence={p.get('confidence')}, status={p.get('status')}"
        for p in this_round_proposals
    ) or "(no proposals were committed this round)"

    user_prompt = (
        f"[round_id={round_id}]\n\n"
        f"You are in the CURATOR phase.\n\nThis round's proposals:\n{round_summary}\n\n"
        f"---\n\n{curate_prompt}"
    )

    result = run_react(
        client=client, model=model,
        system_prompt=sys_prompt, user_prompt=user_prompt,
        registry=reg, budget=budget, audit=audit, checkpoint=checkpoint,
        sentinels={"finish_curation"},
        phase="curator",
        max_iters=budget.max_steps,
    )
    return {"result": result, "finish_reason": finish_box.get("reason"),
            "budget": budget.snapshot()}


# ==================== helpers ====================

def _collect_new_proposals(cfg, s3, round_id: str) -> List[Dict]:
    """Return proposals written during this round (by proposal_id timestamp match)."""
    from . import read_tools as R
    all_props = R.list_prior_proposals(cfg, s3)
    # Round_id looks like round_20260812T144325_abcdef; match proposal_id prefix by timestamp
    round_ts = round_id.split("_", 1)[1].split("_", 1)[0] if "_" in round_id else ""
    if not round_ts:
        return all_props
    return [p for p in all_props if p.get("proposal_id", "").startswith(f"prop_{round_ts[:8]}")]
