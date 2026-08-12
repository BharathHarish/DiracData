-- g_orders_daily : per-merchant per-day orders + checkout funnel + fulfillment (BASELINE gold)
-- grain:    merchant_id, day
-- sources:  silver.s_orders, silver.s_checkout_funnel
-- notes:    Baseline gold. Order-side breakdown separate from merchant P&L in g_merchant_daily
--           (this one covers cancellations + abandonment funnel; that one covers money).
WITH order_agg AS (
    SELECT merchant_id,
           order_time::DATE                                    AS day,
           count(*)                                             AS orders,
           sum(CASE WHEN status_current = 'placed'     THEN 1 ELSE 0 END) AS orders_placed,
           sum(CASE WHEN status_current = 'shipped'    THEN 1 ELSE 0 END) AS orders_shipped,
           sum(CASE WHEN status_current = 'delivered'  THEN 1 ELSE 0 END) AS orders_delivered,
           sum(CASE WHEN status_current = 'cancelled'  THEN 1 ELSE 0 END) AS orders_cancelled,
           sum(CASE WHEN status_current = 'returned'   THEN 1 ELSE 0 END) AS orders_returned,
           sum(shipment_count)                                  AS total_shipments,
           avg(item_count)                                      AS avg_items_per_order
    FROM read_parquet('s3://lake/fintech/silver/s_orders/**/*.parquet', hive_partitioning=1)
    GROUP BY merchant_id, order_time::DATE
),
funnel_agg AS (
    SELECT merchant_id,
           initiated_at::DATE                                   AS day,
           count(*)                                             AS checkout_sessions,
           sum(CASE WHEN converted THEN 1 ELSE 0 END)           AS conversions,
           sum(CASE WHEN status = 'abandoned' THEN 1 ELSE 0 END) AS abandonments,
           sum(payment_failures)                                AS checkout_payment_failures,
           avg(session_duration_ms)                             AS avg_session_duration_ms
    FROM read_parquet('s3://lake/fintech/silver/s_checkout_funnel/**/*.parquet', hive_partitioning=1)
    GROUP BY merchant_id, initiated_at::DATE
)
SELECT
    COALESCE(o.merchant_id, f.merchant_id)      AS merchant_id,
    COALESCE(o.day,         f.day)              AS day,
    COALESCE(o.orders,                     0)   AS orders,
    COALESCE(o.orders_placed,              0)   AS orders_placed,
    COALESCE(o.orders_shipped,             0)   AS orders_shipped,
    COALESCE(o.orders_delivered,           0)   AS orders_delivered,
    COALESCE(o.orders_cancelled,           0)   AS orders_cancelled,
    COALESCE(o.orders_returned,            0)   AS orders_returned,
    (COALESCE(o.orders_cancelled, 0)::DOUBLE
        / NULLIF(COALESCE(o.orders, 0), 0))    AS cancellation_rate,
    COALESCE(o.total_shipments,            0)   AS total_shipments,
    o.avg_items_per_order,
    COALESCE(f.checkout_sessions,          0)   AS checkout_sessions,
    COALESCE(f.conversions,                0)   AS conversions,
    COALESCE(f.abandonments,               0)   AS abandonments,
    (COALESCE(f.abandonments, 0)::DOUBLE
        / NULLIF(COALESCE(f.checkout_sessions, 0), 0)) AS abandonment_rate,
    COALESCE(f.checkout_payment_failures,  0)   AS checkout_payment_failures,
    f.avg_session_duration_ms
FROM order_agg o
FULL OUTER JOIN funnel_agg f ON f.merchant_id = o.merchant_id AND f.day = o.day
