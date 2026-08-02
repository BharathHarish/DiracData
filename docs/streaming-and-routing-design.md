# DiracData — Streaming Envelope + Per-Stage Adaptive Model Routing

**Status:** design (no framework code changed yet) · **Owner:** —  · **Supersedes:** inline `stream_and_collect`

A buildable spec for two related capabilities:

1. **`diracdata.streaming`** — a standalone, provider-agnostic streaming library that normalizes any
   model's stream (Anthropic / OpenAI / Bedrock Converse / OpenAI-compatible like LiteLLM) into one
   canonical **event envelope**, with user-selectable stream modes. Reusable and independently
   testable; the framework imports it, never the reverse.
2. **Per-stage adaptive model routing** — each stage (framing, SQL authoring, verify, summarize,
   learn) can run on a *different* model + sampling budget, chosen from ENV defaults and, per query,
   by an outer-loop **Router** that trades cost for depth (cheap fast-lane on strong experience match,
   deep reasoning model on cold/complex queries, automatic escalation on failure).

---

## 0. Principles (hold for every phase)

- **Separation of concerns.** Streaming knows nothing about agents; the Router knows nothing about
  provider wire formats; stages depend on abstractions (a `StageModel`), not on `model_factory` internals.
- **Clean OOP, small surfaces.** One responsibility per class; dependency direction is one-way
  (`agents → routing → model registry → streaming`). No cycles.
- **No hard-coding / no magic numbers.** Every budget, threshold, model id, and mode default lives in
  `config.Config` and is `DIRACDATA_*`-overridable. The single-source rule already in place
  (`from_env` falls back to field defaults) extends to all new fields.
- **No regression.** Every phase ends with the full suite green (currently 111) plus new tests. Each
  new capability is **off by default** (feature-flagged) until its phase's tests + UAT pass, so
  behaviour is unchanged until explicitly enabled.
- **Agentic judgment, not deterministic gates.** Correctness stays the verifier's job. Routing is a
  *resource* decision, made from **model-emitted signals** (framing self-assesses complexity +
  experience confidence) mapped by a thin, ENV-tunable policy — never brittle keyword regex, never a
  new correctness check. A mis-route is caught by the finish gate and escalated, so it can never
  produce a wrong answer.
- **`diracdata.streaming` is a separate package**, importable on its own (`from diracdata.streaming
  import Collector, StreamMode, StreamEvent`), with no dependency on `agents/`, `memory/`, `context/`.

---

## 1. Dependency map (target)

```
scripts/ask.py ─┐
                ▼
        diracdata.agent (outer loop)
          ├── diracdata.routing        (Router → RunPlan; StageModel resolution)
          │      └── diracdata.models  (ModelRegistry: build+cache chat models by profile)
          │             └── diracdata.utils.model_factory (existing: profiles → provider/model kwargs)
          └── diracdata.streaming      (Collector, adapters, events, modes)  ← also usable standalone
```

`streaming` and `models` have **no upward imports**. `agents/*` call `Collector.run(model, messages,
mode)` instead of `stream_and_collect`, and get their model from `routing`.

---

## 2. Component A — `diracdata.streaming`

### 2.1 The event envelope

A closed, versioned event taxonomy (the contract every adapter and consumer shares):

```
class EventType(StrEnum):
    RUN_START; PHASE_START; RUN_END
    ANSWER_START; ANSWER_DELTA; ANSWER_END
    REASONING_START; REASONING_DELTA; REASONING_END
    TOOL_CALL_START; TOOL_ARGS_DELTA; TOOL_CALL_END; TOOL_RESULT
    USAGE; MODEL_META; ERROR; HEARTBEAT

@dataclass(frozen=True)
class StreamEvent:
    type: EventType
    seq: int
    phase: str | None           # "framing" | "analyst" | "verify" | ...
    data: dict                  # payload (text delta, tool name/args, usage counts, ...)
    raw: object | None = None   # original provider chunk, only when include_raw
```

**Reasoning is a first-class channel** distinct from answer — this is what fixes the gpt-oss
reasoning-bleed and lets a UI show a collapsible "thinking" pane.

### 2.2 Adapters (one per wire format, not per model)

```
class StreamAdapter(Protocol):
    def translate(self, chunk: object) -> list[StreamEvent]: ...   # provider delta → canonical events
    def finalize(self) -> list[StreamEvent]: ...                    # flush end/usage events
```

- `AnthropicAdapter` — content blocks + `thinking` deltas + `tool_use`.
- `OpenAIAdapter` — `choices[].delta.{content, tool_calls, reasoning}`; **covers OpenAI, LiteLLM, and
  any OpenAI-compatible gateway** unchanged.
- `BedrockConverseAdapter` — `contentBlockDelta` (`text`, `reasoningContent`), `toolUse`, `metadata.usage`.

Adapter selection is a lookup keyed by the profile's `provider` (already on `ChatModelProfile`).
**Adding a provider = one adapter + one fixture test.** The agent loop never sees a raw chunk, so a new
model cannot break it.

### 2.3 Collector (replaces `stream_and_collect`)

```
class Collector:
    def __init__(self, adapter_for: Callable[[str], StreamAdapter], config: Config): ...
    def run(self, *, model, messages, provider: str, phase: str,
            mode: StreamMode, sink: EventSink) -> CollectedResult: ...

@dataclass
class CollectedResult:
    answer: str
    reasoning: str            # kept separate — never concatenated into answer
    tool_calls: list[dict]
    usage: Usage              # input/output/reasoning tokens
    message: object | None    # provider message for the langchain loop, when present
```

Folds events → a deterministic result; unit-tested against recorded event logs. Falls back to a single
buffered `invoke` if the provider can't stream (same guarantee as today).

### 2.4 Stream modes + user controls

```
class StreamMode(StrEnum):
    OFF        # no live events; final result only (batch/eval/tests)
    MESSAGES   # answer_* + tool_call_* only (default end-user)
    UPDATES    # coarse phase + tool start/end (progress)
    ALL        # everything incl. reasoning_*, usage, model_meta (dev/observability)
```

Modes are a **render-time filter over the same canonical stream** — trivial and identical across
providers. Orthogonal toggles (also ENV/CLI): `include_reasoning`, `include_usage`, `include_raw`.
An `EventSink` renders events for a target (CLI colored writer, JSON-lines, UI websocket).

### 2.5 Package layout

```
src/diracdata/streaming/
  __init__.py          # exports: StreamEvent, EventType, StreamMode, Collector, EventSink, build_adapter
  events.py            # EventType, StreamEvent, Usage
  collector.py         # Collector, CollectedResult
  modes.py             # StreamMode + filtering
  render.py            # EventSink implementations (cli, jsonl)
  adapters/
    __init__.py        # build_adapter(provider) registry
    anthropic.py  openai.py  bedrock.py  base.py
```

No imports from the rest of `diracdata` except `diracdata.config`. Ships with its own test module.

---

## 3. Component B — per-stage model + sampling config

### 3.1 Stages

```
class Stage(StrEnum):
    FRAMING; AUTHORING; VERIFY; SUMMARIZE; LEARN
```

(`AUTHORING` = the analyst loop; subagents inherit the authoring stage config.)

### 3.2 StageConfig, resolved from ENV with fallback

```
@dataclass(frozen=True)
class StageConfig:
    model_profile: str | None    # None → global agent_model_profile
    max_tokens: int | None       # None → global agent_llm_max_tokens
    temperature: float | None    # None → global (clamped to 0 when deterministic_sampling)
    reasoning_effort: str | None
```

ENV per stage (all optional; missing → global default — the existing single-source pattern):

```
DIRACDATA_STAGE_FRAMING_MODEL_PROFILE / _MAX_TOKENS / _TEMPERATURE / _REASONING_EFFORT
DIRACDATA_STAGE_AUTHORING_...
DIRACDATA_STAGE_VERIFY_...
DIRACDATA_STAGE_SUMMARIZE_...
DIRACDATA_STAGE_LEARN_...
```

`Config` gains `stages: dict[Stage, StageConfig]` built in `from_env`. `deterministic_sampling` stays a
master switch that clamps temperature to 0 for reproducible evals regardless of per-stage values —
knobs **and** a determinism guarantee.

### 3.3 ModelRegistry (build + cache)

```
class ModelRegistry:
    def __init__(self, config: Config): ...
    def get(self, profile_id: str, *, max_tokens=None, temperature=None,
            reasoning_effort=None) -> StageModel: ...   # cached by (profile, overrides)

@dataclass
class StageModel:
    model: object       # the bound chat model
    profile_id: str
    provider: str       # drives adapter selection
```

Wraps the existing `model_factory.build_model_init` / `init_chat_model`; caches so we don't rebuild a
model per call. **This is the single place models are constructed.**

---

## 4. Component C — the Router (AGENTIC, not a policy)

> **Design decision (implemented):** routing is a *judgment*, so the **outer-loop main model chooses**
> — it is NOT a deterministic ENV tier policy. We provide a MODEL CATALOG (facts); the main model
> reasons over it and picks the cheapest model that will still be correct, plus its budget. There are
> no `ROUTER_TIER_*` / `EXPERIENCE_HI` knobs. This supersedes the earlier tier-policy sketch.

### 4.1 The model catalog (the facts we provide)

Each `ChatModelProfile` carries catalog metadata: `cost_tier` (free|low|mid|high), `capability`
(light|standard|strong|frontier), `supports_tools`, `supports_reasoning`, `note`. `render_catalog()`
turns the roster into a compact list for the routing prompt. (GLM-5 = free+strong; gpt-oss = low+reasoning;
Sonnet = mid+frontier; deepseek/nemotron would be `tools=NO` and thus never chosen for authoring.)

### 4.2 The router is a model call

`make_router(main_model, config) -> route(task, signals, failed_profile=None) -> (RunPlan, tokens)`.
When the router is on, the **main model** is asked (prompt `prompts/route.md` + the catalog) to emit:

```
{ "reasoning", "authoring_profile", "max_tokens", "temperature", "max_steps", "allow_shortcut" }
```

`RunPlan` = that decision. `RouteSignals` are FACTS handed to the model (does a proven precedent exist,
the framed intent) — not a policy input. Router off → the standard plan (global model), no call.

### 4.3 Validation + safe fallback (the only deterministic part)

`authoring_profile` must exist in the catalog AND `supports_tools` (the analyst drives tools). Budgets
are clamped to sane ranges; temperature is pinned to 0 under `deterministic_sampling`. Any invalid /
hallucinated / non-JSON pick → fall back to the global model. So a bad decision degrades to today's
behaviour, never a crash.

### 4.4 Escalation (agentic; makes cheap-first safe)

If the loop can't converge (the finish gate never accepts), the agent **re-asks the router** with the
failed model named — the prompt says "pick a STRONGER model than the one that failed." Bounded by
`router_max_escalations`. The finish gate stays the correctness authority; a mis-route costs a retry,
never a wrong answer.

### 4.5 Proven behaviour (live)

- Simple/precedented query → main model chose **GLM-5 (free)**, 5 steps, 1024 tokens, shortcut on.
- Cold cohort query → main model chose **gpt-oss-120b (strong+reasoning)**, 12 steps, explore. Both correct.

### 4.6 Feature flag

`DIRACDATA_ROUTER_ENABLED` (default **false**) — a rollout gate, not routing logic. When false, the
global model is used exactly as today (zero behaviour change). To be A/B'd on the eval rig before default.

---

## 5. Config additions (summary — all ENV, no magic numbers)

| Area | Keys (prefix `DIRACDATA_`) |
|---|---|
| Streaming | `STREAM_MODE`, `STREAM_INCLUDE_REASONING`, `STREAM_INCLUDE_USAGE`, `STREAM_INCLUDE_RAW` |
| Per-stage | `STAGE_<S>_MODEL_PROFILE`, `STAGE_<S>_MAX_TOKENS`, `STAGE_<S>_TEMPERATURE`, `STAGE_<S>_REASONING_EFFORT` |
| Router | `ROUTER_ENABLED` (rollout gate), `ROUTER_MAX_ESCALATIONS` (safety bound). Routing itself is agentic — no tier/profile policy in ENV; models are chosen from the catalog. |

All added to `Config` with defaults; `from_env` falls back to those defaults (single source).

---

## 6. Phased plan + test strategy

Each phase: **unit** (component logic, fixtures/fakes), **integration** (components together, scripted
fake models — no network), **e2e CLI** (real `scripts/ask.py`, skipped without creds/fabric). Every
phase ends with the full suite green and the phase's UAT rows (in `tests/uat_cases.csv`) passing.

### Phase 0 — Per-stage config + ModelRegistry (foundation, no behaviour change)
- Build `Stage`, `StageConfig`, `Config.stages`, `ModelRegistry`. Stages resolve model/sampling;
  defaults reproduce today exactly.
- **Unit:** resolution precedence (plan→stage→global); determinism clamp; registry caching.
- **Integration:** agent builds distinct `StageModel`s per stage from ENV overrides.
- **e2e:** CLI run identical to baseline; `DIRACDATA_STAGE_VERIFY_MAX_TOKENS` observed to change only verify.

### Phase 1 — `diracdata.streaming` envelope + adapters + collector
- Implement events/adapters/collector; make `Collector.run` a drop-in for `stream_and_collect`
  (returns the same `{text, tool_calls, tokens}` **plus** `reasoning`). Flag `STREAM_ENVELOPE_ENABLED`.
- **Unit:** each adapter translates recorded provider chunks → expected events; collector reconstruction;
  reasoning separated from answer.
- **Integration:** loop runs through Collector with a fake streaming model; tool loop unaffected.
- **e2e:** gpt-oss-120b answer contains **no** reasoning preamble; kimi/GLM5 answers unchanged.

### Phase 2 — Stream modes + user controls
- `StreamMode`, `EventSink` renderers; CLI `--stream-mode`, `--no-stream`; ENV defaults.
- **Unit:** mode filter emits exactly the allowed event types.
- **Integration:** `OFF` yields no live events but same final answer.
- **e2e:** CLI `messages` vs `all` vs `off` produce the expected surface; `updates` shows tool start/end.

### Phase 3 — Router v1 (experience fast-lane + escalation)
- Framing emits `routing`; Router → `RunPlan`; outer loop honors per-stage plan + `allow_shortcut`;
  escalation on repeated reject. Flag `ROUTER_ENABLED`.
- **Unit:** policy maps hints→plan; escalation trigger; shortcut path builds [adapt→verify→finish].
- **Integration:** fake models — exact-match query runs fast lane (fewer steps, cheap profile);
  induced finish-gate reject escalates to the next tier/model.
- **e2e:** an exact gold-match query uses `fast` tier; a cold cohort query uses `deep`; both correct.
- **Eval rig:** 5× repeatability A/B (router on vs off) — accuracy holds, tokens ↓.

### Phase 4 — Router v2 (full tiers + per-stage dynamic model)
- Full tier policy; per-stage model differentiation (e.g., strong framing + strong authoring + mid verify).
- **Unit/integration:** tier selection across a matrix of signal combinations.
- **e2e + eval rig:** accuracy parity vs single-strong-model baseline at lower cost.

### Phase 5 — UX events (optional): cost/latency, provenance, replay
- `USAGE`-derived per-turn cost/latency; answer provenance event (result_id / reused experience);
  `--replay` from `transcript.md`.

---

## 7. Regression protection

- All new behaviour behind flags defaulting to **off**; the 111 existing tests must stay green at every
  commit (`PYTHONPATH=src pytest tests -q`).
- `Collector` ships behind `STREAM_ENVELOPE_ENABLED`; only after Phase 1 UAT passes does it replace
  `stream_and_collect`. The old function stays until the envelope is proven, then is deleted (no dupes).
- Golden numeric checks in e2e: retail total = 100,000 clients; 2001 online revenue = $339,537,789.11;
  2002 cohort new = 6,909 / $197,430,325.83, returning = 4,343 / $127,583,020.59.
- Router changes are gated on the eval rig (never a single run).

---

## 8. UAT

Manual/acceptance cases live in [`tests/uat_cases.csv`](../tests/uat_cases.csv) — columns
`id, phase, category, title, precondition, input, expected, status, notes`. Update `status`
(PASS/FAIL/PENDING) as each is exercised. Automated pytest covers the same logic; the CSV is the
human-run acceptance ledger, especially for provider/streaming/router behaviour that needs live models.
