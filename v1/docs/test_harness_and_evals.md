# Test Harness And Evals

## Goal

Build a data-source-agnostic test harness that proves the system can learn a scoped pod, create learned contexts, find schema, build SQL, verify results, and detect bad data conditions over a realistic analytics dataset.

## First Dataset

TPC-DS parquet files queried through DuckDB.

Current status:

- Dataset is not yet downloaded.
- Local parquet is the preferred first target.
- MinIO is optional and not required for the first MVP unless we decide object storage behavior is part of the product test.

## Why Local Parquet First

Local parquet keeps the first harness simple:

- No object storage setup
- No credentials
- Fast iteration
- DuckDB can query directly
- Easier CI smoke tests with smaller subsets

MinIO can be added later to simulate S3 semantics if object storage behavior becomes important.

## Harness Layers

### Unit Tests

- Schema introspection
- Column profile calculation
- Learned context creation
- Join candidate scoring
- Query history parsing
- Embedding record creation when configured
- Result summarization
- Data quality checks

### Learning Evals

Given scoped tables, business input, and query history, assert:

- Table samples are collected with bounded limits
- Distinct value samples are collected with bounded limits
- Table and column descriptions are generated
- Learned contexts include the expected tables and business concepts
- Pod-level join graph includes expected joins

### Golden Retrieval Tests

Given a business question, assert expected top-k retrieval:

- Relevant learned contexts
- Relevant tables
- Relevant columns
- Candidate joins
- Candidate metrics
- Candidate date columns

### SQL Execution Tests

Given a structured plan, assert:

- SQL compiles under DuckDB
- Intermediate CTEs execute
- Result shape matches expected invariant
- Final output is stable enough for comparison

### Verification Tests

Inject data quality issues and assert that the agent flags them:

- Missing recent partition
- Unexpected row-count drop
- Duplicate dimension key
- Null spike in metric column
- Join explosion
- Join loss
- Date range mismatch
- Distribution shift

### End-To-End Agent Evals

Given a question, pod, and data path, assert:

- Answer is supported by SQL result
- SQL uses allowed tables only
- Trace includes required checks
- Warnings are present when expected
- Confidence is not overstated

## TPC-DS Pod Design

Proposed commerce pod:

- Sales facts: store, web, catalog
- Customer dimensions
- Item dimensions
- Date dimensions
- Store and website dimensions
- Promotion dimensions
- Return facts where relevant

This is a proposal for review.

## Example Eval Questions

Initial candidate questions:

- Which sales channel had the highest revenue last month?
- Did web sales grow week over week?
- Which customer segment had the highest return rate?
- What were the top product categories by revenue?
- Did promotions increase average order value?
- Which regions saw the largest drop in sales?
- Are there signs that yesterday's sales data did not refresh?

These need to be converted into golden eval cases after dataset generation.

## Simulated Query History

The harness should generate approximately 500 realistic query history records for TPC-DS.

The MVP should load query history from:

- CSV file
- DuckDB table or view

The generated history should include:

- Common joins
- Repeated metrics
- Date filters
- CTE-heavy analyst queries
- Failed SQL attempts
- Dashboard-style aggregations
- User and warehouse metadata

Query history should be stored in a warehouse-agnostic internal format. Databricks-style fields can be added later when Databricks becomes an adapter target.

## Metrics

### Schema Search

- Context recall at k
- Table recall at k
- Column recall at k
- Mean reciprocal rank
- Business concept match rate

### Join Discovery

- Join precision at k
- Join recall at k
- False positive rate for dangerous joins
- Detection rate for join explosion risk

### SQL And Answer Quality

- Executable SQL rate
- Correct answer rate
- Unsupported answer rate
- Verification coverage
- Repair success rate

### Data Quality

- Stale data detection rate
- Null spike detection rate
- Duplicate key detection rate
- Join loss detection rate
- Distribution shift detection rate

### Operational

- End-to-end latency
- Number of model calls
- Rows scanned
- Intermediate result size
- Token usage

## Open Harness Questions

- What local directory should hold generated TPC-DS parquet files?
- Should the harness include a tiny checked-in sample dataset, or only scripts to generate/download data?
- Should public CI run only smoke tests while local evals run the 1GB benchmark?
- Should eval scoring be deterministic enough to run without a model, or include model-in-the-loop evals from day one?
- Should the query history CSV schema be minimal and warehouse-agnostic, or shaped to ease future Databricks compatibility?
