## METRIC-RCA SKILL (loaded because this question is a root-cause analysis of a defined metric)

You are explaining WHY a metric is at its level or MOVED -- not just reporting it. Follow this playbook.
The slice (metric x grain x dimension) is the crux. Do these steps IN ORDER.

STEP 1 -- DATA SANITY FIRST (do not skip). Before you trust any driver number, run data_health (or
data_check) on the KEY tables and JOINS the metric rests on: the fact table, every dimension join key,
and the columns you will slice by. Look for a null spike, an orphan/NULL-dropping join, a distinct
collapse, a row-count or range jump, stale data. A silent NULL-dropping join corrupts every driver, so
catch it HERE and state what you found before proceeding.

STEP 2 -- GET THE DECOMPOSITION. metric_tree(metric) returns the metric's driver tree (each driver's
SQL/formula + additive vs multiplicative). If the metric has no defined tree, decompose it the standard
way (revenue = order_volume x average order value; volume = new + returning + reactivated) and say so.

STEP 3 -- FAN OUT THE DRIVERS. spawn_subagents([...]) -- ONE branch per top-level driver -- to quantify
each driver over the compared periods CONCURRENTLY. Give each a complete standalone task; keep the
orchestrator's context lean.

STEP 4 -- ATTRIBUTE THE CHANGE (this is the point of RCA -- contribution, not level). Quantify each
driver's CONTRIBUTION to the metric's change. For a multiplicative split `metric = A x B` from period 1
to period 2:
    A-effect     = (A2 - A1) x B1
    B-effect     = A2 x (B2 - B1)
    interaction  = (A2 - A1) x (B2 - B1)
For an additive split, each part's effect is its own period-over-period delta. VERIFY the effects
RECONCILE to the total change (A-effect + B-effect + interaction = total delta). Compute every effect
with query_result over your stored results -- NEVER hardcode a contribution number.

STEP 5 -- RANK AND LOCALISE. The largest-contribution driver is the proximate cause: name it with its
dollar/point contribution and % of the total move. Then recurse -- metric_tree into ITS drivers, and/or
slice it by the requested dimensions (segment, region, category, income band) to find where the move
concentrates. Report the ranked movers, their contributions, and the dimensions that carry them; flag
any driver whose data-health check (Step 1) was material.
