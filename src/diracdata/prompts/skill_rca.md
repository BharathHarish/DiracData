## METRIC ROOT-CAUSE

This asks WHY a metric moved. `metric_tree(metric)` returns the whole driver tree with each node's SQL
and how drivers combine (multiplicative / additive) -- use it; do NOT rediscover the schema or re-`define`
its metrics, the SQL is already there.

INVARIANTS -- do not skip:
- DATA SANITY: `data_health` the fact table + the join keys the metric rests on (once) before trusting a driver.
- ATTRIBUTE THE CHANGE, not the level: quantify each driver's CONTRIBUTION and reconcile the parts to the
  total. Multiplicative `A x B`: A-effect=(A2-A1)*B1, B-effect=A2*(B2-B1), interaction=(A2-A1)*(B2-B1).
  Additive: each part's own period delta. Never hardcode a contribution -- derive it.
- WHERE it concentrated: fan out ONE sub-agent per requested dimension (category, region, income band, ...)
  concurrently; each returns the ranked movers.

MINIMISE SERIAL ROUND-TRIPS: one `metric_tree` call, the driver values for both periods in ONE wide query,
the driver arithmetic done locally. Report the driver decomposition + ranked movers per dimension.
