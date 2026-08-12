# AI Data Modeller — Design (Phase 7)

**Status:** design v2 (agentic-first). Phase 7A skeleton done; growing signal now;
Phase 7B/C combined build next.
**Location:** `src/diracdata/modeller/` — new package, isolated from analyst/learning agents.
**Substrate:** consumes only `lake/fintech/…` (produced by `data_harness/`). Never mutates
that substrate. Writes its own outputs under `lake/fintech/modeller/`.
**Storage assumption:** plain Parquet + Hive partitioning today. Modeller reasons about
Iceberg/Delta/Snowflake optimisations via engine-awareness tools regardless — it can
*propose* materialisations for engines we haven't migrated to yet.

## 0 · The rule that governs everything

> **All judgement is agentic. All plumbing is deterministic.**

If a decision affects *what* the modeller proposes — the answer is an LLM tool call, not a
threshold in config. If it's a decision the model *hasn't been asked to make yet* (kill switches,
budgets, path conventions), it's deterministic plumbing.

Concrete: no `min_pattern_runs_per_day`, no `min_saving_multiple`, no `min_confidence`, no
`anti_churn_days` anywhere in the code. The agent reads the numbers, reads the ledger, reads
its accumulated experiences, and decides — every time.

What *is* deterministic:
- **Safety budgets** (max tokens/wall-clock/scan bytes per round, max proposals per round) — seat belts, not the wheel
- **Tool implementations** (SQL execution, S3 I/O, sqlglot parsing) — mechanical
- **Middleware** (retrieval, prompt caching, audit logging) — shapes context, never decides
- **Trigger types** (cron, event, user) — when to *wake*, not what to do once awake

## 1 · Where it sits (family portrait)

| agent | trigger | runtime | shape | primary artifact |
|---|---|---|---|---|
| **analyst** | user question | ~30s | one-shot per turn | SQL + verified answer |
| **learning** | manual `learn2` | 30-50 min | batch (run-to-completion) | semantic_model, join cards, recipes |
| **modeller** ⭐ | scheduled + event | **long-durable, checkpointed** | proposal JSONs for new gold tables |

Same substrate as the other two: `ChatModelProfile` + tool loop + streaming + `ExperienceBook` +
`Conversation` checkpoints + router garden. New pieces:
- Bigger tool surface (engine + optimisation awareness — see §4)
- Analyst-shaped inner loop (framing → ReAct → verify → finish), no deterministic gates
- Explicit middleware layer for context injection + observability
- Round-scoped long-durable checkpoints under `lake/fintech/modeller/checkpoints/`

## 2 · What the modeller consumes

Read-only from the harness:

| # | source | purpose |
|---|---|---|
| 1 | `lake/fintech/lineage.json` | structural map (raw+silver+gold + edges; no PK/FK) |
| 2 | `lake/fintech/query_history/**` | 30-day full detail — every SQL + cost + layer_mix + tables_touched |
| 3 | `lake/fintech/query_history_agg/**` | 90-day rolled up — frequency signal over long window (future — daily rollup job needed) |
| 4 | `lake/fintech/{raw,silver,gold,reference}/**` | parquet metadata + dry-run targets for cost estimation |
| 5 | `lake/fintech/modeller/proposals/**` | **its own prior proposals** — agent decides dedup + refinement + learning from human outcomes |
| 6 | `lake/fintech/modeller/state/**` | ledger (clusters seen, exclusions, cursor) — agent reads and reasons |
| 7 | `lake/fintech/modeller/experiences.md` | long-term heuristics learned from human decisions |

## 3 · What it produces

### 3.1 Proposal JSON

```json
{
  "proposal_id": "prop_20260812_001",
  "round_id":    "01HK3M9V4Q7…",
  "kind":        "materialise_gold",
  "engine":      "duckdb",                     // or iceberg/delta/snowflake/databricks/trino
  "target_name": "g_lending_90d_health_daily",
  "grain":       ["vintage_month", "snapshot_date"],
  "sources":     ["silver.s_loans", "silver.s_repayments", "raw.repayment_schedule"],
  "sql_body":    "…the CTE that pre-aggregates by vintage × snapshot_date…",
  "layout": {                                  // agent decides these
    "partition_by":    ["snapshot_date"],
    "sort_by":         ["vintage_month"],
    "file_size_mb":    128,
    "row_group_rows":  100000,
    "compression":     "zstd"
  },
  "optimisations": {                           // engine-specific, agent picks
    "z_order":         null,
    "clustering_key":  null,
    "incremental":     "MERGE_INTO"            // when engine supports
  },
  "evidence": {                                // agent authored, based on tool outputs
    "matched_query_templates":       ["rca.lending_90day_emi.v1"],
    "queries_matched_last_30d":       900,
    "queries_per_day":                30,
    "avg_current_cost_ms":            55,
    "p95_current_cost_ms":            120,
    "avg_current_scan_bytes":         400000000,
    "dry_run_new_cost_ms":            8,
    "dry_run_new_scan_bytes":         12000,
    "projected_daily_saving_ms":      1410,
    "projected_daily_saving_bytes_gb": 11.6,
    "agent_rationale":                "3 months of rca.lending_90day_emi.v1 shows 30x/day at ~2.5s each — an 15× cost saving via a rolling gold table is material. Grain (vintage × snapshot_date) satisfies all observed variants. Partition by snapshot_date because analysts filter almost exclusively on recent snapshots."
  },
  "confidence": 0.94,                          // agent's own self-report
  "status":     "pending_review",
  "created_at": "..."
}
```

### 3.2 Ledger (structured, but agent reasons about it — not consulted by deterministic code)

- `state/clusters.json` — patterns seen; latest cost + growth trend
- `state/exclusions.json` — patterns the *agent* has decided (via `write_experience`) to skip, with reason
- `state/proposal_index.json` — index by (target_name, grain_key) — agent reads to reason about dedup
- `state/last_run.json` — timestamp cursor for incremental discovery

### 3.3 Experiential memory (`experiences.md`)

Written by an async curator sub-agent after each round. Semi-structured markdown. Examples the
curator might author over time:

- *"Proposals for patterns with < 500 runs/day get rejected as premature — wait for signal to strengthen."*
- *"Analysts prefer daily grain over weekly for lending; don't propose weekly aggregations for lending domain."*
- *"When projected cost saving is < 5×, humans reject even if the pattern is frequent — don't propose."*
- *"Iceberg materialisations proposed with MERGE_INTO are approved more often than full-refresh CREATE OR REPLACE for tables with active writes."*

These are just markdown — retrieved and injected into future rounds by the retrieval middleware.
The agent decides what to do with them.

### 3.4 Audit log (`audit/<round_id>.jsonl`)

Every tool call: `{round_id, step, ts, tool, args, result_size, elapsed_ms, error?}`. Observability
only — not consulted by any logic.

### 3.5 Checkpoints (`checkpoints/<round_id>.json`)

Conversation state + working memory + step counter. Written every ~5 steps + on SIGTERM. Enables
resume-after-crash for long rounds.

## 4 · Tool surface — ~28 tools, four families + engine awareness

### 4.1 Observation (read-only, no judgement)

| tool | returns |
|---|---|
| `list_lineage()` | parsed lineage.json |
| `list_query_patterns(archetype?, since_days?, min_cost_ms?)` | filter query_history — filters are hints not gates |
| `get_pattern_cost(template_id)` | n_runs, mean/p50/p95/p99 ms, min/max, first/last seen, tables_touched, layer_mix, sample_sql |
| `get_layer_mix_distribution()` | overall workload shape |
| `describe_table_layout(uri)` | file count, avg size, row groups, sort columns, compression |
| `describe_column_stats(uri, col)` | cardinality, null ratio, min/max, sortedness |
| `sample_rows(uri, n)` | quick data peek |
| `list_prior_proposals(status?)` | full history of my own writes |
| `read_proposal(id)` | one proposal's full JSON |
| `list_experiences()` | full experiences.md content |

### 4.2 Engine + dialect awareness (facts, agent picks — this is where §"z-ordering, partitions, layout" lives)

| tool | returns |
|---|---|
| `describe_engine_capabilities(engine)` | JSON — what the target engine can actually do (write model, transactionality, schema evolution, time-travel, MERGE INTO, incremental refresh) |
| `list_optimisation_primitives(engine, kind)` | z-order, clustering key, materialised view, sort_order, partition transforms (bucket / truncate / day / hour), search optimisation, liquid clustering, deletion vectors — engine-specific |
| `list_layout_options(engine)` | file size targets, row group tuning, compression codecs, encoding hints |
| `describe_sql_dialect(engine)` | function name diffs, syntax quirks (LATERAL, UNNEST, MERGE, etc.) |
| `list_supported_engines()` | duckdb / iceberg / delta / snowflake / databricks / trino / spark — what we know how to describe |

### 4.3 Design (LLM-assisted; agent decides)

| tool | returns |
|---|---|
| `fingerprint_sql(sql)` | canonical (join_graph + filter_columns + aggregations + group_by) — used by agent for clustering |
| `similarity(fingerprint_a, fingerprint_b)` | 0.0-1.0 structural similarity — agent decides what "similar enough" means |
| `sketch_gold_sql(pattern, hints?)` | LLM drafts candidate SQL from the pattern — returns to agent for review |
| `critique_sql(sql, context)` | separate verify sub-agent critiques — returns findings for agent to judge |
| `suggest_partition_strategy(pattern, layout)` | LLM proposes N partitioning options with tradeoffs — agent picks |
| `suggest_optimisations(engine, sql, workload_hints)` | LLM proposes z-order / clustering / sort options — agent picks |

### 4.4 Validation (mechanical, returns data)

| tool | returns |
|---|---|
| `dry_run(sql, limit?)` | (rows, elapsed_ms, scan_bytes, plan_summary) |
| `explain_plan(sql)` | DuckDB EXPLAIN plan tree |
| `estimate_scan_bytes(sql)` | cost projection without full execution |
| `validate_syntax(sql, engine)` | ok / error message per dialect |
| `run_sql(sql)` | general-purpose exploration escape hatch |

### 4.5 Write (mechanical, gated only by safety budgets)

| tool | effect |
|---|---|
| `write_proposal(payload)` | commit to `lake/fintech/modeller/proposals/` |
| `write_experience(insight, evidence)` | append to `experiences.md` (curator sub-agent uses this) |
| `mark_proposal(id, decision, reason)` | supersede / withdraw |
| `defer(reason, reconsider_at)` | explicit "not now" — agent chooses when to look again |

### 4.6 Loop control (agentic)

| tool | effect |
|---|---|
| `finish(reason)` | agent decides when the round is complete |
| `ask_user(question)` | even the modeller can defer to human for genuine ambiguity |

## 5 · Loop shape — analyst-agent-shaped

```
Round (one wake of the long-lived agent):

    [Framing sub-agent, LLM]
      → observes: list_query_patterns, list_prior_proposals, list_experiences, list_lineage
      → forms hypothesis of which patterns are worth investigating this round
      → passes intent to main loop

    [Main ReAct loop, LLM, bounded by max_react_steps]
      → agent calls observation/engine/design/validation tools in whatever order
      → no gate on cost/saving/frequency — agent reads numbers and decides
      → drafts 0..N candidate proposals

    [Verify sub-agent, LLM, separate call per candidate]
      → independently critiques each proposal
      → agent reads critique, decides commit / revise / discard

    [Finish gate, LLM]
      → agent judges: are proposals complete? cost estimate present? SQL valid? engine capabilities fit?
      → agent calls write_proposal for each survivor, OR defer with reason

    [Async curator, LLM, background]
      → reads round outcomes, decides what long-term lessons to persist
      → appends to experiences.md
```

## 6 · Middleware — inject context, never decide

Three middleware layers wrap the model call. All are context-shaping; none make judgements.

| middleware | injects | reason |
|---|---|---|
| **prompt-cache** | cached system prompt + tool defs across turns | cost |
| **retrieval** | on each turn: semantic search over `experiences.md` for context relevant to the current focus; injects top-K into the prompt | let the agent see past lessons |
| **ledger-loader** | on framing turn: snapshot of `clusters.json`, `proposal_index.json`, `exclusions.json` | agent reasons about dedup itself |
| **audit** | logs every tool call to `audit/<round_id>.jsonl` | observability |
| **turn-budget** | increments token count; hard-stops at `max_run_tokens` | safety |
| **checkpoint** | serialises Conversation + working memory every 5 steps | resume-after-crash |
| **kill-switch** | wall-clock check per step vs `max_run_seconds` | safety |

## 7 · Long-durable characteristics

- **Round identity** — every round has a `round_id` (ULID); all writes tagged
- **Trigger types**: interval (poll every `poll_interval_seconds`), event-driven (new-batch >= K new queries since cursor), user-invoked (`dirac-modeller propose --now`)
- **Between rounds**: explicit sleep (no busy loop); state persisted to MinIO; process can die and resume
- **Signal handling**: SIGTERM → save checkpoint → exit cleanly
- **No cross-round in-memory state** — every fresh process starts cold and re-reads everything from MinIO
- **Concurrent-safe**: only one runner per configured `root_prefix`, enforced via lock file in `state/`

## 8 · Trigger conditions (plumbing, not judgement)

| trigger | condition | delay |
|---|---|---|
| **new-batch** | `count(query_history since cursor) >= poll_new_query_threshold` AND `now - last_run >= min_seconds_between_runs` | ~10 min after peak |
| **cron** | user-configured (e.g., nightly 03:00) | daily |
| **user** | `dirac-modeller propose --now` CLI | on-demand |

**Note:** `poll_new_query_threshold` and `min_seconds_between_runs` are plumbing — they gate when to *wake*, not what to do once awake. Once the agent runs, it reads whatever's there.

## 9 · Evaluation

Hidden `discovery_targets.yaml` (outside `data_harness/` and outside `src/diracdata/modeller/`,
never leaks in) declares the 4 known-good gold tables the modeller SHOULD propose. Grader:
- Fresh harness + N days of simulated workload
- Run modeller for M rounds
- Score: recall (found all 4?), precision (junk?), quality (SQLs correct + efficient?), timeliness (rounds to find each?)
- Regression: after human approves + materialises, re-run workload → verify actual cost drops match projection

## 10 · Build phases

| phase | scope | exit criteria |
|---|---|---|
| **7A** ✅ | Skeleton — package, config, read-side tools, `dirac-modeller inspect` CLI | done · signal-growth in progress |
| **7B/C combined** | Fingerprint tool + FULL agent (framing → ReAct → verify → finish → curator) with all ~28 tools + middleware | Agent autonomously proposes at least 2 of the 4 discovery targets with valid SQL, correct grain, coherent engine choice, and its own written rationale. NO deterministic thresholds anywhere in the code. |
| **7D** | Ledger + curator + retrieval middleware for experiences | Second round doesn't re-propose same pattern by *agent choice*; curator writes coherent experience after human decisions arrive |
| **7E** | Human-in-loop CLI (list/review/approve/reject) | Rejection of a proposal changes what the next round produces via experience |
| **7F** | Continuous mode + eval grader | 24h continuous run against live harness; scored ≥3/4 recall |

**7B/C is the largest chunk (~1 week).** Prerequisite: signal growth complete (~1 min wall-clock).
