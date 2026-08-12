-- s_merchants : one row per merchant, current settlement config + current pricing + MCC category
-- grain:    merchant_id
-- sources:  raw.merchants, raw.merchant_settlement_config, raw.merchant_pricing_plans, raw.merchant_category_map
-- notes:    current settlement = latest by effective_from; current plan = plan with NULL effective_to (or latest)
WITH latest_settlement AS (
    SELECT merchant_id, arg_max(settlement_speed, effective_from) AS settlement_speed,
           arg_max(settlement_bank_ref, effective_from)          AS settlement_bank_ref,
           max(effective_from)                                    AS settlement_since
    FROM read_parquet('s3://lake/fintech/raw/merchants/merchant_settlement_config/**/*.parquet', hive_partitioning=1)
    GROUP BY merchant_id
),
current_plan AS (
    SELECT merchant_id, arg_max(plan_name, effective_from) AS plan_name,
           arg_max(mdr_bps, effective_from)                AS mdr_bps,
           max(effective_from)                             AS plan_since
    FROM read_parquet('s3://lake/fintech/raw/merchants/merchant_pricing_plans/**/*.parquet', hive_partitioning=1)
    GROUP BY merchant_id
),
mcc AS (
    SELECT mcc_code, arg_max(category_name, mcc_code) AS category_name,
           arg_max(category_group, mcc_code) AS category_group
    FROM read_parquet('s3://lake/fintech/raw/merchants/merchant_category_map/**/*.parquet', hive_partitioning=1)
    GROUP BY mcc_code
)
SELECT
    m.merchant_id,
    m.business_name,
    m.mcc_code,
    c.category_name,
    c.category_group,
    m.tier,
    m.status,
    m.state_code,
    m.city,
    m.gstin,
    m.pan,
    m.onboarded_at,
    length(m.contact_persons)  AS contact_person_count,
    s.settlement_speed,
    s.settlement_since,
    p.plan_name,
    p.mdr_bps,
    p.plan_since
FROM read_parquet('s3://lake/fintech/raw/merchants/merchants/**/*.parquet', hive_partitioning=1) m
LEFT JOIN latest_settlement s USING (merchant_id)
LEFT JOIN current_plan       p USING (merchant_id)
LEFT JOIN mcc                 c USING (mcc_code)
