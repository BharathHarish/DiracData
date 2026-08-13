"""MCP prompt templates (learn-database, executive-scorecard)."""
from __future__ import annotations


def prompt_learn_database(database: str = "") -> str:
    db = database or "<database>"
    return f"""\
You are authoring fabric for database `{db}` on the DiracData CATALOG MCP (LEARN mode).

Follow LEARN instructions exactly. Do not claim completion until completeness_check(db={db!r}).ok is true \
(and fabric_health is green for the catalog).

Steps:
1. use_database({db!r})
2. list_tables + describe_table / profile for every table
3. propose_* for grain, columns (access_recipe for nested types), joins (cardinality), metrics
4. metric_bind_check each metric; detect_boundary_convention on threshold-like columns
5. save_semantic_model
6. refresh_database_md (first paragraph = business-model narrative) then refresh_catalog_md
7. completeness_check + fabric_health; fix gaps; save_experience for durable gotchas
"""


def prompt_executive_scorecard(year_hint: str = "most recent complete year",
                               surface: str = "schema") -> str:
    if surface == "catalog":
        period_tool = (
            "confirm overlapping date ranges with profile or run_sql (MIN/MAX dates) "
            "before joining two time-bearing tables"
        )
    else:
        period_tool = "use temporal_coverage before period joins"
    return f"""\
Executive health scorecard playbook (QUERY mode) for the current DiracData schema/catalog.

Period: {year_hint} (confirm with calendar coverage first).

Cover three angles — for each: top-level number, then drivers, then concerns:
1. Money — net revenue, refund rate, where refunds hurt (channel / category / reason)
2. Marketing — spend efficiency, best/worst channels; apply ontology/channel_maps before ROI joins
3. Assortment — in-stock rate, stockout concentration (e.g. size), attribute completeness

Always: get_dialect first; bind metrics via metric_bind_check; prefer @metric:// and @table:// resources; \
{period_tool}; go beyond the ask with companions + gotchas.
"""


PROMPTS = {
    "learn-database": {
        "title": "Learn database fabric",
        "description": "Full LEARN workflow with completeness gates",
        "fn": prompt_learn_database,
    },
    "executive-scorecard": {
        "title": "Executive health scorecard",
        "description": "Money / marketing / assortment scorecard playbook",
        "fn": prompt_executive_scorecard,
    },
}
