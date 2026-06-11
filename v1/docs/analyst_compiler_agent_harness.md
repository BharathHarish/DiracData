# Analyst Compiler Agent Harness

## Objective

DiracData's answer-time agent should behave like a careful analyst, not a one-shot SQL generator. The harness turns a natural-language question into typed intermediate state, retrieves only relevant learned context, plans safe joins and probes, executes read-only SQL, and verifies the answer before speaking.

## LangGraph Design

Use a controlled `StateGraph` as the outer runtime. The graph owns checkpoints, typed state, route decisions, and stage transitions. LLM calls are stage workers; they do not own the global control policy.

Current stage order:

1. `build_intent_frame`
   - Extracts business metric, dimensions, filters, time range, entities, and ambiguity.
   - Uses recent thread turns for follow-up interpretation.

2. `retrieve_context`
   - Reads existing learned artifacts and business grounding.
   - Retrieves schema descriptions, business terms/metrics/templates, joinable pairs, table descriptions, and profile hints.

3. `decide_route`
   - Uses deterministic routing.
   - Current routes: `clarify`, `known_metric`, `novel_query`.
   - Future routes: `exact_repeat`, `parametric_repeat`, `known_pattern`.

4. `plan_sql`
   - Builds a typed SQL plan with base table, base grain, selected columns, joins, probe SQL, final SQL, assumptions, and risk notes.

5. `validate_sql_plan`
   - Ensures every probe and final SQL is scoped, read-only, and references only pod tables.

6. `execute_probes`
   - Runs bounded row-count, join, freshness, and filter-presence checks.

7. `execute_sql`
   - Runs the final safe SQL with `DIRACDATA_AGENT_SQL_MAX_ROWS`.

8. `truth_compile`
   - Produces final answer, verification status, caveats, and confidence.
   - The displayed business numbers are rendered deterministically from `sql_result.rows`; the model supplies verification/caveats, not free-form metric values.

## Why Not One ReAct Loop

The outer graph should not be a ReAct agent. ReAct is useful inside hard stages, but the global answer policy must be deterministic enough to enforce correctness and short-circuit cheap cases. A single ReAct loop can forget to retrieve business definitions, skip fanout checks, or bypass freshness checks, especially on smaller models.

## Current Implementation Scope

The first implementation avoids adding new tools. It uses the existing repositories and query engine directly:

- `LearnedArtifactRepository`
- `BusinessGroundingRepository`
- `QueryEngine`
- `validate_read_only_sql`
- LangChain model factory through `agent_chat_model_from_settings`

The harness also dry-runs every probe and final SQL with zero rows before execution. This catches binder errors such as nonexistent columns before the graph runs probes or final SQL.

## Runtime Controls

Relevant environment variables:

- `DIRACDATA_AGENT_COMPILER_MAX_PROBES`
- `DIRACDATA_AGENT_COMPILER_PROBE_MAX_ROWS`
- `DIRACDATA_AGENT_COMPILER_MAX_REPAIRS`
- `DIRACDATA_AGENT_SQL_MAX_ROWS`
- `DIRACDATA_AGENT_STREAMING`
- `DIRACDATA_AGENT_STREAM_MODES`
- `DIRACDATA_AGENT_STREAM_VERSION`
- `DIRACDATA_AGENT_CHECKPOINTER`

## Future Extension Points

- Experience/pattern route before SQL planning.
- Parametric SQL template compilation.
- Intent-aware join planner backed by the context graph.
- Stage-specific model routing.
- Async reflection agent that promotes repeated patterns into memory.
- Human clarification interrupts for ambiguous IntentFrames.
