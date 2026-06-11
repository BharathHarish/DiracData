# ReAct Middleware Agent Harness

DiracData v0 uses LangChain `create_agent` as the primary answer-time runtime.
`create_agent` is already a LangGraph-backed ReAct loop: model call, tool
calls, tool observations, repeated until the model stops calling tools. We keep
that loop alive and use middleware to make it compiler-aware.

## Why This Replaces The Inner StateGraph

The earlier analyst compiler graph decomposed answering into explicit nodes:
intent, retrieval, SQL planning, validation, repair, execution, and truth
compilation. That shape was useful for design exploration, but it made SQL
repair too prompt-chain-like. Query-engine errors were passed into a repair
node, yet the model was not continuously observing tool errors inside one
ReAct loop.

For v0, SQL errors must be first-class tool observations:

```text
agent writes SQL
-> run_sql_tool returns DuckDB error
-> agent observes error
-> agent repairs SQL
-> run_sql_tool executes corrected SQL
-> agent answers from successful rows
```

## Runtime Shape

```text
create_data_analyst_agent
-> create_agent
   -> dynamic prompt middleware
   -> SQL execution guard middleware
   -> business/schema/profile/join/SQL tools
   -> memory checkpointer and store
```

Manual `StateGraph` remains useful for outer product workflows later:
preflight, approval gates, async learning, reflection jobs, and persistence
work. It is not the v0 analyst brain.

## Middleware

`DataAnalystDynamicPromptMiddleware` builds a prompt for every model call with:

- current date
- catalog, database, schema
- query engine and SQL dialect
- compact authoritative table/column context
- ReAct SQL contract
- latest SQL error observation, when present
- truth-compiler instructions after successful SQL

`SQLExecutionGuardMiddleware` prevents premature final answers for factual or
numeric data questions. If the model tries to answer without a successful
`run_sql_tool` result in the current turn, the middleware jumps back to the
model node with a runtime guard message.

## SQL Tool Contract

`run_sql_tool` returns structured repairable observations:

```json
{
  "status": "error",
  "sql_dialect": "duckdb",
  "error_type": "ambiguous_column",
  "error": "...",
  "observation": "SQL failed...",
  "repair_instruction": "Qualify the ambiguous column..."
}
```

The agent must repair the SQL and call `run_sql_tool` again before answering.

## Tests

The runtime has regression tests for:

- preserving checkpointer conversation state
- returning repairable SQL errors
- repairing SQL after a query-engine error observation
- forcing SQL execution before numeric final answers

