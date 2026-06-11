# Schema Search And Profiling

## Goal

Create a semantic schema layer during the learning phase so the answering agent can map business questions to relevant contexts, tables, columns, metrics, dimensions, and joins inside a scoped pod.

Schema search is not only a runtime operation. The searchable context is created before answering.

## Inputs

- Table names
- Column names
- Column types
- Null rates
- Distinct counts
- Min and max values
- Common values
- Sample values
- Numeric distributions
- Date ranges
- Query history
- Business glossary
- Pod description
- User-provided metric definitions
- User-provided business explanations of tables and columns
- `select * limit 1000` style table samples
- Distinct value samples per column

## Outputs

- Table descriptions
- Column descriptions
- Candidate business concepts
- Candidate metrics
- Candidate dimensions
- Join candidates
- Pod-level join graph
- Grain hypotheses
- Data quality baselines
- Search index records
- Embedding records when configured
- Learned contexts

## Learning Phase Context Build

For every scoped table in a pod, the context builder should collect:

- Sample rows using a bounded `select * limit 1000` style query
- Schema metadata
- Table and column profiles
- Up to at least 1000 distinct observed values per column where available and practical
- Query history usage patterns
- Business input supplied by the user or data owner

The output is a learned context: a compact semantic artifact that can be searched during answering.

One pod can have multiple learned contexts. The join graph is still built across the pod, because joins are relationships between tables in the scoped pod rather than only inside one context.

## Profiling Strategy

### Table Profile

For each table:

- Row count
- Column count
- Approximate size
- Candidate primary keys
- Candidate foreign keys
- Date columns
- Numeric metric-like columns
- Categorical dimension-like columns
- Last observed date where inferable
- Sample rows with privacy-safe limits
- Short semantic description
- Query history usage summary

### Column Profile

For each column:

- Name
- Data type
- Null count and null rate
- Distinct count
- Min and max for numeric and date columns
- Top values for categorical columns
- Sample values
- Distinct value samples
- String length summaries
- Semantic hints from names and values
- Short semantic description

## Semantic Description Generation

The system should generate compact descriptions that combine:

- Technical name
- Profile evidence
- Business glossary hints
- Query history usage
- Neighboring join relationships
- Value examples when useful
- Human business input

Example:

```text
Column: customer.c_preferred_cust_flag
Likely meaning: whether a customer is marked as preferred.
Evidence: boolean-like values, customer dimension table, frequently filtered in customer segmentation queries.
```

Descriptions should be short, simple, and optimized for semantic matching against business-language questions.

## Embeddings

Table, column, metric, and learned-context descriptions should be embeddable.

BAAI/BGE is the leading proposed model family for local/open embeddings. The exact model and runtime are still open.

The search layer should support:

- Lexical search without embeddings
- Local BAAI/BGE embeddings when configured
- Future hosted embedding providers if needed

## Search Strategy

The answering phase should use hybrid retrieval over learned context:

- Lexical matching over names and generated descriptions
- Type-aware boosts
- Query-history usage boosts
- Table centrality boosts from join graph
- Optional embedding search when configured

Embedding support is useful, but the MVP should still run without requiring an external embedding provider.

## Join Discovery

Join candidates should be discovered during learning from multiple signals:

- Exact column name matches
- Normalized fuzzy name matches
- Primary-key and foreign-key naming conventions
- Data type compatibility
- Value overlap from sampled values
- Uniqueness of candidate dimension keys
- Co-occurrence in query history
- Existing SQL join predicates
- Cardinality and matching uniqueness

Each candidate join should receive a score and explanation.

Candidate output:

```text
store_sales.ss_customer_sk -> customer.c_customer_sk
score: high
evidence:
- compatible integer types
- strong name match
- customer key is unique in sampled profile
- join appears in historical queries
```

## Query History Simulation

For the first test harness, query history can be loaded from a CSV file or a DuckDB table/view.

The records should include:

- statement id
- warehouse id
- executed by
- start time
- end time
- status
- statement text
- statement type
- error message
- total duration
- rows produced
- bytes scanned when simulated

The simulated history should include realistic joins, filters, CTEs, failed queries, and repeated business patterns.

Databricks-style query history remains a future adapter target, not a required MVP source.

## Large Output Handling

The agent must not feed large raw outputs into the model.

Result handling should use:

- Row count
- Column names and types
- Null summaries
- Top values
- Numeric summaries
- Date ranges
- Small samples
- Outlier summaries
- Hash or checksum for repeatability where useful

## Open Schema Questions

- Should generated schema descriptions be model-generated, rule-generated, or hybrid in the first implementation?
- Should profiles be recomputed every run, cached per data path, or persisted by pod?
- What is the privacy policy for sample values in open-source defaults?
- What is the minimum join score needed before the compiler can use a join without asking for confirmation?
- What exactly defines multiple contexts inside one pod?
- Which BAAI/BGE embedding model and runtime should be the first target?
