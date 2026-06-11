# Business Grounding Layer

Date: 2026-06-07

## Purpose

The business grounding layer is the contract between natural-language business questions and executable SQL.

It should not be limited to metrics. It includes:

- Business glossary
- Definitions
- Metrics
- SQL templates
- Default interpretation policies
- Ground-truth SQL examples
- Caveats and trust notes

This layer makes later evals deterministic. Without it, different models may choose different valid interpretations for terms like "customer," "active," "retained," "from California," "online," or "sales."

## Product Requirements

### User

The first user is a business user or analyst asking questions over a scoped data pod.

### Problem

Natural language contains business meaning that is not present in column names alone.

Examples:

- "Active customer" needs a time window and activity definition.
- "Retained customer" needs a cohort definition and return-period definition.
- "Customer from California" needs an address-role policy.
- "Online jewelry shoppers" needs channel, product category, customer grain, and identity policy.
- "Revenue" needs gross/net/discount/tax/return treatment.

If these meanings are left to the model, evals become subjective and smaller models are unfairly penalized for not guessing hidden business defaults.

### Goals

- Store customer-approved business definitions in YAML.
- Make business terms retrievable by agent tools.
- Bind business terms to SQL templates where possible.
- Validate every referenced table, column, join, and profile value against learned artifacts.
- Give the scaffolding layer compact grounded context.
- Give evals deterministic expected interpretation and ground-truth SQL.

### Non-Goals

- Replace dbt, LookML, MetricFlow, or a full semantic layer.
- Auto-invent official business definitions.
- Require every question to use a predefined metric.
- Run multi-model evals before the grounding layer exists.

## File Layout

Proposed source YAML:

```text
conf/business_grounding/{catalog}.{database}.{schema}.yaml
```

Example:

```text
conf/business_grounding/retail_pod.analytics.retail_analytics.yaml
```

Validated active artifact:

```text
artifacts/learning/{catalog}/{database}/{schema}/active/grounding/business_grounding.yaml
artifacts/learning/{catalog}/{database}/{schema}/active/grounding/business_grounding.json
```

The YAML is customer-editable. The JSON is a normalized artifact for tools.

## YAML Schema

```yaml
version: 1

scope:
  catalog: retail_pod
  database: analytics
  schema: retail_analytics

glossary:
  - id: customer
    term: customer
    synonyms: [client, shopper, buyer]
    definition: A person represented by a client account.
    primary_table: clients
    primary_key: clients.client_record
    caveats:
      - Purchase facts may contain billing and shipping customer roles.

definitions:
  - id: active_customer
    term: active customer
    synonyms: [active user, active shopper]
    definition: A customer with at least one completed purchase in the selected period.
    default_time_basis: sale date
    default_identity: billing customer
    sql_template_id: active_customers_by_period
    caveats:
      - The template currently treats any purchase row as completed because the retail schema does not expose order status.

defaults:
  - id: customer_state_means_current_address
    applies_to:
      - customer from state
      - customers in state
      - shoppers from state
    default:
      field: clients.current_address_ref
      meaning: Customer's current residential or account address.
    alternatives:
      - field: online_purchases.billing_address_ref
        meaning: Billing address on the online purchase.
      - field: online_purchases.shipping_address_ref
        meaning: Shipping destination for the online purchase.
    ask_user_when:
      - User mentions billing.
      - User mentions shipping, delivery, destination, or shipped to.
      - The answer materially depends on order-specific address instead of customer residence.

metrics:
  - id: online_jewelry_customers
    name: Online jewelry customers
    synonyms:
      - online jewelry shoppers
      - jewelry buyers online
    definition: Unique billing customers with at least one online purchase where merchandise category is Jewelry.
    grain: customer
    sql_template_id: count_distinct_online_billing_clients
    required_filters:
      - field: merchandise.category
        operator: "="
        value: Jewelry
    required_join_edges:
      - [online_purchases.merchandise_ref, merchandise.merchandise_record]
    caveats:
      - Uses billing customer identity unless the user asks for shipping recipient.

sql_templates:
  - id: count_distinct_online_billing_clients
    name: Count distinct online billing customers
    description: Counts unique billing customers from online purchase rows with optional date, state, demographic, and merchandise filters.
    grain: customer
    parameters:
      - name: year
        type: integer
        required: false
      - name: state
        type: string
        required: false
      - name: gender
        type: string
        required: false
      - name: merchandise_category
        type: string
        required: false
    required_tables:
      - online_purchases
      - clients
    optional_tables:
      - calendar_days
      - addresses
      - client_profiles
      - merchandise
    required_join_edges:
      - [online_purchases.billing_client_ref, clients.client_record]
    sql: |
      SELECT COUNT(DISTINCT op.billing_client_ref) AS customer_count
      FROM online_purchases op
      JOIN clients c ON op.billing_client_ref = c.client_record
      {{ optional_joins }}
      WHERE 1 = 1
      {{ filters }}

ground_truth_sql:
  - id: gt_online_female_jewelry_customers_az_2002
    question: how many female customers shopped jewelry online in 2002 and were also from Arizona state?
    expected_answer:
      type: integer
      value: 18
    uses:
      metrics:
        - online_jewelry_customers
      defaults:
        - customer_state_means_current_address
    sql: |
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
```

## Validation Rules

The loader must validate:

- Scope matches current `catalog/database/schema`.
- Referenced tables exist in the active catalog.
- Referenced columns exist in learned schema/profile artifacts.
- Referenced join edges exist in active `joinable_pairs.jsonl` or can be runtime-validated.
- Referenced profile values exist or are marked as weak evidence.
- SQL templates are read-only.
- SQL templates reference only scoped tables.
- Ground-truth SQL executes successfully before it is used in evals.
- Expected answer matches ground-truth SQL output.
- IDs are unique within each section.

The validator should fail closed. Bad grounding should not silently become agent context.

## Tools

Initial tools:

- `business_term_search_tool(query, limit)`: search glossary, definitions, metrics, templates, and defaults.
- `get_business_definition_tool(id_or_term)`: return a definition or glossary item.
- `get_metric_definition_tool(metric_id)`: return metric definition, synonyms, grain, filters, joins, caveats.
- `get_sql_template_tool(template_id)`: return SQL template metadata and template body.
- `get_default_policy_tool(policy_id_or_term)`: return default interpretation and when to ask the user.

The tools should return compact JSON. They should not dump the whole YAML by default.

## Agent Use

The main agent should prefer this order:

1. Business grounding definition or SQL template.
2. Learned schema descriptions and profiles.
3. Join graph.
4. Runtime recovery.
5. Free-form SQL reasoning only when the grounded layers are insufficient.

The agent should cite grounding caveats in the final answer when the caveat affects interpretation.

## Relationship To Scaffolding

The scaffolding middleware consumes the business grounding layer.

It should transform:

```text
count all male customers from california
```

into:

```yaml
business_intent: count customers by gender and state
resolved_terms:
  - term: customer
    grounding_id: customer
  - term: male
    field: client_profiles.gender
    value: M
  - term: california
    field: addresses.state
    value: CA
defaults_applied:
  - customer_state_means_current_address
required_tables:
  - clients
  - client_profiles
  - addresses
route:
  recommended_model_class: small
```

That question frame is what lets cheaper models succeed.

## Relationship To Evals

The eval framework should wait until:

- Grounding YAML exists.
- Grounding validator passes.
- Model factory can choose providers/models.
- A gold question can point to required grounding IDs and ground-truth SQL.

Only then are multi-model evals fair.

## Initial Retail Analytics Seed Scope

Suggested first grounding set:

Glossary:

- customer / client / shopper
- order / purchase
- item / merchandise / product
- online purchase
- store purchase
- mail order purchase
- return / refund
- current address
- billing address
- shipping address
- sale date
- shipping date

Definitions:

- active customer
- retained customer
- online customer
- store customer
- jewelry customer
- returning customer
- customer from state

Metrics:

- distinct customers
- online customers
- store customers
- mail-order customers
- online jewelry customers
- gross sales amount
- net sales amount
- total return amount
- return rate
- average order line value
- inventory on hand

SQL templates:

- count distinct customers by channel
- count distinct online customers with optional filters
- sales by year/category/channel
- return amount by reason/channel
- customer count by state/gender/year
- top merchandise categories by revenue
- stock level by item/date/fulfillment center

Default policies:

- Customer state defaults to current client address.
- Online customer identity defaults to billing client.
- Purchase date defaults to sale calendar date.
- Product category uses merchandise category.
- Gender uses current client profile unless question asks historical transaction profile.
- Revenue must specify gross/net when ambiguity matters; otherwise use gross with caveat.

## Implementation Plan

1. Create the YAML schema and seed retail analytics YAML.
2. Build a parser and validator.
3. Normalize validated YAML into active artifacts.
4. Add business grounding tools.
5. Wire tools into the agent.
6. Add focused unit tests for validation and retrieval.
7. Run UAT on a few questions to confirm the agent uses grounding.
8. Only then begin model factory and gold eval set work.

