# DiracData v2

This folder is the clean-room rebuild of DiracData's context fabric.

The v2 rule is simple: keep only context that can change SQL accuracy.

Root-level assets stay shared:

- `../.env` for model and storage credentials
- `../v1` for the preserved first implementation

v2-local assets:

- `data/` for local datasets and query history
- `context/` for compact context-fabric inputs

## Current Scope

v2 starts with data contracts only:

- `schema_graph`: lossless domain/entity/table/column graph
- `sql_library`: reusable SQL patterns and metric snippets
- `context_slice`: the small compiled context an agent should receive

No agents, middleware, vector search, or live learning code belongs here until
the context shape is stable.

## First Principles

Context is kept only when it can affect:

- table or column choice
- join path
- metric grain
- filter interpretation
- date/time semantics
- null handling
- result caveats

Everything else is documentation and should not be in the agent prompt.
