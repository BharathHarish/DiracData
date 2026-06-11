# NL2SQL Evaluation Harness Design

Date: 2026-06-07

## Purpose

DiracData needs an eval harness that proves whether the learned context, business grounding, join graph, SQL tools, and agent prompt make smaller and cheaper models reliable for business analytics.

The benchmark should not only ask, "Did the final answer match?" It must tell us where the agent succeeded or failed:

- Did it use the right tools?
- Did it hallucinate table or column names?
- Did it map business entities to the right schema objects?
- Did it produce safe, valid, scoped SQL?
- Did the SQL result match ground truth?
- Did it use business grounding and join evidence rather than guessing?
- Did it ask for clarification when the question was underspecified?
- Did the harness lower cost per correct answer across models?

This is how we make the thesis measurable: better context and better verification should let Qwen/Haiku-class models compete with Sonnet on many analytics tasks.

## Correction On Bedrock Model IDs

AWS documentation confirms these Bedrock runtime model IDs:

- Claude Sonnet 4.6: `anthropic.claude-sonnet-4-6`
- Claude Haiku 4.5: `anthropic.claude-haiku-4-5-20251001-v1:0`
- Qwen3 Next 80B A3B: `qwen.qwen3-next-80b-a3b`

What we tested:

- Direct Bedrock Sonnet 4.6 ID in `ap-south-1`: rejected for on-demand throughput. AWS requires an inference profile ID or ARN.
- `global.anthropic.claude-sonnet-4-6`: worked with plain `Converse`.
- Direct Bedrock Haiku 4.5 ID in `ap-south-1`: rejected for on-demand throughput. AWS requires an inference profile ID or ARN.
- `global.anthropic.claude-haiku-4-5-20251001-v1:0`: later blocked by account-level Anthropic use-case details.
- Qwen `qwen.qwen3-next-80b-a3b`: worked with `Converse`, `ConverseStream`, and the full LangGraph tool-using UAT.

Implication:

- Model profiles should distinguish direct runtime model IDs from inference-profile IDs.
- Eval reports must record the exact resolved model ID, region, provider, and endpoint mode.
- Bedrock Anthropic evals should use inference-profile IDs when required by AWS.

Sources:

- AWS Claude Sonnet 4.6 model card: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html
- AWS Claude Haiku 4.5 model card: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-4-5.html
- AWS Qwen3 Next 80B A3B model card: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen-qwen3-next-80b-a3b.html

## Lessons From Existing Products And Benchmarks

### Snowflake Cortex Analyst

Snowflake's Verified Query Repository stores natural-language questions and corresponding SQL inside the semantic model. Snowflake uses verified queries both as runtime guidance and as evaluation ground truth, but a verified query selected for evaluation is temporarily removed from the semantic view during the eval run so the model cannot simply retrieve the exact answer.

DiracData should copy this principle:

- Keep verified SQL examples in business grounding.
- Split them into `runtime_guidance` and `eval_holdout` partitions during eval.
- Never let an eval question's exact ground-truth SQL be available to the agent in that run.
- Keep near-duplicate paraphrases in evals to test semantic generalization.

Sources:

- Snowflake Verified Query Repository: https://docs.snowflake.com/user-guide/snowflake-cortex/cortex-analyst/verified-query-repository
- Snowflake Cortex Analyst evaluations: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations

### Databricks AI/BI Genie And Agent Evaluation

Databricks Genie uses benchmark questions to evaluate response quality and recommends multiple phrasings of the same question with the same example SQL to assess accuracy. Databricks Agent Evaluation also scores traces, tool calls, correctness, and groundedness.

DiracData should copy this principle:

- Eval one question family with multiple phrasings.
- Score final answer and intermediate trace behavior.
- Use deterministic checks where possible, and reserve LLM judges for semantic judgment.
- Store trace evidence so failures can be classified into tool, schema, join, metric, SQL, or answer failures.

Sources:

- Databricks Genie overview: https://docs.databricks.com/en/genie/index.html
- Databricks Genie monitoring and benchmarks: https://docs.databricks.com/aws/en/genie/monitor
- Databricks Agent Evaluation docs: https://api-docs.databricks.com/python/databricks-agents/latest/databricks_agent_eval.html

### Academic NL2SQL Benchmarks

Spider established broad cross-domain text-to-SQL evaluation. BIRD pushed closer to messy real-world database tasks and emphasizes execution accuracy. CoSQL adds multi-turn conversational querying and clarification behavior.

DiracData should not rely on public leaderboard numbers as proof of enterprise readiness. We should borrow the metrics but build a pod-specific benchmark because enterprise accuracy depends on business semantics, schema linking, metric definitions, joins, and data quality.

Useful benchmark lessons:

- Execution accuracy is more useful than exact SQL string match because equivalent SQL can differ syntactically.
- SQL structure still matters because wrong joins can accidentally produce the same scalar on a tiny slice.
- Conversational tests need follow-up state and clarification handling.
- Hard cases should include ambiguity, unanswerable requests, temporal logic, joins, and grouping.

Sources:

- BIRD benchmark: https://bird-bench.github.io/
- Spider benchmark: https://yale-lily.github.io/spider
- CoSQL paper: https://arxiv.org/abs/1909.05378

## Product Requirements

### Users

- DiracData engineering team comparing models and harness changes.
- Design partners validating whether DiracData can answer questions over their pod.
- Future enterprise buyers who need transparent accuracy claims.

### Primary Goal

Build a repeatable eval harness that runs the same NL question set across models and harness variants, then produces deterministic and trace-aware scores.

### Non-Goals For The First Eval Release

- Vendor benchmark integrations against Databricks/Snowflake APIs.
- Full synthetic data generation beyond retail analytics.
- Automatically claiming customer-specific production accuracy.
- Replacing human review for new business metric definitions.

## Eval Artifacts

### Grounding Source

```text
conf/business_grounding/{catalog}.{database}.{schema}.yaml
```

Needs expansion beyond the current seed to cover all retail schema families:

- Customers and demographics
- Addresses and geography
- Online purchases and refunds
- Store purchases and refunds
- Mail-order purchases and refunds
- Merchandise, category, brand, class
- Calendar and fiscal periods
- Delivery methods and fulfillment centers
- Marketing campaigns
- Stock levels and inventory positions
- Support centers and retail locations
- Return reasons

### Gold Eval Set

```text
evals/gold/{catalog}.{database}.{schema}.yaml
```

Proposed shape:

```yaml
version: 1
scope:
  catalog: retail_pod
  database: analytics
  schema: retail_analytics

cases:
  - id: retail_001
    family: customer_demographics
    difficulty: simple
    question: count all male customers from California
    paraphrases:
      - how many male clients currently live in CA?
      - number of male shoppers from California
    expected:
      answer_type: scalar_integer
      value: 936
    ground_truth_sql: |
      SELECT COUNT(DISTINCT c.client_record) AS customer_count
      FROM clients c
      JOIN client_profiles cp ON c.current_client_profile_ref = cp.client_profile_record
      JOIN addresses a ON c.current_address_ref = a.address_record
      WHERE cp.gender = 'M'
        AND a.state = 'CA'
    required_tables:
      - clients
      - client_profiles
      - addresses
    required_columns:
      - clients.client_record
      - clients.current_client_profile_ref
      - clients.current_address_ref
      - client_profiles.client_profile_record
      - client_profiles.gender
      - addresses.address_record
      - addresses.state
    required_grounding:
      defaults:
        - gender_means_current_client_profile
        - customer_state_means_current_address
      metrics:
        - distinct_customers
    scoring:
      exact_answer_required: true
      sql_equivalence_required: false
```

### Eval Run Record

```text
artifacts/evals/{catalog}/{database}/{schema}/{run_id}/cases/{case_id}/{model_profile}.json
artifacts/evals/{catalog}/{database}/{schema}/{run_id}/summary.json
artifacts/evals/{catalog}/{database}/{schema}/{run_id}/traces/{case_id}/{model_profile}.jsonl
```

Each case result:

```json
{
  "case_id": "retail_001",
  "question": "count all male customers from California",
  "model_profile": "bedrock_qwen3_next_80b_a3b_ap_south_1",
  "resolved_provider": "bedrock_converse",
  "resolved_model": "qwen.qwen3-next-80b-a3b",
  "region": "ap-south-1",
  "harness_version": "data_analyst_agent_v1",
  "status": "passed",
  "scores": {
    "overall": 0.95,
    "result_accuracy": 1.0,
    "sql_correctness": 1.0,
    "schema_validity": 1.0,
    "entity_mapping": 1.0,
    "tool_use": 0.9,
    "business_grounding": 1.0,
    "join_evidence": 1.0,
    "answer_groundedness": 1.0,
    "clarification_behavior": null
  },
  "expected_answer": 936,
  "actual_answer": 936,
  "generated_sql": "SELECT ...",
  "tool_calls": ["business_term_search_tool", "schema_info_tool", "join_discovery_tool", "run_sql_tool"],
  "hallucinated_tables": [],
  "hallucinated_columns": [],
  "missing_required_tables": [],
  "missing_required_columns": [],
  "latency_ms": 20549,
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "tool_calls": 10,
    "sql_executions": 1
  },
  "warnings": []
}
```

## Scoring Dimensions

### 1. Result Accuracy

Primary deterministic score.

For scalar answers:

- Exact numeric equality after type normalization.
- Tolerance allowed only for ratios, percentages, floating-point sums, and currency.

For tabular answers:

- Column set match.
- Row set match after ordering normalization.
- Numeric tolerance where configured.

For top-N/ranked answers:

- Top-k exact match.
- Rank-sensitive score such as NDCG when ordering matters.

### 2. SQL Correctness

Use multiple checks:

- SQL parses in configured dialect.
- SQL is read-only.
- SQL references only scoped tables.
- SQL executes.
- SQL output matches ground-truth output.
- SQL uses required grain, filters, joins, and aggregation semantics.

Execution match is necessary but not sufficient. A query can accidentally produce the same scalar while using the wrong customer role or date role.

### 3. Hallucination Table And Column Names

Deterministic AST/schema validation:

- `hallucinated_tables`: generated SQL references table not in pod.
- `hallucinated_columns`: generated SQL references column not in table.
- `ambiguous_unqualified_columns`: unqualified columns that resolve to multiple tables.
- `external_access_violation`: generated SQL reads files, external tables, or unscoped functions.

This score should be binary per case and aggregated as a rate.

### 4. Entity To Schema Mapping

Did the agent map business words to the right schema objects?

Examples:

- "customer" -> `clients.client_record`
- "from Arizona" -> current address state, unless billing/shipping is explicitly mentioned
- "online" -> `online_purchases`
- "jewelry" -> `merchandise.category = 'Jewelry'`
- "2002" -> sale calendar day, not shipping or return date unless stated

Scoring:

- Deterministic required table/column/default checks from case YAML.
- Optional LLM judge only for cases where several mappings are semantically acceptable.

### 5. Tool Use Correctness

The trace should prove the agent used the harness instead of guessing.

Checks:

- Business term search called before schema tools for business questions.
- Required business definitions/defaults/metrics/templates retrieved when case config says they exist.
- Schema tools called for relevant tables/columns.
- Profile values checked for categorical filters.
- Join discovery called before joins.
- SQL execution called exactly enough times.
- Failed tool output was handled rather than ignored.

This is not "more tools is better." It is "right tool at right time." The eval should penalize unnecessary repeated calls on simple questions.

### 6. Business Grounding Use

Checks:

- Required grounding IDs were retrieved.
- Generated SQL follows grounding policies.
- Answer caveats mention relevant policy only when helpful.
- Eval holdout does not leak exact ground-truth SQL into retrieval.

### 7. Join Evidence

Checks:

- Every join in generated SQL is found in `joinable_pairs.jsonl`, a SQL template join path, or a runtime-validated join.
- Join direction and cardinality are plausible.
- No accidental key joins based only on name similarity.

### 8. Answer Groundedness And Traceability

Checks:

- Final numeric/table answer is derived from `run_sql_tool` output.
- Final answer does not add facts not present in SQL result or tool evidence.
- Caveats are evidence-based.
- If data quality or freshness evidence is absent, the agent does not overclaim freshness.

### 9. Clarification Behavior

Some questions should not produce SQL immediately.

Examples:

- "How many active customers do we have?" without time window.
- "What was revenue last quarter?" if revenue definition is missing.
- "Which campaign performed best?" without performance metric.

Score:

- `clarification_required=true` and agent asks a focused question.
- Penalize hallucinated defaults when the grounding layer says to ask.
- Penalize unnecessary clarification for deterministic questions.

### 10. Cost, Latency, And Harness Efficiency

Measure:

- `cost_per_correct_answer`
- `tokens_per_correct_answer`
- `latency_per_correct_answer`
- `tool_calls_per_correct_answer`
- `sql_executions_per_correct_answer`
- `grounding_hits_per_correct_answer`
- `retry_count`

This is where the harness thesis becomes economic.

## Evaluation Modes

### Mode A: Pure Agent

The agent receives active learned artifacts and business grounding, but no case-specific hints.

Use for model comparison.

### Mode B: Holdout Verified Query

The case's ground-truth SQL is removed from available grounding during the run. Other verified queries remain.

Use for semantic generalization tests.

### Mode C: Guided Verified Query

The verified SQL template is available as runtime guidance.

Use for production behavior measurement, not raw generation skill.

### Mode D: No Grounding Ablation

Disable business grounding tools.

Use to prove grounding layer value.

### Mode E: No Join Graph Ablation

Disable joinable pair retrieval and runtime join recovery.

Use to prove join learning value.

### Mode F: Follow-Up Conversation

Run multi-turn cases with the same checkpointer thread.

Use to score memory and conversational state.

## Gold Set Construction

The first retail gold set should have 100 cases across breadth, not just 100 paraphrases of two questions.

### Query Distribution

| Category | Count | Coverage |
|---|---:|---|
| Customer demographics and geography | 10 | clients, client_profiles, addresses |
| Online purchases | 10 | online_purchases, merchandise, calendar_days |
| Store purchases | 8 | store_purchases, retail_locations |
| Mail-order purchases | 8 | mail_order_purchases, mailer_pages |
| Returns and refunds | 10 | online_refunds, store_refunds, mail_order_refunds, return_reasons |
| Product and category analysis | 8 | merchandise, brand, class, category |
| Time and fiscal analysis | 8 | calendar_days, clock_times |
| Marketing and campaigns | 8 | marketing_campaigns across channels |
| Fulfillment, delivery, stock | 8 | stock_levels, fulfillment_centers, delivery_methods |
| Profitability and monetary metrics | 8 | net_paid, net_profit, tax, discount, returns |
| Cohort, retention, and first purchase | 6 | first_sale_calendar_day_ref, repeated purchases |
| Ambiguous or clarification-required | 6 | active, revenue, best, recent, customer role |

### Difficulty Distribution

- 25 simple: one to three tables, scalar count/sum.
- 35 medium: four to six tables, filters, grouping, date roles.
- 25 hard: multi-channel union, returns, profitability, cohorts, top-N.
- 10 ambiguity tests: should ask clarification.
- 5 negative tests: unanswerable with scoped pod or missing metric.

### SQL Shape Distribution

- Counts and distinct counts
- Sums and averages
- Group by dimensions
- Top-N ordering
- Date filters and fiscal filters
- Multi-table joins
- Multi-channel union
- Refund-adjusted metrics
- Ratio metrics
- Cohort logic
- Null handling
- No-result cases

## Seed 100-Case Blueprint

This is the coverage blueprint for generating actual ground-truth SQL. Each case should be expanded into exact SQL and expected answer by the gold generator.

### Customer Demographics And Geography

1. Count male customers from California.
2. Count female customers from Arizona.
3. Count customers by state for the top 10 states.
4. Count preferred customers by gender.
5. Count customers born after 1980 by state.
6. Count customers by education status in Texas.
7. Count customers with high credit rating by state.
8. Count customers with dependents by gender.
9. Count customers by marital status and gender.
10. Count customers from counties in California.

### Online Purchases

11. Count online customers who bought Jewelry in 2002 from Arizona.
12. Total online net paid in 2002.
13. Top 10 merchandise categories by online sales in 2001.
14. Average online order quantity by category.
15. Count distinct online billing customers by year.
16. Count online orders shipped to a different customer than billed.
17. Online net profit by state using current customer address.
18. Online sales by website property.
19. Online purchases with delivery cost above threshold.
20. Online coupon amount by campaign.

### Store Purchases

21. Count store customers from California in 2002.
22. Store net paid by retail location state.
23. Top 10 stores by net profit.
24. Store sales by merchandise category and year.
25. Average basket quantity by ticket.
26. Count store tickets by customer gender.
27. Store discounts by campaign.
28. Store tax collected by state.

### Mail-Order Purchases

29. Count mail-order customers by state.
30. Mail-order net paid by year.
31. Mail-order sales by mailer department.
32. Top mailer pages by order count.
33. Mail-order delivery cost by delivery carrier.
34. Mail-order net profit by fulfillment center.
35. Count mail-order billing customers who used campaigns.
36. Mail-order sales by support center.

### Returns And Refunds

37. Online refund amount by return reason.
38. Store refund amount by return reason.
39. Mail-order refund amount by return reason.
40. Online return rate by merchandise category.
41. Store net loss by state.
42. Mail-order net loss by fulfillment center.
43. Count customers with both online purchase and online refund.
44. Count customers with store purchases and store refunds in same year.
45. Refund amount by gender.
46. Top return reasons for Jewelry.

### Product And Category Analysis

47. Count merchandise items by category.
48. Average current price by category.
49. Top brands by online sales.
50. Top brands by store sales.
51. Merchandise categories sold in all three channels.
52. Products with stock on hand below threshold.
53. Category profitability across channels.
54. Products with refunds but no sales in selected period.

### Time And Fiscal Analysis

55. Online sales by calendar month in 2002.
56. Store sales by fiscal quarter.
57. Mail-order sales on weekends vs weekdays.
58. Online purchases by hour of day.
59. Refunds by month and channel.
60. Sales on holidays vs non-holidays.
61. Net profit by fiscal year.
62. Orders by day of week.

### Marketing And Campaigns

63. Online sales by campaign.
64. Store sales by campaign.
65. Mail-order sales by campaign.
66. Campaign discount amount by channel.
67. Campaign net profit by merchandise category.
68. Count customers exposed to email campaigns through purchases.
69. Compare sales for campaigns with discount active vs inactive.
70. Top campaign purpose by net paid.

### Fulfillment, Delivery, And Stock

71. Stock quantity by fulfillment center.
72. Stock quantity by merchandise category.
73. Online delivery cost by carrier.
74. Mail-order delivery cost by carrier.
75. Fulfillment centers with lowest stock for Jewelry.
76. Online orders by delivery type.
77. Mail-order orders by delivery contract.
78. Stock on hand trend by year for selected category.

### Profitability And Monetary Metrics

79. Gross online sales by category.
80. Net paid with tax by channel.
81. Net profit by channel in 2002.
82. Discount rate by channel.
83. Coupon amount as percent of sales by category.
84. Return-adjusted net paid by channel.
85. Tax amount by customer state.
86. Highest profit category across all channels.

### Cohort And Retention

87. Count customers whose first sale year is 2001.
88. Count customers with online purchases in both 2001 and 2002.
89. Count customers who bought online first and later bought in store.
90. Repeat online buyers by year.
91. Customers with first shipping date before first sale date.
92. Retained customers from 2001 to 2002 by state.

### Ambiguity And Negative Cases

93. How many active customers do we have?
94. What was revenue last quarter?
95. Which campaign performed best?
96. Count customers from Arizona who received shipments in California.
97. How many users churned last month?
98. What is conversion rate by campaign?
99. Which products are popular?
100. How many customers are at risk?

Cases 93-100 should not all force SQL. Some should require clarification or return "not answerable with current pod/grounding."

## Ground Truth SQL Generation Workflow

Use Sonnet 4.6 as an assisted author, not as the final authority.

1. Generate candidate NL questions from coverage blueprint.
2. Generate candidate SQL using full schema, join graph, business grounding, and profile values.
3. Validate SQL with deterministic parser and scoped table/column checks.
4. Execute SQL with DuckDB.
5. Run independent SQL critique:
   - table correctness
   - join correctness
   - filter correctness
   - grain correctness
   - aggregation correctness
   - null/date semantics
6. Store expected answer and result shape.
7. Human-review high-risk cases.
8. Lock into `evals/gold/...yaml`.

Important:

- Sonnet can draft gold SQL, but the gold case is accepted only after deterministic execution and review.
- For complex cases, create at least one adversarial paraphrase.
- For ambiguous cases, ground truth is not SQL; it is expected clarification behavior.

## Metric And Template Expansion Plan

The business grounding layer must expand before the 100-case eval is meaningful.

### Metrics To Add

- `distinct_customers`
- `online_customers`
- `store_customers`
- `mail_order_customers`
- `gross_sales`
- `net_paid`
- `net_paid_with_tax`
- `net_profit`
- `discount_amount`
- `coupon_amount`
- `return_amount`
- `net_loss`
- `return_rate`
- `return_adjusted_net_paid`
- `average_order_value`
- `average_quantity`
- `stock_on_hand`
- `repeat_customers`
- `retained_customers`
- `campaign_sales`
- `category_profitability`

### SQL Templates To Add

- Distinct customer count by demographic/geography.
- Channel sales by date/category.
- Multi-channel sales union.
- Channel refunds by return reason.
- Return-adjusted channel sales.
- Top-N categories/products/brands.
- Campaign performance.
- Stock by fulfillment center and product.
- Cohort first-purchase year.
- Retention between two years.
- Fiscal-period aggregation.

### Definitions To Add

- Customer identity by channel.
- Billing customer vs shipping customer.
- Current address vs transaction address.
- Sale date vs shipping date vs return date.
- Revenue, gross sales, net paid, net profit.
- Return rate.
- Active customer.
- Retained customer.
- Popular product.
- Campaign performance.

## Eval Runner Architecture

```text
EvalSuiteLoader
  loads gold YAML, validates schema refs, executes ground truth if needed

EvalRunner
  loops model profiles x cases x modes
  invokes UAT/agent runtime
  captures LangGraph stream trace

TraceAnalyzer
  extracts tool calls, SQL, table refs, column refs, final answer, token usage

SQLJudge
  parse safety, scoped refs, execution, answer comparison, required refs

ToolUseJudge
  required tools, call order, failed-tool handling, unnecessary tool penalty

GroundingJudge
  required metrics/defaults/templates retrieved and followed

EntityMappingJudge
  expected business term -> schema mapping checks

LLMJudge
  optional only for semantic explanation, caveat quality, subjective mapping

EvalReporter
  writes per-case JSON, summary JSON, markdown report, leaderboard table
```

## Release Gates

First internal benchmark gates:

- Overall deterministic pass rate >= 80%.
- Simple cases >= 95%.
- Medium cases >= 85%.
- Hard cases >= 65%.
- Ambiguity behavior >= 80%.
- Hallucinated table rate = 0%.
- Hallucinated column rate <= 2%.
- Unsafe SQL rate = 0%.
- Required join evidence coverage >= 95%.
- Required grounding coverage >= 85%.
- Cost per correct answer lower than Sonnet baseline for Qwen/Haiku-class model on simple and medium cases.

These numbers are initial gates, not marketing claims.

## Implementation Phases

### Phase 1: Design And Gold Schema

- Finalize eval YAML schema.
- Add `evals/gold/retail_pod.analytics.retail_analytics.yaml` with 10 cases first.
- Add deterministic validators.
- Add summary report format.

### Phase 2: Grounding Expansion

- Expand business grounding metrics/templates/defaults.
- Validate all referenced tables, columns, joins, and values.
- Add holdout behavior so eval cases are removed from grounding at runtime.

### Phase 3: 100 Gold Cases

- Generate candidate SQL with Sonnet 4.6.
- Execute all ground truth SQL.
- Review and lock expected answers.
- Add paraphrase groups and ambiguity cases.

### Phase 4: Eval Runner

- Run all cases across:
  - `anthropic_sonnet_46`
  - `bedrock_sonnet_46_global`
  - `anthropic_haiku_45`
  - `bedrock_haiku_45_global`
  - `bedrock_qwen3_next_80b_a3b_ap_south_1`
  - `bedrock_mantle_qwen3_next_80b_a3b_ap_south_1`
- Store per-case traces and scores.
- Produce a model comparison report.

### Phase 5: Ablations

- With business grounding vs without.
- With join graph vs without.
- With SQL templates vs without.
- Streaming vs invoke.
- Qwen runtime vs Qwen Mantle.

### Phase 6: Continuous Eval

- Every new grounding change runs affected eval cases.
- Every agent prompt/tool change runs smoke, 10-case quick suite, and nightly 100-case suite.
- Every learned correction becomes a candidate eval case.

## Open Design Questions

- Should verified SQL be available to the agent in production mode but hidden in eval mode? Recommended: yes.
- Should exact SQL match ever be required? Recommended: only for template-binding tests, not general NL2SQL.
- Should Sonnet-generated gold SQL be trusted automatically? Recommended: no; it must execute and pass critique.
- Should Qwen be judged as primary low-cost model? Recommended: yes, because it already passed streaming and tool UAT.
- Should Bedrock Anthropic be included while account use-case status is blocked? Recommended: include profiles but mark provider state as unavailable until AWS setup is complete.

## First Next Step

Implement the eval skeleton with 10 cases first, not 100. The 10-case suite should cover:

- 2 simple customer/geography
- 2 online purchase/product/date
- 1 store purchase
- 1 refund
- 1 campaign
- 1 stock
- 1 multi-channel profitability
- 1 ambiguity/clarification

Once the skeleton catches and explains failures correctly, scale to 100.
