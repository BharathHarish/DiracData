-- g_payments_daily : per rail_type × day payment success/failure roll-up (BASELINE gold)
-- grain:    rail_type, day
-- sources:  silver.s_payments
-- notes:    Baseline gold — cross rail × merchant_tier cut lives in the merchant scorecard;
--           per-merchant × per-rail cross-tabs are deliberately NOT here (that would eat one
--           of the modeller's discovery opportunities).
SELECT
    rail_type,
    attempted_at::DATE                                              AS day,
    count(*)                                                        AS attempts,
    sum(CASE WHEN status_final IN ('captured','settled') THEN 1 ELSE 0 END) AS successes,
    sum(CASE WHEN status_final = 'failed' THEN 1 ELSE 0 END)        AS failures,
    sum(CASE WHEN status_final = 'pending' THEN 1 ELSE 0 END)       AS pendings,
    sum(CASE WHEN status_final IN ('captured','settled') THEN 1 ELSE 0 END)::DOUBLE
        / NULLIF(count(*), 0)                                       AS success_rate,
    sum(CASE WHEN status_final = 'failed' THEN 1 ELSE 0 END)::DOUBLE
        / NULLIF(count(*), 0)                                       AS failure_rate,
    avg(amount)                                                     AS avg_ticket,
    sum(amount)                                                     AS total_amount,
    sum(CASE WHEN status_final IN ('captured','settled') THEN amount ELSE 0 END) AS amount_captured,
    sum(refund_count)                                               AS refund_count,
    sum(refund_total)                                               AS refund_amount,
    avg(routing_latency_ms)                                         AS avg_routing_latency_ms
FROM read_parquet('s3://lake/fintech/silver/s_payments/**/*.parquet', hive_partitioning=1)
GROUP BY rail_type, attempted_at::DATE
