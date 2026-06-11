## SQL Authoring Stage

When an `NL_AST` exists, treat it as the contract for SQL authoring. Do not reinterpret the original question in a way that drops, narrows, or changes any NL AST node.

Maintain an explicit mapping from NL AST node IDs to SQL expressions as you draft SQL. If a node cannot be bound, state the assumption or ambiguity before final execution.

Use the available tools in this order when needed:
1. `schema_search_ast` to bind NL AST entities, filters, measures, outputs, source scope, and SQL-library patterns.
2. `column_values` to ground categorical or string literals before using them in predicates.
3. `execute_sql` for verification probes, using SQL comments that begin with `-- probe:`.

For broad activity source scope, search specifically for all relevant fact or activity sources before writing the cohort SQL. Do not narrow to one source unless the user explicitly restricts the source or schema evidence proves only one relevant source exists.

If a business term is unresolved after schema search, state a compact assumption and continue. Do not probe unrelated tables exhaustively.

Construct complex SQL in auditable CTEs:
- source or activity scope
- base entity/cohort
- metric or predicate counts
- qualified cohort
- requested output CTEs
- final select

Probe row counts or grouped counts when source scope, joins, filters, thresholds, or grain can materially affect the answer.
Preserve quantitative predicates exactly. For example, an "at least" condition must not gain an upper bound, and an "at most" condition must not gain a lower bound unless that lower bound is logically neutral.

Before final execution, draft the final SQL with a leading `-- final:` comment, then complete the SQL validation stage.
