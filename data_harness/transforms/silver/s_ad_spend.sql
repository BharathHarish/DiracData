-- s_ad_spend : campaign × spend_date, normalized (breakdown MAP flattened via count-of-platforms)
-- grain:    campaign_id, spend_date
-- sources:  raw.ad_spend_daily
-- notes:    Keep breakdown as-is; add derived cpc + ctr for convenience
SELECT
    s.campaign_id,
    s.spend_date,
    s.spend_amount,
    s.impressions_count,
    s.clicks_count,
    cardinality(s.breakdown)                                     AS platform_count,
    s.spend_amount / NULLIF(s.clicks_count, 0)                   AS cpc,
    s.clicks_count::DOUBLE / NULLIF(s.impressions_count, 0)      AS ctr
FROM read_parquet('s3://lake/fintech/raw/adtech/ad_spend_daily/**/*.parquet', hive_partitioning=1) s
