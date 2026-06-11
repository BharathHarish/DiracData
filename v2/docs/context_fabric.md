# Context Fabric v2

## Thesis

An analytics agent does not need a large semantic dump. It needs a precise
context slice:

1. the relevant schema graph nodes
2. the safe join path between them
3. the SQL library entries that match the intent
4. the SQL-affecting nuances attached to those nodes and edges

The schema graph is lossless. Retrieval is only an access method over it.

## Artifact Set

v2 should converge on two primary artifacts.

### Schema Graph

The graph has a tree spine:

```text
domain -> entity -> table -> column
```

It also has cross edges:

- joins
- aliases
- confounders
- metric dependencies
- SQL library links

Column leaves may contain SQL-affecting metadata:

- aliases and business terms
- allowed values
- null meaning
- time role
- filter role
- metric role
- caveats

### SQL Library

The SQL library stores proven or candidate SQL patterns.

Each entry should be small:

- domain
- intent terms
- SQL snippet or template
- parameters
- required graph nodes
- required join edges
- rules
- source
- review status

The SQL library can include query-history patterns, user-provided metric SQL,
and self-play candidate patterns.

## Runtime Tools

The future runtime should need only a few tools.

### `context_compiler(question)`

Returns the compact SQL-authoring context slice.

### `structured_relationships(from_nodes, to_nodes)`

Returns safe join paths, relationship types, and grain effects.

### `run_sql(sql)`

Executes read-only SQL and returns results or execution feedback.

## What Not To Add

Do not add standalone artifacts unless they feed the graph, the SQL library, or
the compiled context slice.

Avoid standalone summaries, broad semantic maps, all-schema dumps, analyst
questions, and generic profiling reports in runtime context.

