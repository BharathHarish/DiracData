-- s_users : one row per user; current KYC state; device_count; language; state code
-- grain:    user_id
-- sources:  raw.users, raw.user_kyc_events
-- notes:    Full refresh (small at lean scale); latest KYC event wins.
SELECT
    u.user_id,
    u.user_type,
    u.signup_time,
    u.state_code,
    u.city,
    u.language,
    u.kyc_status                                            AS kyc_status_initial,
    COALESCE(k.latest_kyc_status, u.kyc_status)             AS kyc_status_current,
    k.latest_kyc_time                                       AS kyc_last_updated,
    length(u.devices)                                       AS device_count,
    length(u.kyc_documents)                                 AS kyc_document_count,
    u.preferences::JSON ->> 'theme'                         AS theme_pref,
    (u.preferences::JSON -> 'notifications' ->> 'push')::BOOLEAN AS push_optin,
    u._ingest_ts                                            AS ingest_ts
FROM read_parquet('s3://lake/fintech/raw/users/users/**/*.parquet', hive_partitioning=1) u
LEFT JOIN (
    SELECT user_id,
           arg_max(event_type, event_time) AS latest_kyc_status,
           max(event_time)                 AS latest_kyc_time
    FROM read_parquet('s3://lake/fintech/raw/users/user_kyc_events/**/*.parquet', hive_partitioning=1)
    GROUP BY user_id
) k USING (user_id)
