# Agent Harness, Cognition, And Evaluation Roadmap

Date: 2026-06-07

## Thesis

DiracData should make data agents cheaper and more trustworthy by pushing intelligence into the harness, not by asking a frontier model to reason from scratch every time.

The product claim is not only "better prompts." The claim is:

- Learning converts a scoped data pod into reusable semantic and operational context.
- Cognitive middleware learns from verified interactions and compresses repeated work into reusable pathways.
- A truth compiler verifies SQL plans, result shape, join semantics, metric definitions, and answer support.
- Business grounding gives the agent stable definitions, glossary terms, SQL templates, and ground-truth SQLs for concepts such as active user, retained user, revenue, cohort, funnel, channel, customer, and order.
- Evals prove whether the same harness lets smaller models answer correctly with fewer tokens and lower cost.

The economic test is simple: for the same question set, DiracData should improve correctness, reduce retries, reduce thinking/tool tokens, and lower `cost_per_correct_answer`.

## Cost Anchor

Pricing changes, so eval reports should snapshot model prices at run time.

Current public anchor points:

- Claude Sonnet 4.6 and 4.5: $3 per million input tokens and $15 per million output tokens.
- Claude Haiku 4.5: $1 per million input tokens and $5 per million output tokens.
- Amazon Bedrock Qwen pricing page currently lists Qwen3 Coder 30B A3B in the captured pricing table at $0.1545 per million input tokens and $0.6180 per million output tokens for the shown Standard tier, with Flex and Batch shown at $0.0773 and $0.3090.
- Amazon Bedrock's Qwen model documentation lists Qwen3-Coder-30B-A3B-Instruct and Qwen3 Coder 480B A35B Instruct. It does not show a Qwen3 Coder 72B SKU in the current Bedrock model list.

Sources:

- https://platform.claude.com/docs/en/about-claude/pricing
- https://aws.amazon.com/bedrock/pricing/
- https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards-qwen.html

## Core Measurement Contract

Every answer run should emit an eval record:

```json
{
  "question_id": "retail_001",
  "question": "count all male customers from california",
  "model_provider": "anthropic",
  "model": "claude-haiku-4-5-20251001",
  "harness_profile": "diracdata_agent_v1",
  "correct": true,
  "expected_answer": 936,
  "actual_answer": 936,
  "required_tables": ["clients", "client_profiles", "addresses"],
  "required_joins_verified": true,
  "required_metrics_used": [],
  "clarification_required": false,
  "clarification_asked": false,
  "input_tokens": 0,
  "output_tokens": 0,
  "tool_call_count": 0,
  "sql_execution_count": 0,
  "latency_ms": 0,
  "estimated_cost_usd": 0.0,
  "warnings": []
}
```

Primary metrics:

- `accuracy`: exact answer correctness for deterministic questions.
- `semantic_accuracy`: correct business interpretation when exact numeric answer is not enough.
- `cost_per_correct_answer`: total model cost divided by correct answers.
- `tokens_per_correct_answer`: total input plus output tokens divided by correct answers.
- `latency_per_correct_answer`: time to correct answer.
- `tool_efficiency`: tool calls per correct answer.
- `join_evidence_coverage`: percent of SQL joins backed by join discovery.
- `business_grounding_coverage`: percent of business terms resolved to glossary definitions, metric definitions, SQL templates, or default policies.
- `clarification_precision`: asks only when ambiguity matters.
- `trace_compression_gain`: token reduction from injected cognitive context.

## Recommended Order

The correct order is semantic contract first, then full evals.

The eval harness should exist early as a thin measurement skeleton, but serious model comparisons should wait until the agent has enough grounded context to interpret business questions consistently. Otherwise we are evaluating whether each model guesses the same column meaning, not whether DiracData's harness makes smaller models accurate.

Recommended sequence:

1. Business grounding layer: YAML glossary, definitions, metrics, SQL templates, defaults, and ground-truth SQLs.
2. Model factory: provider-neutral model selection across Anthropic, Bedrock, and future providers.
3. Gold eval set and eval runner: 100 retail analytics questions with broad SQL and answer coverage.
4. Prompt scaffolding and clarification middleware.
5. Truth compiler.
6. Cognitive middleware.
7. Vendor benchmark harness.

This order keeps us honest without blocking learning. Full evals become meaningful only after business meaning has a stable YAML contract. The first implementation target is not the eval framework; it is the business grounding layer that will make evals deterministic.

## Epic 1: Business Grounding Layer

### PRD

Problem:

Business terms are not always table or column names. "Active user," "retained user," "net revenue," and "conversion" need stable definitions and SQL templates.

User value:

- The agent answers with company-approved business definitions.
- Eval cases can assert whether the correct definition, SQL template, or default policy was used.
- Smaller models receive compact grounded plans instead of rediscovering business logic.

MVP scope:

- YAML artifact for glossary, definitions, metrics, SQL templates, default policies, and ground-truth SQLs.
- Business term search tool.
- Metric and definition retrieval tools.
- SQL template retrieval and parameter binding.
- Learning-time validation that referenced tables, columns, and joins exist.

Out of scope:

- Full semantic layer replacement for dbt/LookML/MetricFlow.
- Automatic metric invention.
- Cross-company metric standardization.
- Full eval framework.

### Design

Business grounding files should be customer-editable YAML, not generated-only JSON.

```yaml
version: 1
scope:
  catalog: retail_pod
  database: analytics
  schema: retail_analytics

glossary:
  - term: active customer
    synonyms: [active user, active shopper]
    definition: A customer with at least one completed purchase in the selected period.
    default_time_basis: sale date

metrics:
  - id: online_jewelry_customers
    name: Online jewelry customers
    grain: customer
    synonyms: [jewelry shoppers online, online jewelry buyers]
    sql_template_id: count_distinct_online_billing_clients
    required_filters:
      - field: merchandise.category
        operator: "="
        value: Jewelry

sql_templates:
  - id: count_distinct_online_billing_clients
    description: Count unique billing clients from online purchases.
    parameters:
      - name: year
        type: integer
        required: false
      - name: state
        type: string
        required: false
    sql: |
      SELECT COUNT(DISTINCT op.billing_client_ref) AS customer_count
      FROM online_purchases op
      JOIN clients c ON op.billing_client_ref = c.client_record
      {{ joins }}
      WHERE 1 = 1
      {{ filters }}

defaults:
  - id: customer_state_means_current_address
    applies_to: [customer from state, customers in state]
    default: clients.current_address_ref
    alternatives:
      - online_purchases.billing_address_ref
      - online_purchases.shipping_address_ref
    ask_user_when: User explicitly mentions billing, shipping, delivery, or order destination.
```

Implementation plan:

1. Define business grounding YAML schema.
2. Seed the retail analytics grounding YAML with glossary terms, common definitions, SQL templates, and defaults.
3. Add validation against learned schema, active join graph, and profile values.
4. Store validated grounding artifacts in the active learning context.
5. Add tools for business term search, metric definition lookup, glossary lookup, and SQL template retrieval.
6. Teach the agent prompt to prefer grounding definitions over schema guessing.
7. Keep eval framework implementation paused until grounding exists.

## Epic 2: Model Factory And Provider Selection

### PRD

Problem:

We need to switch models and providers from CLI/API without changing agent code. This must exist before broad evals so the same grounded harness can be tested against Sonnet, Haiku, Bedrock Qwen, and future providers.

User value:

- Design partners can choose cost/performance profiles.
- DiracData can prove that better harnessing lets cheaper models work.
- Bedrock, Anthropic, and future providers can be evaluated consistently.

MVP scope:

- Provider-neutral model factory.
- CLI model/provider overrides.
- Anthropic first-party and AWS Bedrock support.
- Per-model pricing metadata.
- Separate model profiles for learning, scaffolding, answering, and reflection.

Out of scope:

- Fine-tuning.
- Hosted model marketplace UI.
- Full eval runner.

### Design

Provider config:

```yaml
models:
  - id: anthropic_haiku_45
    provider: anthropic
    model: claude-haiku-4-5-20251001
    roles: [scaffolding, answering]
    pricing:
      input_per_million: 1.0
      output_per_million: 5.0
      captured_at: "2026-06-07"

  - id: bedrock_qwen3_coder_30b
    provider: bedrock
    model: qwen.qwen3-coder-30b-a3b-v1:0
    region: us-east-1
    roles: [scaffolding, answering]
    pricing:
      input_per_million: 0.1545
      output_per_million: 0.6180
      captured_at: "2026-06-07"
```

Implementation plan:

1. Create model registry YAML.
2. Add provider-neutral model factory.
3. Support separate environment settings for learning, scaffolding, answering, and reflection.
4. Support CLI overrides for provider, model, region, pricing profile, and role.
5. Capture provider token usage when available.
6. Add fallback estimated tokenization when exact usage is unavailable.

## Epic 3: Gold Eval Set And Model Evals

### PRD

Problem:

We cannot compare models or vendors until we have a gold retail analytics question set with deterministic ground-truth answers, expected SQL patterns, and broad complexity coverage.

User value:

- The team can compare Sonnet, Haiku, Bedrock Qwen, and future providers on the same workload.
- The harness can prove whether grounding plus cheaper models beats frontier-model guessing.
- Regressions become visible.

MVP scope:

- 100 retail analytics gold questions.
- Wide complexity coverage: scalar counts, filtered metrics, joins, date filters, address-role ambiguity, channel comparisons, group-bys, top-k, ratios, returns, inventory, and multi-step questions.
- Ground-truth SQL and actual answer for every question.
- Required definitions, templates, joins, filters, and default policies.
- Eval runner across the model factory.

Out of scope:

- External vendor benchmark automation.
- LLM-as-judge as primary correctness.
- Synthetic questions without verified SQL answers.

### Design

Gold eval case shape:

```yaml
id: retail_az_female_jewelry_2002
question: how many female customers shopped jewelry online in 2002 and were also from Arizona state?
complexity: multi_join_filtered_count
expected_answer:
  type: integer
  value: 18
ground_truth_sql: |
  SELECT COUNT(DISTINCT op.billing_client_ref) AS customer_count
  FROM online_purchases op
  JOIN merchandise m ON op.merchandise_ref = m.merchandise_record
  JOIN calendar_days cd ON op.sale_calendar_day_ref = cd.calendar_day_record
  JOIN clients c ON op.billing_client_ref = c.client_record
  JOIN client_profiles cp ON c.current_client_profile_ref = cp.client_profile_record
  JOIN addresses a ON c.current_address_ref = a.address_record
  WHERE cp.gender = 'F'
    AND m.category = 'Jewelry'
    AND cd.year = 2002
    AND a.state = 'AZ'
required_grounding:
  defaults:
    - customer_state_means_current_address
required_tables:
  - online_purchases
  - merchandise
  - calendar_days
  - clients
  - client_profiles
  - addresses
required_join_edges:
  - [online_purchases.merchandise_ref, merchandise.merchandise_record]
  - [online_purchases.sale_calendar_day_ref, calendar_days.calendar_day_record]
  - [online_purchases.billing_client_ref, clients.client_record]
  - [clients.current_client_profile_ref, client_profiles.client_profile_record]
  - [clients.current_address_ref, addresses.address_record]
```

Implementation plan:

1. Create the 100-question coverage plan.
2. Generate and verify ground-truth SQL against retail analytics DuckDB/MinIO data.
3. Store actual expected answers with the SQL.
4. Build deterministic checks for answer values, required tables, joins, filters, and grounding IDs.
5. Run model matrix through the model factory.
6. Report accuracy, cost, tokens, latency, and grounding coverage.

## Epic 4: Prompt Scaffolding And Clarification

### PRD

Problem:

User questions are often underspecified. "Count all male customers from California" might mean current address, billing address, shipping address, active customers only, historical customers, unique accounts, or purchase participants.

User value:

- The agent asks useful clarifying questions only when ambiguity changes the result.
- When ambiguity is tolerable, the agent states the default assumption.
- Business users get safer answers without needing SQL knowledge.

MVP scope:

- Schema summary generated after learning.
- Ambiguity detector for identity, time, address role, channel, metric grain, active/inactive status, and date basis.
- Clarification policy with "ask vs proceed with caveat."
- Prompt context scaffold that injects defaults and known ambiguous terms.
- A cheap scaffolding model call that turns the user question into a structured question frame.
- Early routing hints for whether the main answer can use a smaller model or needs a stronger model.

Out of scope:

- Multi-turn dashboard requirement gathering.
- Long interviews before every answer.

### Design

Learning should emit:

```json
{
  "schema_summary": {
    "identity_fields": ["billing_client_ref", "shipping_client_ref", "client_record"],
    "address_roles": ["current_address_ref", "billing_address_ref", "shipping_address_ref"],
    "date_roles": ["sale_calendar_day_ref", "shipping_calendar_day_ref"],
    "common_ambiguities": [
      {
        "term": "customer from a state",
        "default": "client current address",
        "alternatives": ["billing address", "shipping address"]
      }
    ]
  }
}
```

The scaffolding middleware should run before the main answer agent.

Input:

- Raw user question.
- Learned schema summary.
- Metric registry.
- Active join graph.
- Business context and glossary.
- Optional cognitive memory packets.

Output:

```json
{
  "question_frame": {
    "raw_question": "count all male customers from california",
    "business_intent": "count customers by gender and state",
    "metric_candidates": [],
    "entities": ["customers"],
    "filters": [
      {"term": "male", "resolved_field": "client_profiles.gender", "value": "M"},
      {"term": "california", "resolved_field": "addresses.state", "value": "CA"}
    ],
    "grain": {
      "selected": "customer",
      "confidence": "high"
    },
    "defaults_applied": [
      {
        "term": "customer from state",
        "default": "current client address",
        "alternatives": ["billing address", "shipping address"]
      }
    ],
    "ambiguities": [
      {
        "type": "address_role",
        "question": "Should California mean current address, billing address, or shipping address?",
        "must_ask": false,
        "reason": "Default policy says customer residence means current address."
      }
    ],
    "required_context": {
      "tables": ["clients", "client_profiles", "addresses"],
      "join_edges": [
        ["clients.current_client_profile_ref", "client_profiles.client_profile_record"],
        ["clients.current_address_ref", "addresses.address_record"]
      ],
      "profile_values": [
        "client_profiles.gender",
        "addresses.state"
      ]
    },
    "route": {
      "recommended_model_class": "small",
      "reason": "Grounded scalar count with known filters and known joins."
    }
  }
}
```

The scaffolding model should be cheaper than the main answer model. It should not execute SQL and should not invent schema. It should classify intent, detect ambiguity, retrieve compact context, and decide whether to ask a question or proceed with explicit defaults.

Future dynamic router:

- `template`: metric definition and SQL template are sufficient.
- `small_model`: grounded scalar question with known tables, joins, filters, and values.
- `strong_model`: ambiguous, multi-hop, new metric, weak schema evidence, or prior failed attempt.
- `ask_user`: ambiguity materially changes the answer and no default policy is available.

Implementation plan:

1. Generate schema summary after learning from descriptions, profiles, joins, and business context.
2. Define `QuestionFrame`.
3. Add scaffolding middleware before the main agent.
4. Add a cheap model profile for scaffolding.
5. Add `schema_summary_tool`.
6. Add ambiguity policy to system prompt.
7. Add eval cases where ambiguity should trigger a question.
8. Add eval cases where defaults should be used with caveats.
9. Record scaffolding output in eval traces so model comparisons are explainable.

## Epic 5: Truth Compiler

### PRD

Problem:

An agent can write SQL that runs and still be wrong. The truth compiler should make correctness inspectable before the final answer.

User value:

- Fewer silent wrong answers.
- Clear caveats when evidence is weak.
- A reusable trace that can become learning material.

MVP scope:

- SQL static checks.
- Join evidence checks.
- Metric grounding checks.
- Filter value checks.
- Result shape checks.
- Optional model reflection after deterministic checks.

Out of scope:

- Full SQL optimizer.
- Formal proof of query equivalence.

### Design

Compiler stages:

1. Intent frame: metric, dimensions, filters, grain, time basis.
2. Evidence frame: tables, columns, profiles, joins, metrics.
3. SQL plan: selected tables, join graph, filters, aggregation.
4. Static checks: read-only SQL, known tables, known columns, expected joins.
5. Execution checks: row counts, uniqueness, filter selectivity, final result shape.
6. Semantic checks: answer matches SQL output and caveats reflect uncertainty.

Implementation plan:

1. Introduce structured `AnswerPlan`.
2. Parse generated SQL into join/filter/table references.
3. Verify every join against active join graph or runtime recovery.
4. Verify filters against profile values.
5. Execute final SQL and compare answer text to result.
6. Store compiler trace in eval artifacts.

## Epic 6: Cognitive Middleware

### PRD

Problem:

The agent repeats expensive reasoning across similar questions. It should learn from verified interactions and inject compact context on future turns.

User value:

- Smaller models perform better because they receive learned shortcuts.
- Repeated business questions become faster and cheaper.
- Analysts can inspect what the system learned and disable bad patterns.

MVP scope:

- Interaction trace capture.
- Verified pattern extraction after truth compiler success.
- Cognitive memory store for schema, joins, SQL patterns, grounding usage, ambiguity decisions, and failed paths.
- Retrieval and injection middleware before agent invocation.
- Eval comparison with middleware on/off.

Out of scope:

- Unbounded conversation memory.
- Learning from failed or unverified answers as trusted knowledge.
- Invisible mutation without audit trail.

### Design

Memory lifecycle:

```text
observe -> verify -> extract -> consolidate -> retrieve -> inject -> measure
```

Memory packet shape:

```json
{
  "packet_id": "pattern_online_jewelry_by_state_year",
  "packet_type": "sql_pattern",
  "scope": {
    "catalog": "retail_pod",
    "database": "analytics",
    "schema": "retail_analytics"
  },
  "trigger_terms": ["jewelry", "online", "state", "year"],
  "content": {
    "default_tables": ["online_purchases", "merchandise", "calendar_days", "clients", "addresses"],
    "join_edges": [
      ["online_purchases.merchandise_ref", "merchandise.merchandise_record"],
      ["online_purchases.sale_calendar_day_ref", "calendar_days.calendar_day_record"]
    ],
    "sql_skeleton": "..."
  },
  "confidence": "high",
  "source_eval_ids": ["retail_az_female_jewelry_2002"],
  "usage_count": 0,
  "last_verified_at": "2026-06-07T00:00:00Z"
}
```

Implementation plan:

1. Store complete answer traces.
2. Extract verified patterns only after truth compiler passes.
3. Add retrieval by semantic terms, tables, joins, grounding IDs, and SQL template IDs.
4. Inject compact cognitive context before the agent run.
5. Record which packets were injected.
6. Measure token/cost/accuracy impact with middleware on/off.
7. Add a memory debugger CLI to show retrieved and injected packets.

## Epic 7: Vendor Benchmark Harness

### PRD

Problem:

Eventually, DiracData must be compared against external systems such as Databricks Assistant/Cortex Analyst-like products on the same data and questions.

User value:

- Clear competitive evidence.
- Design partners can see where DiracData is better, worse, or different.
- The product can avoid vague claims.

MVP scope:

- Vendor-neutral benchmark spec.
- Same dataset, same business context, same question set.
- Capture answer, SQL if available, latency, and cost if available.
- Deterministic scoring where possible.

Out of scope:

- Deep reverse engineering of vendor internals.
- Claims without reproducible setup.

Implementation plan:

1. Define benchmark protocol.
2. Add baseline DiracData reports first.
3. Add "manual external answer import" CSV for vendor outputs.
4. Score external outputs against the same deterministic eval cases.
5. Later automate vendor adapters only when APIs are available and permitted.

## Release Gates

No feature should be called successful unless it passes a release gate.

Suggested first gate:

- Business grounding YAML exists for the retail analytics schema.
- YAML includes glossary terms, business definitions, SQL templates, defaults, and ground-truth SQL examples.
- Every referenced table, column, join, and profile value validates against active learned artifacts.
- Grounding tools can retrieve definitions and SQL templates by natural-language terms.
- Model factory can run at least Anthropic Sonnet, Anthropic Haiku, and one Bedrock model through the same agent interface.
- 100 gold eval questions exist only after the grounding layer and model factory are in place.

## Immediate Next Design Decisions

1. What glossary terms and definitions should be seeded first for retail analytics?
2. Which SQL templates are canonical enough to ground the first agent workflows?
3. What default policies should exist for customer identity, address role, date role, channel, and active/retained status?
4. Which Bedrock region and model list should become the first model-factory target?
5. What coverage taxonomy should the later 100-question gold eval set use?
