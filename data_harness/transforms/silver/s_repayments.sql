-- s_repayments : one row per actual repayment, joined to its schedule with days_late + on_time flag
-- grain:    repayment_id
-- sources:  raw.repayments_actual, raw.repayment_schedule
-- notes:    days_late = paid_at::date - due_date. Negative or 0 = on-time. NULL when schedule missing.
SELECT
    r.repayment_id,
    r.loan_id,
    r.schedule_id,
    r.paid_at,
    r.paid_amount,
    r.payment_method,
    r.allocation.principal_paid                     AS principal_paid,
    r.allocation.interest_paid                      AS interest_paid,
    r.allocation.penalty_paid                       AS penalty_paid,
    s.installment_no,
    s.due_date,
    s.emi_amount,
    date_diff('day', s.due_date, r.paid_at::DATE)                  AS days_late,
    (date_diff('day', s.due_date, r.paid_at::DATE) <= 0)           AS on_time,
    ( date_diff('day', s.due_date, r.paid_at::DATE) > 0
      AND date_diff('day', s.due_date, r.paid_at::DATE) <= 30 )    AS bucket_1_30,
    ( date_diff('day', s.due_date, r.paid_at::DATE) > 30
      AND date_diff('day', s.due_date, r.paid_at::DATE) <= 60 )    AS bucket_31_60,
    ( date_diff('day', s.due_date, r.paid_at::DATE) > 60
      AND date_diff('day', s.due_date, r.paid_at::DATE) <= 90 )    AS bucket_61_90,
    ( date_diff('day', s.due_date, r.paid_at::DATE) > 90 )         AS bucket_90_plus,
    (r.paid_amount / NULLIF(s.emi_amount, 0))       AS pay_ratio
FROM read_parquet('s3://lake/fintech/raw/lending/repayments_actual/**/*.parquet', hive_partitioning=1) r
LEFT JOIN read_parquet('s3://lake/fintech/raw/lending/repayment_schedule/**/*.parquet', hive_partitioning=1) s
       USING (schedule_id)
