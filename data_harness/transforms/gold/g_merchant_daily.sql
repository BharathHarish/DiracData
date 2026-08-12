-- g_merchant_daily : per-merchant per-day GMV + payment + settlement roll-up (BASELINE gold)
-- grain:    merchant_id, day
-- sources:  silver.s_merchants, silver.s_orders, silver.s_payments, silver.s_settlement
-- notes:    Baseline gold — the obvious BI table any data engineer ships day 1.
--           Modeller-discovery targets (see PLAN §8B) are NOT here.
WITH order_agg AS (
    SELECT merchant_id,
           order_time::DATE            AS day,
           count(*)                    AS orders,
           sum(order_amount)           AS gmv,
           sum(item_count)             AS items_sold,
           avg(fraud_score)            AS avg_fraud_score
    FROM read_parquet('s3://lake/fintech/silver/s_orders/**/*.parquet', hive_partitioning=1)
    GROUP BY merchant_id, order_time::DATE
),
pay_agg AS (
    SELECT merchant_id,
           attempted_at::DATE          AS day,
           count(*)                    AS payment_attempts,
           sum(CASE WHEN status_final IN ('captured','settled') THEN 1 ELSE 0 END) AS payment_successes,
           sum(CASE WHEN status_final = 'failed' THEN 1 ELSE 0 END) AS payment_failures,
           sum(CASE WHEN status_final IN ('captured','settled') THEN amount ELSE 0 END) AS payment_amount_captured,
           sum(refund_total)           AS refund_amount
    FROM read_parquet('s3://lake/fintech/silver/s_payments/**/*.parquet', hive_partitioning=1)
    GROUP BY merchant_id, attempted_at::DATE
),
settle_agg AS (
    SELECT merchant_id,
           batch_date                  AS day,
           sum(gross_amount)           AS settlement_gross,
           sum(mdr_amount)             AS settlement_mdr,
           sum(net_after_chargebacks)  AS settlement_net,
           sum(chargeback_offset)      AS chargeback_amount
    FROM read_parquet('s3://lake/fintech/silver/s_settlement/**/*.parquet', hive_partitioning=1)
    GROUP BY merchant_id, batch_date
)
SELECT
    COALESCE(o.merchant_id, p.merchant_id, s.merchant_id)  AS merchant_id,
    COALESCE(o.day, p.day, s.day)                          AS day,
    m.tier,
    m.category_group,
    m.state_code,
    COALESCE(o.orders,             0)                      AS orders,
    COALESCE(o.gmv,              0.0)                      AS gmv,
    COALESCE(o.items_sold,          0)                     AS items_sold,
    o.avg_fraud_score,
    COALESCE(p.payment_attempts,   0)                      AS payment_attempts,
    COALESCE(p.payment_successes,  0)                      AS payment_successes,
    COALESCE(p.payment_failures,   0)                      AS payment_failures,
    (COALESCE(p.payment_successes, 0)::DOUBLE
        / NULLIF(COALESCE(p.payment_attempts, 0), 0))      AS payment_success_rate,
    COALESCE(p.payment_amount_captured, 0.0)               AS payment_amount_captured,
    COALESCE(p.refund_amount,    0.0)                      AS refund_amount,
    COALESCE(s.settlement_gross, 0.0)                      AS settlement_gross,
    COALESCE(s.settlement_mdr,   0.0)                      AS settlement_mdr,
    COALESCE(s.settlement_net,   0.0)                      AS settlement_net,
    COALESCE(s.chargeback_amount, 0.0)                     AS chargeback_amount
FROM order_agg o
FULL OUTER JOIN pay_agg    p ON p.merchant_id = o.merchant_id AND p.day = o.day
FULL OUTER JOIN settle_agg s ON s.merchant_id = COALESCE(o.merchant_id, p.merchant_id)
                             AND s.day        = COALESCE(o.day,          p.day)
LEFT JOIN read_parquet('s3://lake/fintech/silver/s_merchants/**/*.parquet', hive_partitioning=1) m
       ON m.merchant_id = COALESCE(o.merchant_id, p.merchant_id, s.merchant_id)
