-- s_orders : one row per order, current status, item_count + total from line_items
-- grain:    order_id
-- sources:  raw.orders, raw.order_status_history
-- notes:    current status wins via arg_max(changed_at); rolled-up item_count from nested line_items
WITH current_status AS (
    SELECT order_id,
           arg_max(status, changed_at)     AS status_current,
           max(changed_at)                 AS status_updated_at,
           count(*)                        AS status_transitions
    FROM read_parquet('s3://lake/fintech/raw/orders/order_status_history/**/*.parquet', hive_partitioning=1)
    GROUP BY order_id
)
SELECT
    o.order_id,
    o.checkout_session_id,
    o.merchant_id,
    o.user_id,
    o.order_time,
    o.order_amount,
    o.status                                  AS status_original,
    COALESCE(s.status_current, o.status)      AS status_current,
    s.status_updated_at,
    COALESCE(s.status_transitions, 0)         AS status_transitions,
    length(o.line_items)                      AS item_count,
    (SELECT sum(li.qty * li.unit_price) FROM unnest(o.line_items) t(li))    AS gross_line_amount,
    (SELECT sum(li.discount) FROM unnest(o.line_items) t(li))                AS total_line_discount,
    o.fraud_signals.score                     AS fraud_score,
    o.fraud_signals.model_version             AS fraud_model_version,
    o.fulfillment.warehouse                   AS fulfillment_warehouse,
    length(o.fulfillment.shipments)           AS shipment_count
FROM read_parquet('s3://lake/fintech/raw/orders/orders/**/*.parquet', hive_partitioning=1) o
LEFT JOIN current_status s USING (order_id)
