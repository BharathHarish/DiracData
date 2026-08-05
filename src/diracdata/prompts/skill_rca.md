## METRIC-RCA SKILL (loaded because this question is a root-cause analysis of a defined metric)

You are explaining WHY a metric is at its level or MOVED -- not just reporting it. Follow this playbook.
The slice (metric x grain x dimension) is the crux. Do the steps IN ORDER.

BE FAST -- MINIMISE SERIAL ROUND-TRIPS. Every metric_tree / define / run_sql / data_health is one slow
model+DB round-trip; a deep tree done one-node-at-a-time is needlessly slow. Get the tree in ONE call,
compute the whole driver split in ONE (or few) wide queries, and run all the independent DIMENSION slices
CONCURRENTLY. Aim for well under a dozen serial steps before you finish.

STEP 1 -- DATA SANITY (once, on what the headline rests on). Run data_health on the FACT table + the
join keys and slice columns the metric depends on -- once each. Look for a null spike, an orphan/
NULL-dropping join, a distinct collapse, a row-count/range jump. State what you found. Do NOT re-probe a
table you already checked (its result is in working memory).

STEP 2 -- GET THE DECOMPOSITION IN ONE CALL. metric_tree(metric) returns the WHOLE driver tree with
EVERY node's SQL/formula + additive-vs-multiplicative. That IS the definition of every driver -- do NOT
call define on the individual metrics; you already have their SQL from the tree. Then compute all the
driver values for BOTH compared periods in as FEW queries as possible -- ideally ONE wide SELECT
(SELECT year, <every leaf/driver expression>, ... GROUP BY year), not one query per node.

STEP 3 -- ATTRIBUTE THE CHANGE (contribution, not level -- this is the point of RCA). From the driver
values, compute each driver's CONTRIBUTION to the metric's change. For a multiplicative split
`metric = A x B` from period 1 to 2:
    A-effect = (A2 - A1) x B1 ;  B-effect = A2 x (B2 - B1) ;  interaction = (A2 - A1) x (B2 - B1)
For an additive split, each part's effect is its own period-over-period delta. This is cheap arithmetic
over your stored results -- do it LOCALLY with query_result; do NOT spawn a sub-agent for it, and NEVER
hardcode a contribution number. VERIFY the effects RECONCILE to the total change.

STEP 4 -- ATTRIBUTE ACROSS DIMENSIONS, CONCURRENTLY. If the question asks WHERE the move concentrated
(by category, region, income band, gender, ...), fan out ALL requested dimensions in ONE spawn_subagents
call -- ONE sub-agent per dimension, run at once. Each sub-agent computes the metric's period-over-period
change grouped by its dimension and returns the RANKED movers (a compact list). They inherit your intent,
data-health findings, and joins -- tell them to REUSE those and not re-explore. Never slice the dimensions
serially yourself, and never spawn a sub-agent for a single aggregate.

STEP 5 -- RANK, RECONCILE, REPORT. Name the largest-contribution driver as the proximate cause (its
dollar/point contribution and % of the total move), then the biggest movers per dimension. Confirm the
dimension contributions reconcile to the total. Report the driver decomposition, the ranked movers per
dimension, and flag any driver whose Step-1 data-health check was material. Then finish.
