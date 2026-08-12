-- s_adtech_events : one row per impression, click_through flag if clicked, seconds_to_click
-- grain:    impression_id
-- sources:  raw.ad_impressions, raw.ad_clicks
-- notes:    LEFT JOIN clicks by impression_id; small fraction of impressions get clicks
SELECT
    i.impression_id,
    i.campaign_id,
    i.creative_id,
    i.user_id,
    i.shown_at,
    i.platform,
    i.placement.surface                       AS surface,
    i.placement.position_index                AS position_index,
    i.placement.viewport_pct_visible          AS viewport_pct_visible,
    i.placement.duration_ms                   AS view_duration_ms,
    (c.click_id IS NOT NULL)                  AS click_through,
    c.click_id,
    c.clicked_at,
    epoch_ms(c.clicked_at) - epoch_ms(i.shown_at) AS ms_to_click,
    c.click_context.time_since_impression_ms  AS click_time_since_impression_ms
FROM read_parquet('s3://lake/fintech/raw/adtech/ad_impressions/**/*.parquet', hive_partitioning=1) i
LEFT JOIN read_parquet('s3://lake/fintech/raw/adtech/ad_clicks/**/*.parquet', hive_partitioning=1) c
       USING (impression_id)
