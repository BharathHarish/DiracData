# DiracData Fintech Lakehouse — Data Harness (AI Data Modeller · Phase 0)

**Status:** Phase 0 signed off. Decisions locked below. Ready for Phase 1.
**Scope:** a self-contained, laptop-scale, fintech lakehouse simulator that produces the
substrate the AI Data Modeller consumes — raw domain events, silver-cleaned tables,
gold analyst tables (partial — see §8B), a realistic query workload with real
execution telemetry, and a lineage graph across all three layers.
**Location:** tool code lives under `data_harness/` (no `src/diracdata/*` touched).
Data lives in MinIO under `lake/fintech/…` — **this is the new fintech schema**,
replacing the retired `lake/fintech_complex/`.

## 0 · Decisions locked (Phase 0 sign-off)

| # | question | decision |
|---|---|---|
| 1 | Silver granularity | 13 tables (not strictly 1:1) — sensible middle ground |
| 2 | Default time model | `wall_clock` — 1 real minute = 1 minute of data |
| 3 | User geo shape | **India-specific fintech** — state/city/language/UPI-heavy rail mix |
| 4 | Nested-type coverage | **Broad** — ~15+ nested columns across raw (see §6.10 catalog) |
| 5 | Query history retention | **Two-tier: 30 days full-detail + 90 days aggregated** (§10) |
| 6 | Storage prefix | Labs data replaces the old fintech_complex — lives under `lake/fintech/` |
| 7 | schemas/raw.yaml | **Faker/distribution spec ONLY** — no PK/FK declarations. Modeller must discover joins from data (composes with S1 behavioural join cards). |

**One-time cleanup at end of Phase 2** (once labs raw has richer nested coverage):
delete `lake/fintech_complex/` and its associated diracdata state, since it
becomes redundant. Held off until then so we don't lose our currently-only rich
nested test surface.

---

## 1 · Objective

Produce, on a single M3 Pro laptop with no cloud dependency, all inputs the AI Data
Modeller needs to reason about a real fintech data platform:

1. **~48 raw (bronze) tables** across 8 domains + reference — the shape a real fintech
   lakehouse has, ~25 nested/complex-type columns spread throughout (§6.10).
2. **~13 silver tables** that clean, dedupe, join lightly, and expose stable grains.
3. **3 baseline gold tables** we ship (obvious BI facts), plus **4 discovery
   targets deliberately left absent** — including the 90-day EMI-lookback
   lending-health table — for the modeller to spot from query_history and
   propose materialising itself. That's the point of the harness. See §8.
4. **A live query workload** across four archetypes (BI · analyst · ops · RCA) that
   hits raw, silver, and gold in realistic proportion. Every query is executed
   through DuckDB, so timings/rows-scanned/bytes-scanned are *real*, not simulated.
   Workload MUST include the 4 discovery-target patterns so signal is present.
5. **A declared lineage graph** (raw → silver → gold), materialised as
   `lineage.json`, that the modeller reads as a first-class artifact. **No PK/FK
   metadata** — modeller discovers joins from data (§11, §Decision #7).

## 2 · Non-goals (explicit)

- No Kafka, Airflow, Iceberg, Spark, dbt. Overkill at this scale, distraction.
- No production streaming. Micro-batch (per-minute or per-tick) is enough.
- No changes to `src/diracdata/*`. This is a *consumer* of the same MinIO fabric, not
  a fork of the agent code.
- No new services beyond MinIO (already up) + DuckDB (already in venv) + Python.
- No modelling logic in this phase. The modeller is Phase 7+; here we build the
  substrate it will train on.

## 3 · Guiding principles

- **Local, lean, disciplined.** ~2 GB additional disk cap, ~2 GB peak RAM. Rolling
  30-day retention. Guarded by disk-budget check in the orchestrator.
- **Time is first-class.** Every raw row carries `_event_ts` and `_ingest_ts`.
  10% of events arrive late (5–30 min lag) — realistic and forces the silver layer
  to be idempotent.
- **DuckDB throughout.** Same engine for generators (writing parquet), silver/gold
  transforms (executing SQL), and workload queries (with `EXPLAIN ANALYZE` capture).
  One binary, one query semantics, no engine drift.
- **Object-store native.** Everything lands as Parquet on MinIO under
  `lake/fintech/{raw|silver|gold|reference|query_history}/…`. DuckDB reads via httpfs.
  Matches the existing DiracData lakehouse pattern.
- **SQL files are the source of truth.** Each silver/gold table is exactly one
  `.sql` file with a header comment declaring sources + grain + description. The
  lineage graph is *derived* from these files — never hand-maintained.
- **Deterministic + resumable.** Seeded Faker. Every generator persists its state
  (last-emitted PK counters, entity pools) so runs are repeatable and stoppable.

## 4 · Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│  data_harness/orchestrator/run.py                                      │
│    modes: one_pass · continuous(--interval) · backfill(--days)         │
└──────────────┬─────────────────────────────────────────────────────────┘
               │ orchestrates ↓
┌──────────────▼─────────────────────────────────────────────────────────┐
│ Stage 1 · REFERENCE     seed 5 static tables (countries, mccs, ...)    │
│ Stage 2 · GENERATE RAW  8 domain generators emit N rows/tick           │
│                          → lake/fintech/raw/<domain>/<table>/date=…/      │
│ Stage 3 · BUILD SILVER  ~13 SQL scripts (topological order from        │
│                          lineage) → lake/fintech/silver/<table>/date=…/   │
│ Stage 4 · BUILD GOLD    7 SQL scripts (topological order)              │
│                          → lake/fintech/gold/<table>/date=…/              │
│ Stage 5 · QUERY WORKLOAD library of parameterised queries executed via │
│                          DuckDB with EXPLAIN ANALYZE; every execution  │
│                          appended to lake/fintech/query_history/date=…/   │
└────────────────────────────────────────────────────────────────────────┘
                                    ↓
                    lake/fintech/lineage.json  (auto-generated from SQL parse)
                                    ↓
                            AI Data Modeller  (Phase 7+)
```

Storage layout (single bucket, prefix-partitioned — no new buckets):

```
lake/
  fintech/                                    # ← the new fintech schema; replaces fintech_complex/
    reference/<table>/*.parquet               # unpartitioned, small, idempotent seed
    raw/<domain>/<table>/date=YYYY-MM-DD/hour=HH/part-*.parquet
    silver/<table>/date=YYYY-MM-DD/part-*.parquet
    gold/<table>/date=YYYY-MM-DD/part-*.parquet
    query_history/date=YYYY-MM-DD/part-*.parquet          # 30-day full detail
    query_history_agg/day=YYYY-MM-DD/part-*.parquet        # 90-day aggregated
    lineage.json                              # single file, auto-regenerated
    generator_state/<domain>.json             # per-generator persistent counters
```

## 5 · Directory layout

```
data_harness/
  PLAN.md                       # this file
  README.md                     # 60-line quickstart
  config.yaml                   # runtime knobs (rates, caps, retention, seeds)
  __init__.py
  common/
    duckdb_conn.py              # canonical DuckDB connection factory (httpfs + secret)
    minio_client.py             # boto3 wrapper
    paths.py                    # centralised S3 URI builders
    logging.py                  # structured logger, one line per action
  generators/
    __init__.py
    base.py                     # BaseGenerator, cadence, state, seeded Faker
    reference.py                # 5 static reference tables
    users.py                    # 4 tables
    merchants.py                # 5 tables
    checkouts.py                # 4 tables
    orders.py                   # 5 tables
    payments.py                 # 7 tables (incl. nested payment_events.attributes)
    lending.py                  # 7 tables (incl. repayment_schedule.installments[])
    adtech.py                   # 6 tables (incl. attribution.touchpoints[])
    risk.py                     # 5 tables
  writers/
    parquet_writer.py           # buffered parquet write to lake/fintech/raw/...
    hive_partitioner.py         # date=YYYY-MM-DD/hour=HH layout helper
  transforms/
    silver/                     # one .sql per silver table (see §7)
      s_users.sql
      s_merchants.sql
      s_orders.sql
      s_payments.sql
      s_loans.sql
      s_repayments.sql
      s_adtech_events.sql
      s_attribution.sql
      s_risk_events.sql
      s_checkout_funnel.sql
      s_settlement.sql
      s_ad_spend.sql
      s_user_activity.sql
    gold/                       # one .sql per gold table (see §8)
      g_merchant_daily.sql
      g_payments_daily.sql
      g_orders_daily.sql
      g_lending_health_daily.sql    # ⭐ 90-day EMI lookback lives here
      g_adtech_daily.sql
      g_user_cohort_daily.sql
      g_unit_economics_daily.sql
    runner.py                   # picks up all .sql, topo-sorts via lineage, executes
    sql_header.py               # parses the header comment block declaring sources/grain
  workload/
    __init__.py
    query_bank/
      bi.py                     # ~40 dashboard templates (repetitive, gold-heavy)
      analyst.py                # ~30 exploration templates (silver+gold, novel)
      ops.py                    # ~20 real-time templates (small time windows)
      rca.py                    # ~10 deep-dive templates (raw+silver cross-domain)
    runner.py                   # picks archetype by weight, parameterises, executes,
                                # captures EXPLAIN ANALYZE, appends to query_history
    telemetry.py                # DuckDB profile-json → flat query_history schema
  orchestrator/
    __init__.py
    lineage.py                  # parse all .sql headers → lineage.json
    dag.py                      # topological sort, dependency checks
    run.py                      # main CLI entrypoint
    guardrails.py               # disk-budget check, RAM check, query kill-switch
  schemas/
    raw.yaml                    # authoritative raw table defs (48 tables)
    silver.yaml                 # silver table defs (13 tables)
    gold.yaml                   # gold table defs (7 tables)
                                # lineage.json lives IN the lake, not here
  scripts/
    seed_reference.py           # one-time load of the 5 reference tables
    pump_once.py                # one batch of raw across all generators
    build_silver.py             # run all silver transforms once, in order
    build_gold.py               # run all gold transforms once, in order
    generate_query_history.py   # run N workload queries, log telemetry
    full_dag.py                 # reference → raw → silver → gold → queries, one pass
    reset.py                    # nuke lake/fintech/* (keeps reference, resets state)
    status.py                   # CLI: row counts, last-run timestamps, disk usage
  outputs/                      # (gitignored) — local scratch, run logs
```

**Rule:** nothing in `data_harness/` imports from `src/diracdata/*`. The two live
side-by-side in the same repo but are functionally independent. The only shared
resource is the MinIO instance (which stores their outputs in disjoint prefixes).

## 6 · Domain map (48 raw tables · 8 domains + reference)

**Key columns are shown for orientation only.** `schemas/raw.yaml` will NOT
declare PKs, FKs, or referential constraints. The data modeller (and existing
S1 join cards) must discover relationships from the data itself. Generators
maintain referential integrity by construction (e.g., orders.merchant_id draws
from the existing merchant pool), but the fact of that relationship is
implicit, not metadata-declared.

**India-specific fintech geography** — all user/merchant locations drawn from
Indian states, cities, PIN codes, languages; payment rails weighted UPI-heavy
(UPI 60%, DC 12%, CC 10%, NB 8%, Wallet 5%, IMPS 3%, NEFT 2%); currencies
primarily INR with a small USD/SGD/AED tail for cross-border. See §6.11.

**Nested-type coverage** (⭐) is broad — repeated across users, merchants,
orders, payments, lending, adtech, checkouts, risk. Full catalog in §6.10.

### 6.1 users (4 tables — rich nested)

| table | grain | key cols | notes |
|---|---|---|---|
| users | user_id | user_id, user_type, signup_time, state, city, language, kyc_status, **devices: LIST\<STRUCT\>** ⭐, **preferences: JSON** ⭐, **kyc_documents: LIST\<STRUCT\>** ⭐, **feature_flags: MAP\<VARCHAR,BOOLEAN\>** ⭐ | India-heavy — Indian states/cities; language ∈ {en, hi, ta, te, mr, bn, kn, gu, ml, pa} |
| user_devices | device_id | device_id, user_id, os, model, first_seen, trusted, **fingerprint: STRUCT** ⭐ | also present flattened as child table — modeller can propose consolidation |
| user_kyc_events | kyc_event_id | kyc_event_id, user_id, event_type, event_time, doc_type, **verification_payload: JSON** ⭐ | append-only |
| user_sessions | session_id | session_id, user_id, session_start, session_end, platform, **event_stream: LIST\<STRUCT\>** ⭐ | nested clickstream inside session |

### 6.2 merchants (5 tables)

| table | grain | key cols | notes |
|---|---|---|---|
| merchants | merchant_id | merchant_id, business_name, mcc_code, onboarded_at, status, tier, state, city, gstin, **contact_persons: LIST\<STRUCT\>** ⭐, **business_registration: STRUCT** ⭐ | India — GSTIN + PAN + state |
| merchant_kyc | kyc_event_id | merchant_id, event_type, event_time, doc_type, **verification_result: STRUCT** ⭐ | onboarding funnel |
| merchant_settlement_config | merchant_id | settlement_speed, settlement_bank_ref, effective_from | SCD-ish |
| merchant_pricing_plans | plan_id | merchant_id, plan_name, mdr_bps, effective_from, effective_to, **rail_overrides: MAP\<VARCHAR,DOUBLE\>** ⭐ | per-rail mdr overrides in a MAP |
| merchant_category_map | mcc_code | mcc_code, category_name, category_group | joins to reference.mcc_codes by value |

### 6.3 checkouts (4 tables)

| table | grain | key cols | notes |
|---|---|---|---|
| checkout_sessions | session_id | session_id, merchant_id, user_id, initiated_at, status, total_amount, **utm: STRUCT** ⭐ | funnel top; utm source/medium/campaign nested |
| checkout_items | item_id | item_id, session_id, sku, qty, unit_price, **variant_attrs: MAP** ⭐ | many-per-session |
| checkout_events | event_id | event_id, session_id, event_type, event_time, **event_payload: JSON** ⭐ | funnel steps, JSON payload |
| checkout_abandonments | session_id | session_id, abandoned_at, last_step, **funnel_steps: LIST\<STRUCT\>** ⭐ | funnel bottom, steps-per-session array |

### 6.4 orders (5 tables — deeply nested)

| table | grain | key cols | notes |
|---|---|---|---|
| orders | order_id | order_id, checkout_session_id, merchant_id, user_id, order_time, order_amount, status, **line_items: LIST\<STRUCT\>** ⭐, **fulfillment: STRUCT with LIST\<STRUCT\> shipments** ⭐, **fraud_signals: STRUCT** ⭐, **metadata: JSON** ⭐ | orders row has BOTH nested line_items AND child order_items table — modeller decides which pattern to use per query |
| order_items | order_item_id | order_item_id, order_id, sku, qty, unit_price, discount | flat child (redundant with orders.line_items — intentional) |
| order_status_history | order_id, changed_at | order_id, status, changed_at, changed_by, **state_transition: STRUCT** ⭐ | SCD-2 |
| fulfillment_events | fulfillment_event_id | fulfillment_event_id, order_id, event_type, event_time, warehouse, **tracking_scans: LIST\<STRUCT\>** ⭐ | shipped/delivered/returned |
| order_notes | note_id | note_id, order_id, note_text, noted_by, noted_at | sparse |

### 6.5 payments (7 tables — very rich nested)

| table | grain | key cols | notes |
|---|---|---|---|
| payment_attempts | attempt_id | attempt_id, order_id, user_id, merchant_id, amount, rail_type, attempted_at, status, **risk_checks: LIST\<STRUCT\>** ⭐, **routing_decision: STRUCT** ⭐ | India rails: UPI 60%, DC 12%, CC 10%, NB 8%, WALLET 5%, IMPS 3%, NEFT 2% |
| payment_events | event_id | event_id, attempt_id, event_type, event_time, **event_attributes: STRUCT** ⭐ (rail-specific), **processor_response: JSON** ⭐ | multiple events per attempt |
| payment_methods | method_id | method_id, user_id, method_type, issuer, added_at, **method_details: STRUCT** ⭐ (variant — upi vpa, card bin, wallet handle) | polymorphic details per method_type |
| refunds | refund_id | refund_id, original_payment_id, refund_amount, refund_time, reason, **reason_metadata: JSON** ⭐ | subset of payments |
| chargebacks | chargeback_id | chargeback_id, original_payment_id, dispute_reason, filed_at, resolved_at, **evidence_docs: LIST\<STRUCT\>** ⭐ | rare |
| settlement_batches | batch_id | batch_id, merchant_id, batch_date, gross_amount, mdr_amount, net_amount, status, **breakdown: LIST\<STRUCT\>** ⭐ (per-rail split) | hourly batch |
| disputes | dispute_id | dispute_id, chargeback_id, evidence_submitted_at, resolution, resolved_at, **timeline: LIST\<STRUCT\>** ⭐ | very rare |

### 6.6 lending (7 tables — the modeller's marquee domain, very rich nested)

| table | grain | key cols | notes |
|---|---|---|---|
| loan_applications | app_id | app_id, user_id, requested_amount, tenure_months, applied_at, status, **applicant_snapshot: STRUCT** ⭐ (kyc/income/credit at app time), **bureau_report: JSON** ⭐ | funnel |
| loans | loan_id | loan_id, app_id, user_id, principal, interest_rate, tenure_months, disbursed_at, status, **risk_snapshot: STRUCT** ⭐, **installments: LIST\<STRUCT\>** ⭐ (denormalised schedule on the loan row) | approved apps; installments ALSO exist as repayment_schedule table |
| loan_disbursements | disbursement_id | disbursement_id, loan_id, disbursed_at, amount, disbursement_method, **payout_details: STRUCT** ⭐ | usually 1:1 with loan |
| repayment_schedule | schedule_id | schedule_id, loan_id, installment_no, due_date, principal_due, interest_due, emi_amount | *contracted* schedule (flat) |
| repayments_actual | repayment_id | repayment_id, loan_id, schedule_id, paid_at, paid_amount, payment_method, **allocation: STRUCT** ⭐ (principal vs interest vs penalty split) | *actual* payments |
| delinquencies | delinquency_id | delinquency_id, loan_id, dpd_bucket, snapshot_date, outstanding, **aging_buckets: STRUCT** ⭐ | daily snapshot |
| credit_bureau_pulls | pull_id | pull_id, user_id, pulled_at, credit_score, bureau, **report_payload: JSON** ⭐ | at application + periodic |

### 6.7 adtech (6 tables)

| table | grain | key cols | notes |
|---|---|---|---|
| ad_campaigns | campaign_id | campaign_id, campaign_name, channel, budget, started_at, ended_at, **targeting: STRUCT** ⭐, **budget_pacing: LIST\<STRUCT\>** ⭐ | 200 campaigns |
| ad_creatives | creative_id | creative_id, campaign_id, creative_type, creative_url, **creative_meta: JSON** ⭐ | many-per-campaign |
| ad_impressions | impression_id | impression_id, campaign_id, creative_id, user_id, shown_at, platform, **placement: STRUCT** ⭐ | highest-volume table |
| ad_clicks | click_id | click_id, impression_id, clicked_at, **click_context: STRUCT** ⭐ | CTR ~ 2% |
| attribution | attribution_id | attribution_id, order_id, **touchpoints: LIST\<STRUCT\>** ⭐, model, attributed_at, **weights: MAP\<VARCHAR,DOUBLE\>** ⭐ | multi-touch, nested |
| ad_spend_daily | campaign_id, spend_date | campaign_id, spend_date, spend_amount, impressions_count, clicks_count, **breakdown: MAP\<VARCHAR,DOUBLE\>** ⭐ | daily rollup |

### 6.8 risk (5 tables)

| table | grain | key cols | notes |
|---|---|---|---|
| risk_events | risk_event_id | risk_event_id, entity_type, entity_id, event_time, rule_id, severity, **evidence: JSON** ⭐ | polymorphic entity |
| rules_fired | firing_id | firing_id, risk_event_id, rule_name, score, action, **rule_inputs: STRUCT** ⭐ | many-per-event |
| fraud_labels | label_id | label_id, entity_id, entity_type, labeled_at, label, **label_reasons: LIST\<VARCHAR\>** ⭐ | ground truth |
| sanctions_screens | screen_id | screen_id, user_id, screened_at, hit_flag, sanction_list, **match_details: STRUCT** ⭐ | KYC step |
| velocity_windows | window_id | window_id, user_id, window_start, window_end, txn_count, txn_amount, **per_rail_breakdown: MAP\<VARCHAR,STRUCT\>** ⭐ | rolling counter |

### 6.9 reference (5 tables, static, idempotent seed)

| table | rows | purpose |
|---|---|---|
| countries | ~250 | ISO country codes + region |
| currencies | ~30 | ISO currency codes + decimals |
| mcc_codes | ~30 | merchant category codes + group |
| holidays | ~500 | country_code × date → holiday_name |
| fx_rates | ~30/day × 90 days | (from, to, date) → rate |

**Total raw: 48 tables.** All landed to `lake/fintech/raw/<domain>/<table>/…`

### 6.10 Nested-type catalog (~25 nested columns across raw)

Coverage exercises every complex-type surface DuckDB supports, deliberately
spread so the modeller and downstream tools (existing S1/S2 substrate) have
broad shape to reason about.

| type | count | example columns |
|---|---|---|
| `LIST<STRUCT>` | ~12 | users.devices, users.kyc_documents, orders.line_items, fulfillment.shipments, payment_attempts.risk_checks, loans.installments, chargebacks.evidence_docs, settlement_batches.breakdown, disputes.timeline, attribution.touchpoints, fulfillment_events.tracking_scans, checkout_abandonments.funnel_steps, session.event_stream |
| `STRUCT` (flat or moderately nested) | ~10 | user_devices.fingerprint, merchants.business_registration, merchant_kyc.verification_result, checkout_sessions.utm, orders.fraud_signals, orders.state_transition, payment_attempts.routing_decision, payment_methods.method_details, loans.risk_snapshot, loan_disbursements.payout_details, repayments_actual.allocation, delinquencies.aging_buckets, ad_campaigns.targeting, ad_impressions.placement, ad_clicks.click_context, rules_fired.rule_inputs, sanctions_screens.match_details |
| `MAP<K,V>` | ~5 | users.feature_flags, checkout_items.variant_attrs, merchant_pricing_plans.rail_overrides, attribution.weights, ad_spend_daily.breakdown, velocity_windows.per_rail_breakdown |
| `JSON` | ~7 | users.preferences, user_kyc_events.verification_payload, checkout_events.event_payload, payment_events.processor_response, refunds.reason_metadata, loan_applications.bureau_report, credit_bureau_pulls.report_payload, risk_events.evidence, ad_creatives.creative_meta |
| **Deeply nested** (STRUCT-of-LIST-of-STRUCT) | 2 | orders.fulfillment (warehouse + shipments[] each with items[]), ad_campaigns.budget_pacing |

**Intentional redundancy** — orders.line_items (nested) exists alongside
order_items (child table); users.devices (nested) exists alongside user_devices
(child table); loans.installments (nested) exists alongside repayment_schedule
(child table). Real fintech lakehouses have this shape because different upstream
systems (relational DB vs event stream vs document store) express the same
entity differently. The modeller must decide per query-pattern which
representation to prefer — and can propose consolidating one away if usage
justifies it.

### 6.11 India-specific fintech geography (§Decision #3)

Applied uniformly to generators:

- **Users**: 50k drawn Zipf-weighted by state (~40% MH + KA + TN, ~30% GJ + UP
  + WB, ~20% remaining top-13, ~10% long-tail rest of India). Cities Zipf per
  state (Mumbai/Bangalore/Delhi-NCR anchors). Languages ∈ {en, hi, ta, te, mr,
  bn, kn, gu, ml, pa} weighted by state.
- **Merchants**: 500 drawn similarly. All carry GSTIN + PAN. `mcc_code`
  spread realistically (food, apparel, electronics, travel, groceries,
  services heavy).
- **Payment rails** (India-realistic): UPI 60% · DC 12% · CC 10% · NB 8% ·
  WALLET 5% · IMPS 3% · NEFT 2%.
- **Currencies**: 95% INR + 5% USD/SGD/AED tail (cross-border merchants).
- **Amounts**: log-normal INR, ticket-size skewed low (median ~₹450, tail to
  ~₹50k).
- **Holidays**: Indian national + regional festival calendar for spend seasonality.
- **PIN codes**: 6-digit, valid state-prefix ranges.

These are Faker localisations + weighted picks — no PII, all synthetic.

## 7 · Silver layer (13 tables — cleaned, deduped, lightly joined)

Silver rules: **exactly one row per natural key**, all timestamps in UTC, all FKs
resolvable, one column-of-record for status/state per entity, small joins done
(dim enrichment), no aggregations, no cross-domain fanouts.

| silver table | grain | reads from (raw) |
|---|---|---|
| s_users | user_id | users + user_kyc_events (latest state) |
| s_merchants | merchant_id | merchants + merchant_settlement_config + merchant_pricing_plans (current) + merchant_category_map |
| s_orders | order_id | orders + order_status_history (current status) + order_items (rolled up) |
| s_payments | attempt_id | payment_attempts + payment_events (terminal event) + refunds (attached) |
| s_loans | loan_id | loans + loan_applications + credit_bureau_pulls (latest) |
| s_repayments | repayment_id | repayments_actual + repayment_schedule (on_time flag, days_late) |
| s_adtech_events | impression_id | ad_impressions + ad_clicks (click-through flag) |
| s_attribution | attribution_id × touchpoint_no | attribution (touchpoints UNNESTed to touchpoint-per-row) |
| s_risk_events | risk_event_id | risk_events + rules_fired (rule details flattened) |
| s_checkout_funnel | session_id | checkout_sessions + checkout_events (aggregated funnel stage) |
| s_settlement | batch_id | settlement_batches + chargebacks (netted into net_amount) |
| s_ad_spend | campaign_id × spend_date | ad_spend_daily (normalised) |
| s_user_activity | user_id × activity_date | user_sessions + orders (aggregated per user-day) |

Each is one SQL file in `transforms/silver/`, with this header:

```sql
-- s_repayments : one row per actual repayment, with on-time flag and days-late
-- grain: repayment_id
-- sources: raw.repayments_actual, raw.repayment_schedule
-- notes: JOIN on schedule_id; days_late = paid_at::date - due_date; on_time := days_late <= 0
CREATE OR REPLACE TABLE silver.s_repayments AS
SELECT ...
```

## 8 · Gold layer — split by ownership

**Key insight (thanks to review):** if we build every gold table ourselves, the
modeller has nothing to discover. Gold is split into two disjoint sets:

### 8A · Baseline gold — WE build (Phase 4)

The obvious, uncontroversial tables any data engineer would ship on day 1.
They exist so the BI archetype has a fast path, and so the modeller sees "here
is the baseline you can compare your proposals against".

| gold table | grain | reads from (silver) | headline metrics |
|---|---|---|---|
| g_merchant_daily | merchant_id × day | s_merchants, s_orders, s_payments, s_settlement | GMV, orders, success_rate, mdr, net_revenue |
| g_payments_daily | rail_type × merchant_type × day | s_payments, s_merchants | attempts, successes, failure_rate, avg_ticket |
| g_orders_daily | merchant_id × day | s_orders, s_checkout_funnel | orders, cancellations, abandonment_rate |

### 8B · Discovery targets — MODELLER must propose (Phase 7+)

These are **deliberately absent**. Their SQL is not written. Their entries are
not in `lineage.json`. Analysts and RCA users hit the underlying silver/raw
patterns repeatedly — the modeller reads `query_history`, spots the repeated
expensive shapes, and **proposes** the gold table (SQL + grain + business
definition + expected cost saving). We (or a human reviewer) then either
approve or reject.

| discovery target (the modeller must invent this name/shape) | approx. grain | signal in query_history |
|---|---|---|
| lending health with 90-day rolling EMI lookback ⭐ | vintage × snapshot_date | RCA queries with correlated 90-day windows against s_loans + s_repayments — expensive, repeated daily |
| adtech performance with attribution + ROAS | campaign × day | analyst queries that UNNEST s_attribution.touchpoints and join s_ad_spend — cross-domain, expensive |
| user cohort retention & LTV | signup_month × current_month | BI/analyst queries computing MoM retention off s_users + s_user_activity + s_orders |
| cross-domain unit economics / contribution margin | merchant × day | rare-but-huge RCA queries joining 5+ silver tables (orders + payments + refunds + ad_spend + fraud) |

**Test of modeller success:** for each of the 4 discovery targets, did it (a)
identify the pattern from query_history alone, (b) propose the right grain, (c)
write correct SQL, (d) estimate a plausible cost saving? Grading harness reads
`lake/fintech/modeller_proposals/*.json` and compares grain + input tables against
a hidden `discovery_targets.yaml` we hold back (never leaks into the harness).

## 9 · The 90-day EMI lookback — the canary discovery case

This is the flagship discovery target. Its properties:

**What the pattern looks like in query_history** (before the modeller acts):

```sql
-- something analysts run repeatedly, ~30x/day, avg 2.5s, ~400 MB scanned
WITH window AS (
  SELECT loan_id,
         SUM(rs.emi_amount)   AS emi_due_90d,
         SUM(ra.paid_amount)  AS emi_paid_90d
  FROM silver.s_loans l
  JOIN silver.s_repayments ra ON ra.loan_id = l.loan_id
  JOIN raw.repayment_schedule rs ON rs.schedule_id = ra.schedule_id
  WHERE rs.due_date BETWEEN DATE '2026-05-13' AND DATE '2026-08-11'
  GROUP BY loan_id
)
SELECT vintage_month, ...
FROM ...
```

Analysts vary the snapshot_date, run daily, always the same shape. Total daily
cost: ~30 queries × 2.5s × 400 MB = 75 s CPU + 12 GB scan / day.

**What the modeller should propose:**

```json
{
  "proposal_id": "prop_20260812_01",
  "kind": "materialise_gold",
  "target_name": "g_lending_90d_health_daily",
  "grain": ["vintage_month", "snapshot_date"],
  "sources": ["silver.s_loans", "silver.s_repayments", "raw.repayment_schedule"],
  "lookback_days": 90,
  "sql_body": "…the CTE that pre-aggregates by vintage × snapshot_date…",
  "evidence": {
    "matched_query_template_hash": "sha1:…",
    "queries_per_day": 30,
    "avg_cost_ms": 2500,
    "avg_scan_bytes": 400_000_000,
    "projected_cost_ms": 8,
    "projected_scan_bytes": 12_000
  },
  "confidence": 0.94
}
```

If the modeller catches this, ships correct SQL, and estimates >100× cost
saving, it has earned its keep. If it doesn't, we know exactly where it
failed — which query patterns didn't rise above the noise, whether the grain
was correctly inferred, whether the SQL is executable, whether cost estimation
was sane.

## 9A · Workload must generate the discovery signal

The modeller can only discover what shows up in `query_history`. The workload
runner therefore ensures each discovery target has enough repetition + cost to
be findable. Concretely, embedded in `workload/query_bank/`:

| template family | archetype | typical count/day | targets |
|---|---|---|---|
| "90-day EMI lookback by vintage" (parameterised on snapshot_date) | rca | ~30 | 90-day EMI target |
| "ROAS by campaign this week" (parameterised on week) | analyst | ~40 | attribution+ROAS target |
| "cohort retention MoM" (parameterised on cohort_month) | bi | ~60 | user cohort target |
| "contribution margin per merchant this week" (5+ table join) | rca | ~8 | unit economics target |

Each of those runs hundreds of times per week across parameter variations. The
signal is guaranteed to be there. The question we're testing is whether the
modeller **finds it and acts on it**.

## 10 · Query history — two-tier, deep training corpus (§Decision #5)

Retention is **two-tier** so the modeller has both breadth (long history for
pattern-frequency signal) and depth (recent full detail for SQL extraction),
without blowing the disk budget.

**Tier A — full detail, 30 days rolling** (~1.5 GB @ 20 queries/tick lean).
Every executed query, full SQL + plan + all telemetry columns. This is what
the modeller reads to *extract candidate SQL* for its proposals.

**Tier B — aggregated summary, 90 days rolling** (~50 MB). Nightly rollup by
`(template_id, day, archetype)` → `n_runs, avg_elapsed_ms, avg_scan_bytes,
p95_elapsed_ms, tables_touched_dominant, layer_mix_dominant`. This is what
the modeller reads to *establish pattern frequency* over a long enough window
to warrant proposing materialisation.

Every query the workload runner submits is executed through DuckDB with
`EXPLAIN ANALYZE`, and one row is appended to
`lake/fintech/query_history/date=YYYY-MM-DD/…`:

```
query_id             sha1-of-normalised-sql + submit_ts nonce
user_id              rotating pool of ~50 synthetic users
user_role            one of: bi_dashboard, analyst, ops, data_scientist, exec
archetype            one of: bi, analyst, ops, rca
submitted_at         actual timestamp of submission
started_at           DuckDB begin
finished_at          DuckDB end
elapsed_ms           finished_at − started_at (millisec)
sql_text             full parameterised query
sql_hash             hash of the *template* (dedupes across parameter values)
template_id          the query_bank template that produced this
tables_touched       list — e.g. ["silver.s_orders", "silver.s_users"]
layer_mix            {"raw": 0, "silver": 2, "gold": 0}   ← key modeller feature
rows_returned        count
rows_scanned         sum across scan operators
bytes_scanned        sum across scan operators
join_shape           {"n_joins": 1, "join_types": ["inner"]}
filters              list of {column, op, value_sample}
group_by_cols        list
aggregations         list of fn names
error                null or exception message
status               "success" | "error" | "killed_budget"
plan_summary         DuckDB EXPLAIN output (truncated to 2 KB)
```

**Realism knobs**:

- Weights across archetypes: BI 50%, analyst 30%, ops 15%, rca 5%.
- Users rotate; each user tends to reuse ~5 templates (Zipf on template popularity).
- Cost distribution should be log-normal — a few big RCA queries, many cheap BI.
- The workload runner writes ~100 queries per tick (once per minute in continuous
  mode; on-demand in one-pass mode).

Query history is itself queryable as a parquet dataset — the modeller reads it
via DuckDB the same way as anything else.

## 11 · Lineage JSON — auto-generated, single source of truth

**Scope constraint (§Decision #7):** lineage.json captures raw table
*existence* (name, columns, row_count, size) but declares **NO primary or
foreign keys**. Only silver→raw and gold→silver *edges* (which the modeller
needs to build a topological picture) and per-transform declared *sources*.
Join relationships between raw tables must be discovered from the data — by
the existing S1 behavioural-join-cards pipeline, or by the modeller's own
analysis. This is deliberate: FKs stated as metadata leak too much answer.

Every `.sql` file in `transforms/silver/` and `transforms/gold/` starts with a
header comment block:

```
-- s_orders : one row per order, current status, item-count rolled up
-- grain:    order_id
-- sources:  raw.orders, raw.order_status_history, raw.order_items
-- lookback: none
```

`orchestrator/lineage.py` does two things:

1. Parses these headers → declared sources.
2. Parses the SQL body with `sqlglot` → actual FROM/JOIN references.
3. Asserts the two match (fails the DAG build if a transform reads a table its
   header didn't declare — keeps the modeller's view of lineage honest).

Then emits `lake/fintech/lineage.json`:

```json
{
  "generated_at": "2026-08-11T15:00:00Z",
  "raw": {
    "raw.orders":            {"cols": [...], "row_count": 128341, "bytes": 12488291, "partition": "date=..."},
    "raw.repayments_actual": {"cols": [...], ...},
    ...
  },
  "silver": {
    "silver.s_orders": {
      "grain":    "order_id",
      "sources":  ["raw.orders", "raw.order_status_history", "raw.order_items"],
      "join_keys":["order_id"],
      "lookback": null,
      "sql_hash": "sha1:...",
      "last_built_at": "2026-08-11T15:04:12Z",
      "row_count":    128341,
      "build_ms":     412
    },
    ...
  },
  "gold": {
    "gold.g_lending_health_daily": {
      "grain":     "vintage_month x snapshot_date",
      "sources":   ["silver.s_loans", "silver.s_repayments"],
      "lookback":  {"days": 90, "column": "snapshot_date"},
      "business_definition": "portfolio health with 90-day EMI payment lookback",
      ...
    },
    ...
  },
  "edges": [
    {"from": "raw.orders",             "to": "silver.s_orders"},
    {"from": "raw.order_status_history","to": "silver.s_orders"},
    {"from": "silver.s_loans",         "to": "gold.g_lending_health_daily"},
    {"from": "silver.s_repayments",    "to": "gold.g_lending_health_daily"},
    ...
  ]
}
```

This file is the modeller's structural map of the world. It's authoritative and
regenerated on every DAG run — so it can never drift from the SQL.

## 12 · Configuration surface (`data_harness/config.yaml`)

All knobs live here. Everything downstream reads from this file. No magic numbers
elsewhere. Full defaults committed alongside this plan (lean mode).

Key sections:

- `storage`: MinIO endpoint, bucket name (`lake`), root prefix (`labs/`)
- `retention`: `raw_days: 30`, `silver_days: 60`, `gold_days: 90`, `query_history_days: 14`
- `entity_caps`: `merchants: 500`, `users: 50_000`, `campaigns: 200`, `skus: 5_000`
- `event_rates` (per-tick, per-domain): see §13.1 — starts small (100 rows/tick)
- `workload`: archetype weights, users pool size, queries-per-tick
- `guardrails`: `max_lab_disk_gb: 8`, `max_query_seconds: 30`, `max_query_scan_gb: 2`
- `mode`: `lean | normal | heavy` — scales event_rates by 1× / 3× / 10×
- `time`: `mode: wall_clock | simulated`, `simulated_speed: 60` (60× real-time)
- `seeds`: master seed → per-generator seeds derived (fully reproducible)

## 13 · Phase-wise build plan

### Phase 0 — **plan + scaffold** (THIS TURN)

Deliverables:
- `data_harness/PLAN.md` (this document)
- `data_harness/config.yaml` (all knobs, lean-mode defaults committed)
- `data_harness/README.md` (60-line quickstart pointing at PLAN.md)
- Directory scaffold (already created above)
- Confirmation gates: schemas/raw.yaml can be auto-generated in Phase 1

**Exit criteria:** you read this doc, push back on §6/§7/§8 shapes, we align on
open questions in §15.

---

### Phase 1 — **skeleton end-to-end on `users` domain** (~1 week)

Prove the vertical works before scaling out. Just one domain.

- `common/*` — DuckDB connection factory, MinIO client, path helpers, logger
- `writers/parquet_writer.py` — buffered write + Hive partitioning
- `generators/base.py` — BaseGenerator, cadence, seeded Faker, state persistence
- `generators/reference.py` — seed all 5 reference tables (one-off)
- `generators/users.py` — 4 tables
- `transforms/silver/s_users.sql`
- (no gold yet — g_user_cohort_daily is a §8B discovery target, not built by us)
- `orchestrator/lineage.py` — SQL header parser + sqlglot FROM extraction
- `orchestrator/dag.py` — topological order from lineage
- `orchestrator/run.py --stages=reference,raw,silver` — end-to-end pass
- `scripts/status.py` — CLI showing row counts by layer

**Exit criteria:** one command `python -m data_harness.orchestrator.run --mode one_pass`
produces reference tables → 4 raw user tables → s_users, lineage.json is
written (raw + silver only), and `scripts/status.py` prints row counts by layer.

---

### Phase 2 — **remaining 7 domains, raw only** (~1.5 weeks)

Fan out the same shape to the other domains. No silver/gold yet for these.

- `generators/merchants.py` — 5 tables (Zipf distribution on volume)
- `generators/checkouts.py` — 4 tables
- `generators/orders.py` — 5 tables (references merchants + users)
- `generators/payments.py` — 7 tables **(nested payment_events.event_attributes)**
- `generators/lending.py` — 7 tables **(repayment_schedule.installments[])**
- `generators/adtech.py` — 6 tables **(attribution.touchpoints[])**
- `generators/risk.py` — 5 tables
- FK integrity checks in `guardrails.py` (orphan rate on cross-domain FKs)

**Exit criteria:** 48 raw tables landing on tick, disk footprint <500 MB after
30 simulated days at lean-mode rates.

---

### Phase 3 — **silver layer (all 13 tables)** (~1 week)

- `transforms/silver/*.sql` — 12 more silver tables (s_users already done)
- `transforms/runner.py` — reads all `.sql`, topo-sorts via lineage, executes
- Incremental rebuild: watermark per silver table stored in `generator_state/`
- Sanity: silver row counts reconcile against raw within tolerance

**Exit criteria:** silver refresh completes end-to-end in <60 s at lean scale,
reconciliation passes.

---

### Phase 4 — **baseline gold layer (3 tables only)** (~3 days)

- `transforms/gold/g_merchant_daily.sql`
- `transforms/gold/g_payments_daily.sql`
- `transforms/gold/g_orders_daily.sql`
- Reconciliation: `sum(g_merchant_daily.gmv) ≈ sum(s_orders.order_amount)` etc.
- **Deliberately absent:** the 4 discovery targets from §8B. They are the
  modeller's job in Phase 7+. Not stubbed, not scaffolded, not hinted at in
  lineage.json — completely absent from our build.

**Exit criteria:** the 3 baseline tables refresh in <15 s at lean scale, reconcile
against silver, and appear in `lineage.json`. No entry for the 4 discovery
targets anywhere in the harness.

---

### Phase 5 — **query workload runner** (~1 week)

- `workload/query_bank/{bi,analyst,ops,rca}.py` — ~100 total templates
- `workload/runner.py` — archetype-weighted sampler, parameter binder,
  DuckDB executor, `EXPLAIN ANALYZE` capture
- `workload/telemetry.py` — profile JSON → flat query_history schema
- Append to `lake/fintech/query_history/date=…/part-…parquet`
- Include the raw-vs-gold RCA queries deliberately (so the modeller sees the
  90-day-lookback pattern hitting raw hundreds of times)

**Exit criteria:** `python -m data_harness.workload.runner --n 500` writes 500
query rows with realistic cost/latency spread; `layer_mix` distribution matches
config.

---

### Phase 6 — **observability + orchestrator polish** (~3 days)

- `orchestrator/run.py --mode continuous --interval 60s` (apscheduler loop)
- `orchestrator/run.py --mode backfill --days 30` (fast-forward)
- `guardrails.py`: disk-budget guard pauses generators if MinIO bucket > cap
- Optional: streamlit dashboard on top of `lake/fintech/query_history` and
  per-run stats

**Exit criteria:** run continuously for 24h at lean mode with no manual
intervention; disk stays within budget; graceful pause on cap.

---

### Phase 7+ — **AI Data Modeller** (separate design doc)

Consumes `lineage.json` + `query_history` + `raw|silver|gold` metadata. Proposes:

- New gold tables that would absorb the most-expensive repeated RCA shapes
- Materialised views for high-frequency BI queries
- Sort keys / partitioning suggestions
- Rolling-window pre-aggregations (the 90-day EMI is the canonical case)

Out of scope here.

## 14 · Deliverables per phase (checklist form)

| # | phase | primary artifacts | reviewable exit test |
|---|---|---|---|
| 0 | plan + scaffold | PLAN.md · config.yaml · README.md · directory tree | you read PLAN.md and approve §6-8 |
| 1 | vertical on users | 1 gen · 1 silver · orchestrator · lineage.json | `run --mode one_pass` end-to-end, status CLI shows counts |
| 2 | all raw | 7 more generators (~44 more tables) | 48 raw tables landing, FK integrity |
| 3 | silver | 12 more silver .sql · runner | silver refresh <60s, reconciliations |
| 4 | **baseline gold only (3 tables)** | g_merchant_daily · g_payments_daily · g_orders_daily | reconciliations pass; discovery targets DELIBERATELY absent |
| 5 | workload | 100 templates · runner · telemetry — MUST include the 4 discovery-target patterns from §9A | 500 real queries logged with layer_mix; each discovery target has ≥50 rows in query_history |
| 6 | ops polish | continuous mode · backfill · guardrails | 24h run stays lean, auto-pause works |

Rough total: **3-4 weeks** (down from 4-5 — we now build 3 gold tables not 7).
Phase 7+ is the modeller itself, which lives in `src/diracdata/modeller/` and
consumes this substrate to propose the 4 missing gold tables.

## 15 · Open questions — CLOSED (see §0 Decisions locked)

All seven have been decided; the outcomes are captured in §0 and threaded
through §6, §9A, §10, §11, and the `config.yaml`. No open blockers remaining.

---

**Phase 0 complete. Ready to start Phase 1** — the users-domain vertical
end-to-end (generator → silver → orchestrator → lineage.json → status CLI),
targeting one command:

```bash
python -m data_harness.orchestrator.run --mode one_pass --stages=reference,raw,silver
```

that lands 5 reference tables + 4 users raw tables + 1 silver (s_users) and
prints layer row counts.
