# Product Design

## Product Thesis

DiracData is a trustworthy data agent for people who ask business questions but do not want to write code or SQL.

The agent should feel like a careful analyst embedded in a team pod. It first learns the scoped pod, then uses learned contexts from that pod to search schema semantically, build a query plan, verify intermediate results, detect data quality risks, and produce a final answer with optional epistemic trace.

The product is not a text-to-SQL assistant. It is an analyst agent that treats data trust as part of the answer.

## Confirmed Decisions

- First MVP user: data analyst-like workflows for product managers, business users, growth teams, and other end consumers who are not proficient with code.
- First workflow: ask a business question over a scoped data warehouse context.
- Scope model: a pod is a business-team-level boundary containing N relevant warehouse tables, not the entire warehouse.
- Context model: one pod can contain multiple learned contexts.
- First open-source shape: standalone package named `diracdata`.
- Runtime strategy: build with LangGraph dependencies and patterns now; create LangGraph-adjacent adapters later.
- First execution engine: DuckDB.
- First local data layout: TPC-DS parquet files in a local folder.
- First query history sources: CSV or DuckDB-accessible SQL query history.
- UX posture: final answer first, optional epistemic trace, and explicit warnings for data quality issues.
- Semantic posture: business meaning first, not only technical table and column names.

## First User Promise

A business user should be able to ask:

> Why did weekly active users drop in the west region last week?

The system should first learn the pod by sampling scoped tables, profiling columns, ingesting business input, ingesting query history, generating short semantic descriptions, and building a pod-level join graph.

Then the answering agent should:

1. Search learned contexts and schema for relevant business concepts.
2. Identify candidate tables, metrics, dimensions, and joins.
3. Explain the intended calculation before executing expensive or ambiguous work.
4. Build the SQL in stages rather than as one opaque statement.
5. Verify row counts, join shapes, null rates, date ranges, freshness, and distribution sanity.
6. Return a concise answer with caveats when the data does not fully support the conclusion.

## Product Principles

### Trust Before Fluency

The agent should not optimize for sounding confident. It should optimize for being right, being inspectable, and knowing when the available evidence is weak.

### Analyst-Like Decomposition

The agent should behave like an analyst who constructs a query through small tested steps:

- Find the relevant tables.
- Check date ranges.
- Inspect grain.
- Verify joins.
- Calculate base populations.
- Add filters.
- Compare cohorts or periods.
- Validate the final result.

### Business Semantics First

Users ask in business language. The system must map phrases like "active customers", "churned accounts", "orders", "gross revenue", and "new users" onto technical schema using learned contexts, table metadata, column profiles, query history, and pod-level join graph.

### Scoped Data Context

The first useful agent does not need the entire warehouse. A pod-level knowledge base can provide major economic value if it is well profiled, semantically indexed, and continuously verified.

### Learn Then Answer

The system has two product phases:

- Learning: convert scoped tables into living semantic context.
- Answering: use learned context to answer business questions with verification.

### Optional Trace, Mandatory Discipline

The end user may not want to inspect every step. The system should still create a trace internally and expose it on demand.

## MVP User Experience

The first interface can be CLI, notebook, or programmatic. The experience should eventually support:

- Natural-language question
- Scoped warehouse connection
- SQL dialect declaration
- Pod declaration
- Learned context selection or automatic context search
- Final answer
- Generated SQL
- Result table or chart-ready data
- Data quality warnings
- Verification summary
- Optional detailed trace

## Open Product Questions

- What is the first concrete pod domain: commerce, product analytics, growth, finance, or support?
- What exactly defines a learned context inside a pod?
- Should the first UX require approval before running final SQL, or run automatically with clear trace?
- What counts as an acceptable final answer for a non-technical user: prose only, table plus prose, chart-ready output, or all three?
- Which warehouse dialect should be the first non-DuckDB target: Databricks SQL, Snowflake, BigQuery, or Postgres?
- What level of query cost control should exist in the MVP?
