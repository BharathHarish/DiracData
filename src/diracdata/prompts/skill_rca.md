## METRIC ROOT-CAUSE

This asks WHY a metric moved. DELEGATE the decomposition -- don't hand-walk it:
`spawn_metric_rca(metric, period_a, period_b, dimensions=[...])` runs a specialist that walks the driver
tree, computes each driver's EXACT contribution (reconciled), and ranks the movers per dimension.

Your job is to VERIFY, not redo: check the fact table's data_health if not already done, confirm the
leading driver makes business sense, optionally spot-check one number with run_sql. Then finish with the
driver decomposition (each driver's contribution + % of the move) + the ranked movers per dimension + a
CHECKS line.
