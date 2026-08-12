-- s_user_activity : per (user_id, activity_date), session count + order count + gmv
-- grain:    user_id, activity_date
-- sources:  raw.user_sessions, raw.orders
-- notes:    activity_date = date component of session_start OR order_time. UNION then aggregate.
WITH combined AS (
    SELECT user_id, session_start::DATE AS activity_date, 1 AS session_count, 0 AS order_count, 0.0 AS gmv
    FROM read_parquet('s3://lake/fintech/raw/users/user_sessions/**/*.parquet', hive_partitioning=1)
    UNION ALL
    SELECT user_id, order_time::DATE   AS activity_date, 0, 1, order_amount
    FROM read_parquet('s3://lake/fintech/raw/orders/orders/**/*.parquet', hive_partitioning=1)
)
SELECT
    user_id,
    activity_date,
    sum(session_count)                          AS session_count,
    sum(order_count)                            AS order_count,
    sum(gmv)                                    AS gmv,
    (sum(order_count) > 0)                      AS transacted
FROM combined
GROUP BY user_id, activity_date
