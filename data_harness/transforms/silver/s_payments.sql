-- s_payments : one row per payment attempt, final status from events, refund total attached
-- grain:    attempt_id
-- sources:  raw.payment_attempts, raw.payment_events, raw.refunds
-- notes:    final_event = latest event by time; refund_total sums refunds against this attempt's id
WITH final_event AS (
    SELECT attempt_id,
           arg_max(event_type, event_time)              AS terminal_event,
           max(event_time)                              AS terminal_event_time,
           count(*)                                     AS event_count
    FROM read_parquet('s3://lake/fintech/raw/payments/payment_events/**/*.parquet', hive_partitioning=1)
    GROUP BY attempt_id
),
refund_agg AS (
    SELECT original_payment_id AS attempt_id,
           sum(refund_amount)   AS refund_total,
           count(*)             AS refund_count
    FROM read_parquet('s3://lake/fintech/raw/payments/refunds/**/*.parquet', hive_partitioning=1)
    GROUP BY 1
)
SELECT
    a.attempt_id,
    a.order_id,
    a.user_id,
    a.merchant_id,
    a.amount,
    a.rail_type,
    a.attempted_at,
    a.status                                        AS status_reported,
    COALESCE(f.terminal_event, a.status)            AS status_final,
    f.terminal_event_time,
    COALESCE(f.event_count, 0)                      AS event_count,
    length(a.risk_checks)                           AS risk_check_count,
    a.routing_decision.primary_rail                 AS primary_rail,
    a.routing_decision.fallback_rail                AS fallback_rail,
    a.routing_decision.latency_ms                   AS routing_latency_ms,
    COALESCE(r.refund_total, 0.0)                   AS refund_total,
    COALESCE(r.refund_count, 0)                     AS refund_count,
    (COALESCE(r.refund_total, 0.0) > 0.0)           AS was_refunded
FROM read_parquet('s3://lake/fintech/raw/payments/payment_attempts/**/*.parquet', hive_partitioning=1) a
LEFT JOIN final_event f USING (attempt_id)
LEFT JOIN refund_agg  r USING (attempt_id)
