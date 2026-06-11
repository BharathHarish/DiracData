# MVP Scope

## MVP Definition

The MVP is a working open-source data analyst agent with two phases:

- Learning: build semantic context from a scoped TPC-DS pod using local parquet files queried through DuckDB.
- Answering: answer business questions over the learned context with visible verification and data quality warnings.

## In Scope

### User Workflow

- User provides local parquet data location.
- User declares SQL dialect, starting with DuckDB.
- User defines a scoped pod by selecting tables.
- User provides business input about the schema.
- User provides query history as CSV or a DuckDB-accessible table/view.
- Learning phase samples scoped tables and profiles columns.
- Learning phase creates one or more learned contexts.
- Learning phase creates a pod-level join graph.
- User provides a natural-language business question.
- Answering phase searches learned contexts and schema semantically.
- Answering phase uses the pod-level join graph.
- Agent builds staged SQL.
- Agent executes intermediate queries.
- Agent verifies intermediate and final results.
- Agent returns final answer, SQL, result summary, and optional trace.

### Core Capabilities

- Schema introspection
- Table profiling
- Column profiling
- Context building
- Business input ingestion
- Semantic schema search
- Pod-level join graph discovery
- Query history ingestion
- Optional embedding interface
- DuckDB SQL execution
- Intermediate result wrangling
- Large result summarization
- Data freshness checks
- Row count and join sanity checks
- Final answer support checks

### Test Harness

- Local TPC-DS parquet dataset
- Simulated SQL query history loaded from CSV or DuckDB
- Golden business questions
- Expected table and join retrieval labels
- Expected SQL result checks
- Data quality perturbation tests

## Out Of Scope For First MVP

- Full warehouse adapter matrix
- Production auth flows
- BI tool integrations
- Automatic dashboard generation
- Fine-tuned model training
- Full semantic layer authoring
- Enterprise governance workflows
- Multi-agent team simulation
- Streaming interactive UI
- Fully autonomous warehouse-wide exploration

## Success Criteria

The MVP should satisfy all of the following:

- Correctly retrieves relevant tables for a set of TPC-DS business questions.
- Builds learned context from scoped TPC-DS tables.
- Builds a useful pod-level join graph.
- Produces executable DuckDB SQL for a meaningful subset of questions.
- Detects common bad data conditions injected into test data.
- Surfaces uncertainty when schema or data evidence is weak.
- Avoids returning unsupported confident conclusions.
- Produces trace artifacts that explain what was checked.
- Handles large intermediate results through summaries and sampled inspection.

## Optimization Targets

The product ambition is to optimize correctness, transparency, schema discovery, join discovery, speed, cost, and ease of adoption together.

For the MVP, each target needs a measurable proxy:

- Correctness: final SQL result matches expected answer or invariant.
- Transparency: trace includes selected tables, joins, checks, and warnings.
- Schema discovery: top-k context, table, and column retrieval accuracy.
- Join discovery: pod-level join graph precision and recall.
- Speed: median end-to-end answer time over benchmark questions.
- Cost: model calls and execution scans are counted per question.
- Ease of adoption: user can run the local TPC-DS demo with one documented command after setup.

## MVP Pod Proposal For Review

Use a commerce analytics pod from TPC-DS.

Candidate tables:

- `store_sales`
- `web_sales`
- `catalog_sales`
- `date_dim`
- `item`
- `customer`
- `customer_address`
- `store`
- `web_site`
- `promotion`
- `reason`
- `returns` tables where available

This is a proposal, not a confirmed decision.

## Open Scope Questions

- Which TPC-DS scale factor should be the default for local tests: 1GB only, or smaller smoke dataset plus 1GB benchmark?
- Should MinIO be used in the MVP, or should local parquet remain the only first target?
- What minimum answer quality is acceptable before the first public release?
- Should embeddings be mandatory for the MVP, or optional after lexical search works?
