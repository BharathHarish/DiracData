-- s_risk_events : one row per risk event, top rule + score flattened, action decided
-- grain:    risk_event_id
-- sources:  raw.risk_events, raw.rules_fired
-- notes:    top_rule = rule with highest score. terminal_action = worst action across all fired rules.
WITH agg AS (
    SELECT risk_event_id,
           arg_max(rule_name, score)                       AS top_rule_name,
           max(score)                                      AS top_rule_score,
           max(CASE action WHEN 'block' THEN 3 WHEN 'challenge' THEN 2 ELSE 1 END) AS worst_rank,
           count(*)                                        AS rules_fired_count
    FROM read_parquet('s3://lake/fintech/raw/risk/rules_fired/**/*.parquet', hive_partitioning=1)
    GROUP BY risk_event_id
)
SELECT
    e.risk_event_id,
    e.entity_type,
    e.entity_id,
    e.event_time,
    e.rule_id                                       AS rule_id_triggering,
    e.severity,
    g.top_rule_name,
    g.top_rule_score,
    CASE g.worst_rank WHEN 3 THEN 'block' WHEN 2 THEN 'challenge' ELSE 'allow' END AS terminal_action,
    COALESCE(g.rules_fired_count, 0)                AS rules_fired_count
FROM read_parquet('s3://lake/fintech/raw/risk/risk_events/**/*.parquet', hive_partitioning=1) e
LEFT JOIN agg g USING (risk_event_id)
