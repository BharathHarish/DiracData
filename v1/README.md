# DiracData

DiracData is an early-stage open-source project for trustworthy data agents built on LangGraph.

The first target is a data analyst agent for business users, product managers, growth teams, and other non-code-proficient consumers who need to ask questions over a scoped warehouse context.

The central thesis is that data agents should act like careful analysts, not one-shot SQL generators. They should search schema semantically, construct queries in verifiable steps, inspect intermediate results, check data quality, surface caveats, and answer with epistemic humility.

## Current Status

This repository is in design-first scaffold mode.

The first implementation milestone will focus on:

- A learning phase that turns scoped pod tables into semantic context
- An answering phase that uses learned context to answer business questions
- DuckDB as the local execution engine
- Local parquet files as the first warehouse simulation
- TPC-DS as the first repeatable benchmark dataset
- Schema introspection, profiling, and semantic search
- Join candidate discovery from schema, values, and CSV/DuckDB SQL query history
- A test harness for SQL correctness, result verification, and data quality checks

## Design Docs

- [Product Design](docs/product.md)
- [Engineering Architecture](docs/architecture.md)
- [MVP Scope](docs/mvp_scope.md)
- [Epistemic SQL Compiler](docs/epistemic_sql_compiler.md)
- [Schema Search And Profiling](docs/schema_search_and_profiling.md)
- [Test Harness And Evals](docs/test_harness_and_evals.md)
- [Low Level Design](docs/low_level_design.md)
- [Implementation Plan](docs/implementation_plan.md)

## Planned Import Shape

The standalone package import is expected to become:

```python
from diracdata import data_analyst_agent
```

LangGraph integration is a first-class design constraint, but the current package is not nested under `langgraph`.
