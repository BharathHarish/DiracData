# Endurance agent — first-principles design

How the V5 harness stays effective across the FULL range of tasks: a 3-step "how many orders in 2001"
AND a 7-level × 5-dimension RCA that would otherwise exhaust the budget. The mechanisms are **adaptive
and agentic** — the model invokes the heavy machinery only when the task warrants it; a short task never
pays for it.

## The invariant

> **Effective for a long-running task and a short task alike.** Simple query → answer directly, cheapest
> tier, a few steps. Complex query → decompose, delegate, reconcile, without running out of gas. The
> difference is the MODEL's judgment (guided), never a gate that fires on every query.

## The core failure it fixes

Evidence — deep RCA (`online_net_revenue`, 7 levels, 5 dimensions), Haiku:
- Good plan (t1 DQ → t2–t5 driver levels → t6 attribution → t7 slice ×5) but **decorative**: it didn't
  drive delegation. `t7` (5 dimensions) was executed SERIALLY in the parent (22 `run_sql`).
- Sub-agents delegated **trivial leaves** (`SUM(net_paid)`) while the parallelizable dimension work
  stayed local; one sub-agent **429'd on TPM**.
- 24 `data_health` re-probes; 1 `metric_tree` vs 21 `define`.
- Degraded into empty `finish({})` spam → **blank** (separately fixed: best-answer surfacing).

Root cause: **the orchestrator DID the work instead of ORCHESTRATING it, and its context grew unbounded.**

## Principles

1. **Orchestrator orchestrates; workers work.** The main loop plans → delegates → reconciles → finishes.
   It does not run leaf SQL for a complex task.
2. **The plan is the execution contract, not a checklist.** An independent plan item IS a delegation target.
3. **Delegate parallelizable, non-trivial units; keep cheap arithmetic local.** A dimension slice earns a
   sub-agent; a `SUM` does not.
4. **Context flows down concretely, results flow up compressed.** Sub-agents inherit resolved SQL / joins /
   DQ ledger / bindings (don't re-derive); they return compact results, not transcripts.
5. **The orchestrator's context stays bounded.** Finished branches compact to their conclusion.
6. **Budget is allocated, not flat.** Each unit its own budget; escalation is per-unit.
7. **Progress is monotonic.** A `verified` unit can't reopen; no-progress → change strategy or finish-best.
8. **Always hold a current best answer.** Never blank at a boundary.

*Adaptivity is principle 0: every mechanism below is conditional on task shape, so a short task is untouched.*

## Mechanisms (agentic — guide the loop, no hard-coded DAG)

| # | mechanism | fixes | where | short-task cost |
|---|-----------|-------|-------|-----------------|
| M2 | **Fan-out policy in the CORE loop** — delegate a unit iff independent + non-trivial + compact-result; batch all independent units into ONE `spawn_subagents`; never spawn a lone aggregate; never run independent slices serially. Explicitly: simple task → answer directly. | wrong-granularity delegation; serial slices | `analyst_core.md` | none (conditional) |
| M3 | **Context hand-off** — `spawn_subagent` auto-packages the parent's confirmed intent + DQ ledger + resolved facts/bindings into every sub-task ("reuse; do NOT re-probe/re-derive"). | token multiplication; re-derivation | `subagents.py` | none (only fires on fan-out) |
| M5 | **Progress sentinel / freeze** — a `verified` item is frozen; K steps with no new verified item/result → change strategy or finish-best. | churn (t1 verified 8×); empty-finish spam | `working_memory.py` + loop | negligible |
| M1 | **Decompose-first** — for a wide/deep task, plan `parallel` vs `local` items BEFORE any SQL. | decorative planning | `analyst_core.md` | none (skip for simple) |
| M6 | **Per-unit budget + rate-aware fan-out** — 429 backoff on main + sub loops; OpenAI concurrency capped below CPU count. | ran out of gas; TPM 429 | config + loop + registry | none |
| M4 | **Context compaction** — reconciled branch → replace raw rows with the conclusion in working memory. | unbounded context = endurance limit | mid-run summarize | none |

## Build order

1. **M2 + M3** (fan-out policy + context hand-off) — biggest, most generic win; re-run the 7×5 stress test.
2. **M5** (progress sentinel) — kills churn + spam cheaply.
3. **M1** (decompose-first) — plan drives delegation.
4. **M6** (per-unit budget + 429 backoff) — reliability floor for long runs.
5. **M4** (context compaction) — last endurance lever.

## Measurement

Two anchors, run after each lever, and BOTH must stay healthy (the invariant):
- **Short**: "how many online purchases in 2001?" — must stay Nano, ~3 steps, tiny tokens.
- **Long**: the `online_net_revenue` 7×5 RCA — target: converges without exhausting the budget, sub-agents
  do the dimension slices (not leaves), no re-probe storm, reconciles to the total, no blank/caveat.
