# Trust, Orchestration & Metric-RCA — evolving the analyst harness

Status: **design** (no code). Companion to `docs/multi-engine-design.md`. Owner: analyst-harness.

## 0. Thesis, and the honest gap

The *philosophy* is proven: one analyst reasoning across a multi-engine estate, every number gated by
faithfulness + independent verify, on a free model, learning the estate as it goes. The *execution* is
thin in four specific places, and this doc is the plan to make the code match the philosophy:

1. **The outer loop is flat.** `agents/loop.py` is a single ReAct loop; the `Plan`/`PlanItem` in
   `WorkingMemory` is advisory (a `plan_update` tool the model may ignore), not the spine that
   decomposes → dispatches → verifies → assembles. There is no first-class TODO orchestrator.
2. **Sub-agents run one-at-a-time.** `agents/subagents.py::run_subagent` executes a sub to completion,
   sequentially. Nothing fans out parallel exploration, parallel per-source reduction, parallel
   data-sanity, or parallel RCA-driver quantification — the naturally parallel parts of a multi-estate
   question run serially.
3. **Verify checks the SQL, not the data.** The finish gate = faithfulness + one logical verify against
   the golden rules. A correct query over stale/drifted data passes clean. **Layer-1 (data sanity) is
   entirely unverified.**
4. **Metrics/RCA are a flat glossary, not a tree.** `define`/`semantic_layer` is a lookup of business
   terms; there is no metric *decomposition* (driver tree), so RCA is ad-hoc, not a systematic walk.

Everything below strengthens execution around a single spine: **an outer Plan→Execute→Verify loop
(Claude-Code shape), sub-agents as the parallelism substrate, verification baked into the harness
(not tools) across three trust layers, and a metric tree that makes RCA first-class** — while *shrinking*
the tool surface and keeping learning to expectations, not derived structures.

## 1. The three-layer trust model

An answer is only as accurate as (a) the data, (b) the logic, (c) the authoring. Guiding principle,
which also satisfies "minimal tools, clean learning, no over-seeding": **the learning agent produces the
*expectations*; the query harness *asserts against them*** — the learning agent writes the "fixtures,"
the harness runs the "tests" (the coding-agent analogy, made literal).

| Layer | Failure mode | Produced by (learning) | Asserted by (query harness) |
|---|---|---|---|
| **1 · Data sanity** | stale (a pipeline didn't run); NULL%/range/distinct **drift** in the columns the query uses | **baselines-with-tolerance** (profile → expected NULL%, range, distinct, distribution quantiles) + **freshness** (max-ts + expected recency) | *opportunistic footprint check*: is the source fresh? are the key columns inside their learned envelope? |
| **2 · Logical / semantic** | SQL ≠ NL intent: wrong join grain, **LEFT vs INNER**, MECE, condition semantics | concept→(source·table·column) bindings + definitions (framing pins intent up front) | independent verify vs intent; **deep mode: differential** — a twin author writes it a different way, answers must agree |
| **3 · SQL authoring** | NULL handling, **array 1-vs-0 indexing**, engine-specific higher-order fns, complex/nested types | type + nullability + **nested-type shape** (JSON/struct/array fields) + engine **gotchas** (experiences) | result probes (NULL-induced undercount, grain leak, out-of-range); **deep: recompute the key number a second way** |

The one deterministic gate stays **faithfulness** (numbers trace to a stored result). Everything else —
including "is this drift *material to this question*?" — is **agentic judgment grounded in measured
facts**: the harness *measures* drift/freshness deterministically and *feeds* it to the verifier, which
*judges* materiality. Facts measured, materiality judged — same shape as faithfulness.

## 2. Outer loop: a first-class agentic TODO (Claude-Code shape) — NOT a typed DAG

We explicitly reject a typed-DAG orchestrator / deterministic scheduler. The outer loop stays an
**agentic loop like Claude Code**: the agent keeps a running **TODO**, decides the next step itself,
spawns sub-agents when *it* judges parallelism helps, and calls verification/sanity when *it* judges it
needed. There is no graph a deterministic executor walks; the agent's reasoning is the orchestration.

```
FRAME   -> bind concept -> source·table·column; pin intent (layer 2 up front)
loop (the agent drives):
  the agent reads its TODO (rendered into working memory each turn) and decides:
    explore | plan/update TODO | reduce-at-source | reconcile | spawn parallel sub-agents |
    check data health | draft answer | request deeper verification | finish
FINISH  -> faithfulness (deterministic fact-check) + an AGENTIC verifier is invoked
           (it may consider data-sanity evidence, request a differential re-check, etc.)
           -> answer + AUDIT TRAIL (§7, a byproduct of what the agent actually did)
```

- **The TODO is a capability the AGENT maintains** — `WorkingMemory.Plan` promoted from a thin advisory
  note to a real, persisted, status-bearing TODO (pending/in-progress/done) that is rendered back at the
  top of context every turn, so the agent stays coherent across long autonomous runs. It is a *scratchpad
  the agent reasons over*, not a schedule the harness enforces. (This is the Claude-Code `TodoWrite`
  analog, not a workflow engine.)
- **The harness offers, the agent decides.** Parallel sub-agents, drift measurement, deep verification,
  the metric tree — all *capabilities* the agent invokes by judgement, steered by prompts. The harness
  never encodes "if X then verify" — it makes verification easy and cheap to invoke and prompts strongly
  for it.
- **The one guaranteed step is FINISH**, which runs the deterministic faithfulness fact-check and invokes
  the *agentic* verifier. That guarantee already exists (`FinishGate` + `make_verifier`); we deepen what
  the agentic verifier can consider (data-sanity, differential), not add deterministic gates.
- Deep vs normal (§5) is *how hard the agent is prompted/budgeted to verify*, not a count of DAG nodes.

## 3. Sub-agents as the parallelism substrate

Sub-agents are the right tool for every *independent* branch of a multi-estate question — today they run
serially; the evolution is **concurrent fan-out** (bounded by the `diracdata.execution` pool / a thread
pool), each sub isolated-context, all sharing the one `ResultStore` (result_ids already globally unique).
Natural fan-out points:

- **Parallel exploration** — one sub per source maps its schema/fabric while the planner drafts the plan.
- **Parallel data-sanity** — one sub per source (or per key-column group) runs the layer-1 drift/freshness
  checks *concurrently* with the main reduction — "opportunistic" becomes "opportunistic *and* free of
  wall-clock cost."
- **Parallel per-source reduction** — each source's contribution is independent; reduce them at once, then
  reconcile.
- **Parallel differential verify (deep)** — the twin author + multiple verify lenses run side-by-side.
- **Parallel RCA drivers (§6)** — one sub per driver branch of the metric tree.

Fan-out is the **agent's** decision: when it judges branches independent, it spawns several sub-agents at
once; the harness provides the concurrency (bounded pool) and a join, the agent chooses to use it. No
scheduler dispatches nodes. This is the Claude-Code pattern (the agent launches parallel subtasks, then
synthesizes) applied to data. Cost guard: concurrency is bounded and each sub is budgeted, so fan-out
trades wall-clock for a fixed token ceiling, not an unbounded blowup.

## 4. The data-sanity layer (opportunistic, from learned baselines)

The missing Layer-1, built entirely from the profile fabric (no new heavy structure):

- **Scope = the query footprint.** Only the tables/columns the plan actually touched are checked — reuse
  the `profile_column` evidence the analyst already gathered; don't rescan the estate.
- **Checks** (measured, then judged): **freshness** (`MAX(ts)` vs the learned recency expectation → "did a
  pipeline run?"), **NULL% drift**, **range / domain** violation, **distinct-count / distribution** drift
  vs the learned baseline+tolerance.
- **Output = evidence, not a gate.** Anomalies are attached to the verify context ("orders.amount NULL%
  is 12% vs a 0.02% baseline") and the verifier decides materiality to *this* question — and either
  proceeds with a caveat, or blocks in deep mode.
- Runs **concurrently** with reduction via a sanity sub-agent (§3), so normal mode pays little wall-clock.

## 5. Deep vs Normal — the verification-depth dial

Same plan, different verification budget (and it maps to cost/trust + the model router):

- **Normal** — faithfulness + one logical verify + a *light* footprint sanity (freshness + key-column
  NULL%). Fast; the default; pairs with the cheap model.
- **Deep** — adds **differential re-computation** (twin author; answers must agree), a **full drift sweep**
  on every touched column, **adversarial multi-lens semantic verify**, and result cross-checks. Slower,
  board-grade.

Selection is agentic/config: normal for exploration; escalate to deep when the number matters, or *when
normal's sanity/verify flags something*. The chosen depth + every verdict **is** the audit trail (§7).

## 6. Metrics + RCA: the metric tree

Today `define` is a flat glossary. Evolve the semantic layer into a **metric tree** — each metric carries
its **decomposition into driver metrics** (a DAG), so RCA is a systematic *walk*, not improvisation.

```
revenue = Σ payments.amount
  ├─ volume  (paying-customer / order count)
  │     ├─ new-customer volume
  │     └─ returning-customer volume
  └─ AOV     (avg order value)
        ├─ price / unit
        └─ mix (segment / region share)   ← each driver is itself a defined metric
```

- **RCA = walk the tree.** "Why is revenue low / why did it move?" → quantify each child driver's
  contribution → the biggest mover is the proximate cause → recurse into *its* children. Deterministic
  structure, agentic judgment at each node.
- **Sub-agents quantify branches in parallel** (§3): one sub per driver, each returns a verified
  contribution; the parent synthesizes and ranks. This is where sub-agents pay off most — an RCA is
  embarrassingly parallel across drivers.
- **The tree is user-authored** (the metric "curation tax" — you cannot invent a company's decomposition),
  BUT the experiences memory can **suggest candidate decompositions** from verified RCAs and query
  history, and learn **RCA leads** ("when revenue drops, check returning-customer volume first — it moved
  last time"). So it's authored-then-learned, not learned-from-nothing.
- Cross-estate native: a driver's SQL can span sources (volume in Postgres, segment mix in the lake) —
  the same reduce→reconcile pipeline, per driver.

## 7. The audit trail (the product's trust artifact)

Every answer emits a structured trail — the plan DAG, each step's SQL + source + dialect, each result's
grain, the data-sanity findings (freshness + drift), the verify verdicts (per layer + final), the mode
(normal/deep), and the differential comparison if run. This is: the *proof* that the harness verified all
three layers, the *provenance* for the MCP `ask_estate` endpoint, and the *explanation* an analyst trusts
("here's the number, here's the tree walk, here's why the data is healthy, here's the cross-check").

## 8. Minimal tools + learning-as-baseline-setter

Verification moving into the harness lets the **tool surface shrink**:

- **Keep:** navigation (`get_tables`/`get_columns`/`profile_column`), `run_sql(source)`,
  `combine_results`, `plan` (now first-class), `finish`, `spawn` (now parallel), `define` (now a metric
  tree).
- **Remove / fold in:** `join_path` (over-seeded — inferred from profiles + descriptions), `data_check`
  (becomes a *harness* gate layer, not a tool the model may skip), `describe_tables` (folds into
  `get_tables`), source-scope `find_examples` (no cross-schema noise).

Net: fewer, simpler tools; the intelligence is in the **harness** (plan, fan-out, layered verify) and the
**fabric** (expectations), not in a proliferation of tools or derived graphs.

## 9. Evolving the learning agent: describer → *baseline-setter* + tree-suggester

- **Profiles become baselines-with-tolerance** (expected NULL%, range, distinct, distribution) so drift is
  *detectable*, not just describable.
- **Freshness baselines** per timestamp column (the "pipeline didn't run" detector).
- **Nested-type shape** — profile JSON/struct/array down to fields (the Q-B gap), so authoring is right
  first-time and Layer-3 has ground truth.
- **Gotchas** captured as experiences (array 1-indexing, jsonb casts, engine fn quirks).
- **Candidate metric decompositions + RCA leads** suggested from verified runs (feeds §6; never invents a
  metric, but proposes a driver tree for a human to confirm).
- **Still no join graph** — the fabric is *expectations*; the harness infers joins and verifies.

## 10. Evolving the query agent

- **PLAN becomes the spine** (§2) — a persisted, typed TODO DAG; the ReAct loop executes one node.
- **Sub-agents fan out** (§3) — parallel exploration / reduction / sanity / RCA-driver / differential.
- **The finish gate becomes the layered, mode-gated verifier** (§1, §5) — data-sanity(footprint) +
  logical + authoring-probes + faithfulness; deep adds differential re-compute.
- **RCA walks the metric tree** (§6) with parallel driver sub-agents.
- **Shrink the toolset** (§8) and **emit the audit trail** (§7) as a first-class output.

## 11. Phases (each shippable, tested, behind flags, 0 regression on the single-source happy path)

- **T0 — Agentic TODO as first-class.** Promote `Plan`/`PlanItem` from a thin advisory note to a
  persisted, status-bearing TODO the AGENT maintains + reasons over, rendered at the top of context every
  turn (Claude-Code `TodoWrite` analog). No scheduler, no DAG — the agent drives. Behind a flag; the
  single-source path stays byte-identical. Tests: TODO round-trips + statuses + render; existing e2e
  unchanged.
- **T1 — Parallel sub-agents.** Concurrent fan-out over the execution pool; barrier-join. Tests: two
  independent reductions run concurrently; result_ids stay unique; token ceiling respected.
- **T2 — Data-sanity layer. [DONE]** `diracdata/quality/` (probe + object-store JSONL history, last
  `DIRACDATA_DQ_HISTORY_KEEP=20` snapshots + drift-vs-previous) exposed as two source-aware agent tools:
  `data_health` (fresh one-pass type-aware probe on every touch — no reuse-cache, since data is ingested
  frequently — appends a snapshot + returns drift EVIDENCE) and `read_dq_history` (inspect the trend).
  Drift is measured; materiality is the analyst's/verifier's agentic call (prompt-steered, no hard gate).
  Verified: unit (probe/history/drift/tools) + live Postgres (5000-row orders; jsonb/ARRAY skipped for
  MIN/MAX; freshness captured; seeded-baseline drift flagged). All green, zero regression.
- **T3 — Deep mode + differential. [DEFERRED — build last; twin mode likely dropped.]** Independent
  verify is already baked in by default (every finish is re-judged), so a twin-author differential must
  first prove it catches a class of error the default verify misses before it earns its 2× cost. Likely
  reduces to a verification-DEPTH dial over the existing verifier (normal vs deep = wider drift/consistency
  sweep), not a second author. Revisit after T4/T5.
- **T4 — Metric tree + RCA. [DONE]** Structured `Workspace.metric()` + recursive `metric_tree()`
  (cycle/depth-safe) over the existing user-authored `semantic_layer.json`, exposed as a `metric_tree`
  tool (gated on a real tree, beside `define`): one call returns a metric's whole driver decomposition
  (each node's SQL/formula + additive/multiplicative). The analyst prompt makes RCA a systematic WALK --
  get the tree, `spawn_subagents` one per driver to quantify contributions (reusing T1 fan-out), rank the
  mover, recurse. Structure is authored/measured; ranking is the agent's judgement (no deterministic
  walker/ranker, no typed DAG). RCA leads reused from the experiences memory. Config:
  DIRACDATA_METRIC_TREE_MAX_DEPTH. Verified: 10 unit/integration tests + live fintech metric tree over
  the real Postgres estate (revenue -> order_volume/aov -> ...). Example: docs/examples/semantic_layer.fintech.json.
  All green, zero regression.
- **T5 — Audit trail + tool-surface trim.** Emit the structured trail; remove/fold join_path/data_check/
  describe_tables; source-scope examples. Tests: trail completeness; no-regression on trimmed tools.

## 12. Risks / non-goals

- **Keep the one-deterministic-gate discipline** — data-sanity *measures* deterministically but
  *materiality* is agentic; don't turn drift thresholds into hard gates.
- **Differential re-compute doubles cost** — deep mode only.
- **Drift tolerances need tuning** — start conservative; learn them from repeated healthy runs (the memory
  flywheel), or false alarms will erode trust faster than misses.
- **Parallelism ≠ free** — bound concurrency + budget each sub; fan-out trades wall-clock for a *fixed*
  token ceiling, never an open-ended blowup.
- **The plan phase adds latency** — accepted: it's what makes verification per-step, produces the audit
  trail, and makes deep/normal a plan-depth choice.
- **Non-goal:** auto-inventing metric decompositions or join graphs. Metrics are authored-then-learned;
  joins are inferred-then-verified. The harness *verifies*; it does not *pre-seed*.
