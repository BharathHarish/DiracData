-- s_attribution : one row per touchpoint (touchpoints UNNESTed to touchpoint-per-row grain)
-- grain:    attribution_id, touchpoint_index
-- sources:  raw.attribution
-- notes:    flattens multi-touch attribution; preserves per-touchpoint weight and channel
SELECT
    a.attribution_id,
    a.order_id,
    a.model                                     AS attribution_model,
    a.attributed_at,
    ord.tp_index                                AS touchpoint_index,
    ord.tp.channel                              AS channel,
    ord.tp.campaign_id                          AS campaign_id,
    ord.tp.touched_at                           AS touched_at,
    ord.tp.weight                               AS weight,
    epoch_ms(a.attributed_at) - epoch_ms(ord.tp.touched_at) AS ms_from_touch_to_attribution
FROM read_parquet('s3://lake/fintech/raw/adtech/attribution/**/*.parquet', hive_partitioning=1) a,
     LATERAL (SELECT tp, tp_index FROM unnest(a.touchpoints) WITH ORDINALITY AS t(tp, tp_index)) ord
