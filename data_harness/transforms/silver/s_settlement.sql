-- s_settlement : one row per settlement batch; chargebacks netted; per-rail breakdown kept as struct list
-- grain:    batch_id
-- sources:  raw.settlement_batches, raw.chargebacks, raw.payment_attempts
-- notes:    We join chargebacks to a batch via the payment_attempts.merchant_id + batch_date-of-payment
--          heuristic: chargebacks whose original_payment was for this merchant on/before batch_date.
--          This is approximate — real settlement netting is more complex; here we compute a simple net.
WITH cb_by_merchant_day AS (
    SELECT p.merchant_id,
           p.attempted_at::DATE                       AS d,
           sum(cb_amount.amt)                         AS chargeback_total,
           count(*)                                   AS chargeback_count
    FROM read_parquet('s3://lake/fintech/raw/payments/chargebacks/**/*.parquet', hive_partitioning=1) c
    JOIN read_parquet('s3://lake/fintech/raw/payments/payment_attempts/**/*.parquet', hive_partitioning=1) p
      ON c.original_payment_id = p.attempt_id
    JOIN (SELECT c.chargeback_id, p.amount AS amt
          FROM read_parquet('s3://lake/fintech/raw/payments/chargebacks/**/*.parquet', hive_partitioning=1) c
          JOIN read_parquet('s3://lake/fintech/raw/payments/payment_attempts/**/*.parquet', hive_partitioning=1) p
            ON c.original_payment_id = p.attempt_id) cb_amount
      ON c.chargeback_id = cb_amount.chargeback_id
    GROUP BY 1, 2
)
SELECT
    b.batch_id,
    b.merchant_id,
    b.batch_date,
    b.gross_amount,
    b.mdr_amount,
    b.net_amount                                AS net_amount_raw,
    b.status,
    length(b.breakdown)                         AS rail_count,
    COALESCE(cb.chargeback_total, 0.0)          AS chargeback_offset,
    COALESCE(cb.chargeback_count, 0)            AS chargeback_count,
    b.net_amount - COALESCE(cb.chargeback_total, 0.0) AS net_after_chargebacks
FROM read_parquet('s3://lake/fintech/raw/payments/settlement_batches/**/*.parquet', hive_partitioning=1) b
LEFT JOIN cb_by_merchant_day cb
       ON cb.merchant_id = b.merchant_id AND cb.d = b.batch_date
