# Implementation Plan

## Phase 0: Design And Repo Scaffold

Status: in progress.

Deliverables:

- Repository structure
- Product design doc
- Engineering architecture doc
- MVP scope doc
- Epistemic SQL compiler doc
- Schema search and profiling doc
- Test harness and evals doc
- Low-level design doc

## Phase 1: Local Data, DuckDB, And Query History Foundation

Deliverables:

- DuckDB adapter
- Local parquet dataset registration
- Schema introspection
- SQL execution with row limits
- Result summarization
- Query history ingestion from CSV
- Query history ingestion from DuckDB table or view
- Basic CLI or Python entrypoint for local runs

Key tests:

- Adapter can register parquet tables.
- Adapter can list tables and columns.
- Adapter can execute safe read-only SQL.
- Adapter summarizes large results without loading everything into model context.
- Query history can be parsed from CSV and DuckDB.

## Phase 2: Learning Phase And Context Building

Deliverables:

- Table profiler
- Column profiler
- `select * limit 1000` style table sampling
- Distinct value sampling per column
- Business input capture
- Profile persistence format
- Generated table and column descriptions
- Learned context artifacts
- Catalog search records
- Optional embedding interface

Key tests:

- Profiles match expected TPC-DS schema.
- Null rates, distinct counts, and top values are stable.
- Date coverage is detected.
- Large tables are profiled with bounded sampling.
- Learned contexts contain short semantic descriptions.
- Context build works without embeddings.

## Phase 3: Pod-Level Join Graph

Deliverables:

- Name-based join candidate scoring
- Type compatibility scoring
- Sample value overlap scoring
- Cardinality compatibility scoring
- Query-history join extraction
- Join graph representation

Key tests:

- Known TPC-DS joins are recovered.
- Dangerous false joins are ranked lower.
- Duplicate dimension keys trigger warnings.
- Join row-count checks detect explosion and loss.

## Phase 4: Semantic Context And Schema Search

Deliverables:

- Hybrid lexical search
- Business glossary matching
- Generated description matching
- Optional BAAI/BGE embedding support
- Pod-level schema search API
- Context-level search API

Key tests:

- Business questions retrieve expected tables and columns.
- Synonyms and business terms improve retrieval.
- Search works without external embedding services.

## Phase 5: Epistemic SQL Compiler

Deliverables:

- Intent representation
- Logical plan representation
- Staged SQL generation
- Verification check planning
- Plan repair loop
- Trace records

Key tests:

- Plans contain required verification checks.
- SQL fragments execute in order.
- Failed checks trigger repair or refusal.
- Final answer confidence reflects verification results.

## Phase 6: Data Analyst Agent

Deliverables:

- LangGraph graph
- Tool bindings
- Agent state model
- Final answer contract
- Optional trace output

Key tests:

- End-to-end question answering over TPC-DS pod.
- Agent uses allowed tables only.
- Agent exposes final SQL and checks.
- Agent refuses unsupported questions.

## Phase 7: Public MVP Polish

Deliverables:

- README quickstart
- TPC-DS setup guide
- Example pod config
- Example eval run
- Basic contribution guide
- Release checklist

Key tests:

- New user can run local demo.
- Smoke tests pass without 1GB dataset.
- Full evals run locally against TPC-DS.

## Immediate Next Decisions Needed

- Confirm the first TPC-DS local data directory.
- Decide whether to include a tiny generated sample dataset for CI.
- Confirm whether MinIO stays out of MVP.
- Pick the first concrete pod domain from TPC-DS.
- Pick the first non-DuckDB dialect to design against.
