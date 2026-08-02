# DiracData

**A self-learning analyst agent for text-to-SQL — it frames intent, verifies every number, and gets
better and cheaper with every question.**

Most text-to-SQL fails not because SQL is hard but because the model lacks governed context and never
checks its own work. DiracData is built the opposite way: a single analyst loop (coding-agent shape)
that binds every business concept to a definition *before* writing SQL, builds the query **verify-first**
(runs each fragment to confirm filters, joins, and grain), and reports only numbers that trace to a
stored result. Every answer passes a **finish gate** — plan-verified, faithful to query results, and
independently reviewed against shared golden SQL rules — so it's *verifiable per answer*, not "probably
right." And it **remembers**: an async curator distills each verified run into durable schema knowledge
(SQL patterns, RCA leads, data gotchas, bindings) that the next question reuses.

## What makes it different

- **Compounding trust.** Every verified answer becomes reusable schema memory (`experiences.md`,
  curated agentically — append/update/delete, kept succinct). Accuracy rises and cost falls with use;
  the "curation tax" is paid by *usage*, not by hand-authored YAML.
- **Verify-first, gated finish.** Independent verifier + faithfulness (numbers must match a stored
  result) + golden SQL rules (grain/fan-out, NULLs, MECE, joins, calendar-vs-fiscal, dialect) — the one
  deterministic gate is faithfulness; everything else is agentic judgment.
- **Model- and warehouse-agnostic.** Provider-normalized streaming (Anthropic / OpenAI / Bedrock /
  OpenAI-compatible), and an **agentic router** where the main model picks the cheapest model that will
  still be correct (free model for a simple lookup, a strong reasoning model for a cold RCA) — no policy,
  just judgment over a model catalog. Runs fully self-hostable on your object store (MinIO/S3).
- **Investigative, not just metric lookup.** Multi-step RCA, cohort/MECE segmentation, and exploratory
  analysis — with subagents for fan-out — the open-ended work a fixed semantic layer can't pre-model.

## Quickstart

```bash
# Ask one question (or omit --question for a REPL).
PYTHONPATH=src .venv/bin/python scripts/ask.py --schema retail_analytics \
    --model-profile bedrock_zai_glm_5_ap_south_1 --question "How much online revenue in 2001?"

# Turn on the optional subsystems (all off by default):
DIRACDATA_STREAM_ENVELOPE_ENABLED=true DIRACDATA_ROUTER_ENABLED=true DIRACDATA_AGENTIC_MEMORY_ENABLED=true \
  PYTHONPATH=src .venv/bin/python scripts/ask.py --schema retail_analytics --question "..." --stream-mode all

# Resume a durable conversation (transcript + running summary carry across sessions):
PYTHONPATH=src .venv/bin/python scripts/ask.py --schema retail_analytics --conversation-id my-thread

# Build the compiled context fabric for a schema (offline);   run the tests:
PYTHONPATH=src .venv/bin/python scripts/learn.py --schema retail_analytics
PYTHONPATH=src .venv/bin/python -m pytest tests -q
```

Everything is ENV-configured (`.env` / `DIRACDATA_*`): model profiles, object store (local or S3/MinIO),
data root, budgets, and the optional streaming / router / memory switches. See `src/diracdata/config.py`.

## Architecture

One brain, tools for capability, deterministic gates only where a guarantee is required — plus three
**optional, self-contained packages** a consumer can omit:

- `agent.py` — the entry point: frame → route → analyst loop → finish gate → record → learn.
- `diracdata.streaming` — provider-agnostic streaming envelope (normalize any model; keep reasoning
  separate; `off` / `messages` / `updates` / `all` display modes).
- `diracdata.routing` — the agentic model router (main model chooses model + budget per turn from a
  cost/capability catalog; validate + escalate).
- `diracdata.experiences` — schema-scoped agentic memory: an async, self-curating `experiences.md`,
  read back into every turn.
- `context/` (domain fabric + workspace), `memory/` (WorkingMemory + durable conversation), `learning/`
  (offline fabric builder), `utils/` (DuckDB engine, object stores, model factory + catalog).

Read **[AGENT.md](AGENT.md)** for the full folder map, per-turn request flow, and the non-negotiable
principles (agentic judgment over deterministic gates; prompts in `prompts/`; every constant in ENV).

## Tests

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests -q
```
Fake models for logic; the real-fabric / live-model tests skip without the object store. The UAT ledger
(streaming, routing, memory, cross-model) with pass/fail is in [tests/uat_cases.csv](tests/uat_cases.csv).
