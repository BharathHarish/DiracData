## SQL Validation Stage

Before executing final SQL for a complex analytics question, write a visible `SQL_VALIDATION` block.

The validation must be strict and scoped. Compare only:
- NL AST filters, operators, thresholds, time windows, and requested outputs.
- Schema bindings and grounded values actually used.
- Candidate SQL CTEs, joins, predicates, aggregations, and final output columns.

The `SQL_VALIDATION` block must include:
- One row per required NL AST node ID.
- The original phrase.
- The SQL expression or CTE that implements it.
- Status: `PASS`, `FAIL`, or `UNRESOLVED`.
- A short mismatch reason when status is not `PASS`.

Return `PASS` only if the candidate SQL preserves every required NL AST node. Return `FAIL` if any predicate, operator, source scope, grain, value, time window, or requested output is missing or changed.
Reject SQL that narrows or expands a quantitative predicate. An "at least" condition must not gain an upper bound. An "at most" condition must not gain a non-neutral lower bound.
Reject SQL that narrows a broad activity source scope to one source without explicit user wording or schema evidence.

Do not provide broad improvement suggestions. If validation fails, state exact mismatches, repair the SQL, and validate again.

Only after `SQL_VALIDATION: PASS`, execute the final SQL with a leading `-- final:` comment.
