# Typed Workflow Kernel Plan

## Goal

Move DiracData v2 from model-discretion orchestration to a typed data-agent workflow
where LLMs reason inside stages, while the harness owns transitions, gates, retries,
and final execution.

## Design Principles

- LLMs may reason and use tools inside a stage, but cannot skip gates.
- Every stage returns a parseable packet.
- SQL is never executed until intent, SQL assertions, dry run, and steward review pass.
- Data Engineering rewrites must be revalidated before execution.
- Gold/query-history patterns are evidence, not final authority; they produce assertions
  and defaults that can be disclosed or challenged.

## Phase 1: Typed Kernel

Create a new workflow behind `--workflow typed`.

Stages:

1. Compile semantic context.
2. Stop for clarification if the compiler finds SQL-affecting ambiguity.
3. Create intent packet.
4. Author SQL packet.
5. Run deterministic semantic assertions.
6. Run dry run.
7. Run steward review.
8. Optionally run data engineering optimization.
9. Re-run assertions, dry run, and steward review after optimization.
10. Execute only the final steward-approved SQL.

Tests:

- Compiler clarification stops before model calls.
- Anti-join semantic assertion catches inverted SQL.
- `NOT IN` is rejected for negative cohort semantics.
- Steward fail routes back to SQL author.
- DE optimized SQL must pass steward again before execution.

## Phase 2: CLI Wiring

Expose the workflow through:

```bash
v2/scripts/run_primitive_agent.py --workflow typed
```

Existing `gated`, `supervisor`, and `outer` modes remain unchanged for comparison.

Tests:

- CLI interactive clarification still works.
- CLI non-interactive output captures typed workflow events.

## Phase 3: Assertion Growth

Add semantic assertions generated from gold NL-SQL/query-history patterns:

- entity role assertions: customer-current location, billing location, shipping location
- cohort assertions: anti-join, semi-join, repeat purchase, first purchase
- grain assertions: row grain, customer grain, line-item grain
- value assertions: categorical value probe required before predicate
- join assertions: only observed or validated join paths
- result assertions: final answer cannot introduce claims not proven by SQL

Tests:

- Retail goldset recall and assertion coverage.
- Known failure cases: customer location vs billing location, first-time buyer wording,
  anti-join inversion, fanout joins.

## Phase 4: Row-Count And Cost Probes

Add optional CTE probe SQL generation:

- count rows per CTE
- count distinct business keys per CTE
- compare before/after joins
- flag unexpected fanout
- detect very large final result sets

Tests:

- Synthetic fanout fixture.
- Large result truncation fixture.
- Cost guard fixture.

