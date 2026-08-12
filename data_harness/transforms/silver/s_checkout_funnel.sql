-- s_checkout_funnel : one row per session, funnel steps rolled up, converted flag, duration
-- grain:    session_id
-- sources:  raw.checkout_sessions, raw.checkout_events
-- notes:    counts of each event_type as separate cols; time to conversion / abandonment
WITH event_agg AS (
    SELECT session_id,
           count(*)                                      AS event_total,
           sum(CASE event_type WHEN 'page_view'       THEN 1 ELSE 0 END) AS page_views,
           sum(CASE event_type WHEN 'item_add'        THEN 1 ELSE 0 END) AS items_added,
           sum(CASE event_type WHEN 'item_remove'     THEN 1 ELSE 0 END) AS items_removed,
           sum(CASE event_type WHEN 'payment_start'   THEN 1 ELSE 0 END) AS payment_starts,
           sum(CASE event_type WHEN 'payment_success' THEN 1 ELSE 0 END) AS payment_successes,
           sum(CASE event_type WHEN 'payment_fail'    THEN 1 ELSE 0 END) AS payment_failures,
           min(event_time)                               AS first_event_at,
           max(event_time)                               AS last_event_at
    FROM read_parquet('s3://lake/fintech/raw/checkouts/checkout_events/**/*.parquet', hive_partitioning=1)
    GROUP BY session_id
)
SELECT
    s.session_id,
    s.merchant_id,
    s.user_id,
    s.initiated_at,
    s.status,
    s.total_amount,
    s.utm.source                              AS utm_source,
    s.utm.medium                              AS utm_medium,
    s.utm.campaign                            AS utm_campaign,
    COALESCE(e.event_total,       0)          AS event_total,
    COALESCE(e.page_views,        0)          AS page_views,
    COALESCE(e.items_added,       0)          AS items_added,
    COALESCE(e.items_removed,     0)          AS items_removed,
    COALESCE(e.payment_starts,    0)          AS payment_starts,
    COALESCE(e.payment_successes, 0)          AS payment_successes,
    COALESCE(e.payment_failures,  0)          AS payment_failures,
    (COALESCE(e.payment_successes, 0) > 0)    AS converted,
    e.first_event_at,
    e.last_event_at,
    (epoch_ms(e.last_event_at) - epoch_ms(s.initiated_at))  AS session_duration_ms
FROM read_parquet('s3://lake/fintech/raw/checkouts/checkout_sessions/**/*.parquet', hive_partitioning=1) s
LEFT JOIN event_agg e USING (session_id)
