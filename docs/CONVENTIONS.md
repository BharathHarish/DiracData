# Engineering conventions (non-negotiable)

This is production framework code, meant to be kept and borrowed by developers. Every change honors
these. They override convenience.

## Code

1. **Clean OOP, clear separation of concerns.** Small classes/functions, one responsibility each.
   Optional subsystems are their own importable packages with one-way deps
   (`agent → {engines, execution, streaming, routing, models, experiences} → config/utils`).
2. **Lean, no bloat.** No dead code, no speculative abstractions, no "just in case" branches, no random
   mess. If a thing isn't used on a real path, it doesn't ship. Minimal files, minimal surface.
3. **No hard-coding, no magic numbers.** Every constant is a `config.Config` field, ENV-overridable via
   `DIRACDATA_*`; leaf functions default to `_DEFAULTS = Config()`. A bare literal outside `config.py`
   is a bug.
4. **Generic, not shortcut.** Build the capability properly and generally; do not special-case to make one
   demo pass. If it only works for the happy path, it isn't done.
5. **Framework-first.** This is a Python framework, not a UI. Public APIs must be obvious to import and
   reuse; prompts in `prompts/*.md`; behavior configured via ENV/YAML/programmatic, never a hosted
   assumption.

## Correctness & judgement

6. **No deterministic hard-coded judgement layer.** The ONLY deterministic gate is **faithfulness**
   (a number must trace to a stored result — a fact-check, not a judgement). Every *judgement* — is the
   SQL right, is drift material, should we verify deeper, should we fan out, which driver caused a move —
   is **agentic**: prompt-driven, made by the agent/verifier, dynamically invoked. The harness *provides
   capabilities and measured facts and prompts*; it never encodes a decision as an if/else.
7. **Agentic loop, not a typed DAG.** The outer loop is a Claude-Code-style agentic loop: the agent keeps
   a TODO, decides the next step, spawns sub-agents by judgement, and calls verification when it judges
   it needed. No deterministic scheduler walks a fixed graph. Parallelism/verification/sanity are
   *offered* to the agent, not *forced* by the harness.

## Process

8. **Zero regression.** New capability lands behind a `Config` flag (default off / behavior-preserving);
   the single-source, single-engine happy path stays byte-identical. Run the full suite before AND after
   every change. (This is the rule most often broken — treat it as a gate.)
9. **UAT-first, then tests.** For every feature: write a feature-level `tests/uat_*.csv` with the cases
   BEFORE coding, then unit + integration tests that cover the capability (not just the happy path).
   Live-model / real-DB tests skip cleanly when creds are absent.
10. **Verify before claiming.** State outcomes faithfully — run the tests, show the numbers; never report
    "works" from a single happy run or without evidence.
