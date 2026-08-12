"""Main ReAct loop — dispatches LLM tool calls until the agent calls a terminator.

Terminators: any control tool the caller registered under `sentinels` returns True.

The loop is bounded by BudgetTracker (tokens, steps, wall-clock). No judgement
lives here — the LLM decides which tools to call and when to finish.
"""
from __future__ import annotations
import json
import time
from typing import Any, Callable, Dict, List, Optional, Set
from .llm import chat_with_tools
from .tool_registry import Registry
from .middleware.budgets import BudgetTracker, BudgetExceeded
from .middleware.audit import AuditSink
from .middleware.checkpoint import Checkpointer


def run_react(
    *,
    client, model: str,
    system_prompt: str,
    user_prompt: str,
    registry: Registry,
    budget: BudgetTracker,
    audit: AuditSink,
    checkpoint: Optional[Checkpointer] = None,
    sentinels: Optional[Set[str]] = None,
    phase: str = "main",
    max_iters: int = 100,
    log_tool_output_cap: int = 800,
) -> Dict[str, Any]:
    """Run a ReAct loop until either:
      - the LLM calls a sentinel tool (in `sentinels`),
      - the LLM responds with no tool_calls (implicit finish),
      - the budget is exceeded,
      - max_iters reached.

    Returns {status, iters, terminator, final_text, sentinel_payload?}.
    sentinel_payload is populated when a sentinel tool is called — it contains the args.
    """
    sentinels = sentinels or set()
    messages: List[Dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]
    tools = registry.schemas()
    audit.emit(event="phase.start", phase=phase, model=model, n_tools=len(tools))

    for i in range(max_iters):
        try:
            budget.tick_step()
        except BudgetExceeded as ex:
            audit.emit(event="budget.exceeded", reason=str(ex), phase=phase)
            return {"status": "budget_exceeded", "iters": i, "terminator": "budget", "final_text": str(ex)}

        try:
            msg, usage = chat_with_tools(client, model, messages, tools)
            budget.add_tokens(usage.get("total_tokens", 0))
        except BudgetExceeded as ex:
            audit.emit(event="budget.exceeded", reason=str(ex), phase=phase)
            return {"status": "budget_exceeded", "iters": i, "terminator": "budget", "final_text": str(ex)}
        except Exception as ex:
            audit.emit(event="llm.error", error=str(ex), phase=phase)
            return {"status": "llm_error", "iters": i, "terminator": "error", "final_text": str(ex)}

        # No tool calls → send ONE reminder to the model (agents often stop too early
        # after validation but before commit). If the model still doesn't call a tool,
        # accept implicit finish.
        if not msg.tool_calls:
            content = msg.content or ""
            # Track how many implicit-finish reminders we've already sent
            reminders_sent = sum(1 for m in messages
                                 if m.get("role") == "user"
                                 and "REMINDER: you have not called any tool" in (m.get("content") or ""))
            if reminders_sent == 0 and phase == "main":
                # First implicit finish in main phase — nudge and retry
                audit.emit(event="phase.reminder_sent", phase=phase, prior_text_len=len(content))
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content":
                    "REMINDER: you have not called any tool. Every response in the MAIN phase "
                    "MUST include a tool call. If you have validated a candidate SQL and the "
                    "saving is material, call `write_proposal(payload)` now. If you are done, "
                    "call `finish(reason)`. If you need more evidence, call another observation tool. "
                    "Plain-text responses end the round with nothing committed — do not do that."
                })
                continue
            audit.emit(event="phase.implicit_finish", phase=phase,
                       final_text_len=len(content), reminders_sent=reminders_sent)
            return {"status": "ok", "iters": i, "terminator": "implicit",
                    "final_text": content}

        # Assistant message with tool calls — accumulate
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })

        sentinel_hit = None
        sentinel_args = None
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            audit.emit(event="tool.call", phase=phase, step=i, name=name, args_size=len(tc.function.arguments or ""))
            t0 = time.perf_counter()
            result = registry.dispatch(name, args)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            result_json = _safe_json(result)
            audit.emit(event="tool.result", phase=phase, step=i, name=name,
                       elapsed_ms=elapsed_ms,
                       result_head=result_json[:log_tool_output_cap],
                       result_size=len(result_json))
            # Cap tool result payload — some tools (list_lineage) return 50KB+ and
            # accumulating them explodes context. The head is usually enough for the model.
            cap = getattr(budget, "_tool_cap", None)
            if cap is None:
                cap = 4000
            truncated = result_json[:cap]
            if len(result_json) > cap:
                truncated += f"\n\n... [{len(result_json) - cap} more bytes truncated — call again with narrower args if needed]"
            messages.append({
                "role": "tool", "tool_call_id": tc.id,
                "content": truncated,
            })
            if name in sentinels:
                sentinel_hit = name
                sentinel_args = args

        if checkpoint:
            checkpoint.maybe_save(phase, messages, extra={"budget": budget.snapshot()})

        if sentinel_hit:
            audit.emit(event="phase.sentinel", phase=phase, sentinel=sentinel_hit)
            return {"status": "ok", "iters": i + 1, "terminator": sentinel_hit,
                    "final_text": msg.content or "", "sentinel_payload": sentinel_args}

    audit.emit(event="phase.max_iters", phase=phase, max_iters=max_iters)
    return {"status": "max_iters", "iters": max_iters, "terminator": "max_iters",
            "final_text": "loop hit max_iters"}


def _safe_json(v: Any) -> str:
    try:
        return json.dumps(v, default=str)
    except Exception:
        return json.dumps({"error": "not JSON-serialisable", "repr": str(v)[:400]})
