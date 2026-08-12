-- s_loans : one row per loan; application context; latest credit score at/before disbursal
-- grain:    loan_id
-- sources:  raw.loans, raw.loan_applications, raw.credit_bureau_pulls
-- notes:    latest_bureau = latest pull per user; vintage_month = month of disbursed_at
WITH latest_bureau AS (
    SELECT user_id,
           arg_max(credit_score, pulled_at) AS credit_score_latest,
           max(pulled_at)                   AS bureau_pulled_at
    FROM read_parquet('s3://lake/fintech/raw/lending/credit_bureau_pulls/**/*.parquet', hive_partitioning=1)
    GROUP BY user_id
)
SELECT
    l.loan_id,
    l.app_id,
    l.user_id,
    l.principal,
    l.interest_rate,
    l.tenure_months,
    l.disbursed_at,
    date_trunc('month', l.disbursed_at)::DATE     AS vintage_month,
    l.status,
    l.risk_snapshot.score_at_origination          AS score_at_origination,
    l.risk_snapshot.dpd_current                   AS dpd_current,
    l.risk_snapshot.dpd_max_ever                  AS dpd_max_ever,
    length(l.installments)                        AS installment_count,
    a.requested_amount                            AS app_requested_amount,
    a.status                                      AS app_status,
    a.applicant_snapshot.monthly_income           AS applicant_monthly_income,
    a.applicant_snapshot.employment_type          AS employment_type,
    b.credit_score_latest,
    b.bureau_pulled_at
FROM read_parquet('s3://lake/fintech/raw/lending/loans/**/*.parquet', hive_partitioning=1) l
LEFT JOIN read_parquet('s3://lake/fintech/raw/lending/loan_applications/**/*.parquet', hive_partitioning=1) a
       ON a.app_id = l.app_id
LEFT JOIN latest_bureau b
       ON b.user_id = l.user_id
