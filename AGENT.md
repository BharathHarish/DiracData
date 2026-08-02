# DiracData — architecture guide for coding agents

DiracData is an adaptive **text-to-SQL analytics agent**: one analyst loop (coding-agent shape) that
frames a business question, explores the data with tools, builds SQL it has verified piece by piece,
and reports only numbers that trace to a query result. It **remembers** two ways: durable
per-conversation memory (transcript + running summary) and durable **schema memory** (an agentic
curator maintaining an `experiences.md` per schema). It can **stream** across any provider and
**route** each turn to a cost-appropriate model.

All code lives in one package: `src/diracdata/`. There is no `v1/`/`v2/`/`v3/`.

## Non-negotiable principles

1. **Agentic judgment, not deterministic gates.** Correctness is enforced by *prompted judgment* (the
   author + an independent verifier reason against the shared golden SQL rules), not hardcoded checks.
   The one deterministic gate is **faithfulness** (numbers in the answer must match a stored result).
   Everything else — what to learn/curate, which model to route to, whether SQL is correct, what to
   summarize — is a model decision. Do not add deterministic correctness checks.
2. **Prompts live in `prompts/*.md`**, never inline. Loaded via `prompts.load_prompt(name)`. Grep
   guard: no `PROMPT = """` in `src/`.
3. **No magic numbers.** Every constant is a `config.Config` field (ENV-overridable via `DIRACDATA_*`);
   leaf functions default to `_DEFAULTS = Config()`. A bare numeric literal outside `config.py` is a bug.
4. **Optional subsystems are their own packages** with one-way deps (`agent → {streaming, routing,
   models, experiences} → config/utils`). A consumer can omit any of them.
5. **Lean.** Minimal files, clean OOP, no redundant logic. One `agent.py` entry point. New capabilities
   land behind a `Config` flag (default off) so the suite stays green.

## Folder map (one line each)

```
src/diracdata/
  agent.py            THE entry point: V4Agent — framing + agentic route + analyst loop + finish gate + conversation + schema memory
  config.py           Config dataclass: EVERY constant, from ENV (Config.from_env); Stage enum + per-stage StageConfig
  prompts.py          load_prompt(name) + dialect_note(engine) — read prompts/*.md at runtime (cached)
  prompts/            analyst, framing(+task), verify, sql_rules, dialect_duckdb, summarize, route, curate, learn_table, learn_joins
  agents/             the loop + phases
    loop.py           run_loop — the single ReAct loop (tools, finish gate, observe hook)
    framing.py        frame_intent — tooled intent framing; folds in conversation summary + learned schema memory
    verify.py         make_verifier + FinishGate (plan-verified + faithful + verified) + faithfulness parser
    subagents.py      build_subagent_tool / run_subagent — a full analyst on a focused task, isolated context
    summarizer.py     make_summarizer — regenerates the running conversation summary each turn (agentic)
  tools/              everything the agent can call: __init__ (build_tools registry), navigation, query, control
  memory/             runtime state + durable conversation memory
    working_memory.py WorkingMemory (goal, intent, facts, plan, result index)  ·  plan.py (Plan/PlanItem)
    results.py        ResultStore — full result -> parquet in the object store; returns a compact envelope
    conversation.py   Conversation — transcript.md (full trace) + summary.md (running, regenerated) per id
  streaming/          OPTIONAL provider-agnostic streaming envelope (importable standalone)
    events.py adapters.py collector.py modes.py  — normalize any stream; keep reasoning separate; off|messages|updates|all
  routing/            OPTIONAL agentic model router
    router.py         make_router — the MAIN model chooses the analyst's model+budget from the catalog; validate + escalate
  models/             ModelRegistry — build + cache chat models by (profile, sampling overrides)
  experiences/        OPTIONAL agentic schema memory (async, self-curating)
    book.py           ExperienceBook — the curated experiences.md (section-aware append/update/delete)
    consolidator.py   MemoryConsolidator — durable async candidate queue + background drain thread
    curator.py        make_curator — the agentic curator (read/update tools, prompts/curate.md)
  context/            read-side domain context: workspace.py (schema map + gold/history example bank), fabric.py, valuecache.py
  learning/           offline fabric builder (run separately): fabric_agent.py, profiler.py, tools.py
  utils/              pure infra: object_store, duckdb_engine, model_factory (+ model catalog), streaming (stream_and_collect), sql, stewardship
scripts/  ask.py (ask/REPL; streaming + router + memory flags)   learn.py (build the fabric)
tests/    unit + integration (fake models; real-fabric tests skip without MinIO)
```

## Request flow (one turn — `V4Agent.run(goal, conversation=...)`)

1. **Frame** (`agents/framing`): bind every concept to a definition / real column BEFORE any SQL; fold
   in the conversation summary (resolve follow-ups) and the curated schema memory (`experiences.md`).
   Writes `confirmed_intent`.
2. **Route** (`routing`, if `router_enabled`): the main model reads the model catalog and picks the
   analyst's model + budget for this turn (cheapest that will be correct); off → the global model.
3. **Analyst loop** (`agents/loop`): one ReAct loop over the tools. `find_examples` for precedent,
   tiered navigation, verify-first `run_sql`, `plan_update` TODO, `spawn_subagent` for fan-out/RCA.
4. **Finish gate** (`agents/verify`): accepted only if every plan item is verified, figures are faithful
   to stored results, and an independent verifier confirms intent + golden SQL rules. If the routed
   model can't converge, the router is re-asked for a stronger one (escalation).
5. **Record** (`memory/conversation` + `agents/summarizer`): append the full trace to `transcript.md`,
   regenerate `summary.md`.
6. **Learn** (`experiences`, if `agentic_memory_enabled`): the turn is enqueued (instant, durable) and
   the **async curator** (background thread) folds any durable knowledge into `experiences.md` —
   append/update/delete, kept succinct by judgement. Read back at framing/authoring next turn.

## Run & test

```bash
# Ask one question (or omit --question for a REPL). Flags: --stream-mode off|messages|updates|all, --no-stream.
PYTHONPATH=src .venv/bin/python scripts/ask.py --schema retail_analytics \
    --model-profile bedrock_zai_glm_5_ap_south_1 --question "How much online revenue in 2001?"

# Enable optional subsystems (all off by default):
DIRACDATA_STREAM_ENVELOPE_ENABLED=true DIRACDATA_ROUTER_ENABLED=true DIRACDATA_AGENTIC_MEMORY_ENABLED=true \
  PYTHONPATH=src .venv/bin/python scripts/ask.py --schema retail_analytics --question "..." --stream-mode all

# Build/refresh the compiled fabric (offline);   Tests (real-fabric tests skip without MinIO):
PYTHONPATH=src .venv/bin/python scripts/learn.py --schema retail_analytics
PYTHONPATH=src .venv/bin/python -m pytest tests -q
```

## How to change things

- **Add a tool:** the right `tools/*.py` builder (navigation = read, query = run/slice, control =
  plan/finish/transcript); register in `tools/__init__.build_tools`; add its name to `_FRAMING_TOOLS`
  if framing should see it.
- **Edit a prompt / SQL rule:** edit `prompts/*.md` (effective next run). `sql_rules.md` is shared by
  the analyst + verifier; a dialect's specifics live in `dialect_<engine>.md`.
- **Add a constant / knob:** a `config.Config` field + a matching `Config.from_env` line
  (`DIRACDATA_<NAME>`) + reference via `_DEFAULTS.<field>` — never inline.
- **Add a model:** a `ChatModelProfile` in `utils/model_factory` with its catalog facts (cost,
  capability, `supports_tools`, `supports_reasoning`, note) so the router can reason about it.
- **Add a streaming provider:** a `StreamAdapter` subclass in `streaming/adapters.py` + register it.
- **Add a memory kind:** a new `## SECTION` — the curator is prompt-driven (`prompts/curate.md`); the
  taxonomy is open.
```
