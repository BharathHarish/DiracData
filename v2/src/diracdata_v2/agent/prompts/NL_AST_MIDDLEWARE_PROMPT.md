## NL AST Stage

For complex analytics questions, create a visible `NL_AST` JSON block before using schema, value, or SQL tools.

The `NL_AST` is the logical contract of the user's question before SQL exists. It must preserve the user's meaning without inventing schema details.

Include only fields that are relevant:
- `intent_type`
- `grain`
- `source_scope`
- `filters`
- `metric_conditions`
- `time_windows`
- `requested_outputs`
- `ambiguities`
- `verification_plan`

For each filter, metric condition, source-scope decision, and requested output:
- Assign a stable `id`.
- Preserve the original phrase.
- Preserve the operator, threshold, subject, time window, and output obligation exactly when present.
- Mark `binding_status` as `unbound`, `needs_value_grounding`, `ambiguous`, or `resolved`.

Do not compress "at least N" and "at most M" into a range unless the user explicitly stated a range.

For broad activity verbs such as buying, shopping, ordering, transacting, or using, set `source_scope` to all relevant activity sources unless the user explicitly restricts the channel or source.

Do not write SQL in this stage. Do not bind to table or column names unless the user explicitly named them. If a term needs a business definition or schema evidence, mark it as unresolved instead of guessing.
