"""SemanticModelIndex -- the governed model as a queryable index (grep/lookup), not a blob."""

from diracdata.learning.model_index import SemanticModelIndex, build_model_lookup_tools

SM = {
    "schema": "retail_analytics",
    "models": {
        "online_purchases": {
            "grain": "one row per order line item", "kind": "fact",
            "columns": {
                "net_paid": {"short": "Net amount paid for the line", "long": "revenue after discount"},
                "fulfillment": {"short": "nested shipments", "long": "nested",
                                "access_recipe": "UNNEST(UNNEST(fulfillment.shipments).items).sku"},
            },
            "measures": [{"name": "online_revenue", "agg": "sum", "additive": True}],
        },
        "addresses": {"grain": "one row per address", "kind": "dimension",
                      "columns": {"state": {"short": "US state (2-letter)"}}},
    },
    "relationships": [
        {"left": "online_purchases", "left_keys": ["billing_address_ref"], "right": "addresses",
         "right_keys": ["address_record"], "cardinality": "many_to_one", "verified_by": "0% orphan"},
    ],
    "metrics": {"online_revenue": {"description": "sum of net_paid", "sql": "SUM(net_paid)"}},
    "dimensions": {"billing_state": {"expr": "addresses.state",
                                     "via": ["online_purchases.billing_address_ref = addresses.address_record"]}},
}


def test_header_is_tiny_pointer_not_blob():
    idx = SemanticModelIndex(SM)
    h = idx.header()
    assert "2 tables" in h and "model_search" in h
    # the ACTUAL recipe string is NOT dumped -- it is retrieved on demand via model_lookup
    assert "UNNEST(UNNEST(fulfillment.shipments).items).sku" not in h
    assert "net_paid" not in h and "billing_state" not in h   # no column/dim payload in the header


def test_empty_model_has_no_header_no_tools():
    idx = SemanticModelIndex({})
    assert idx.empty() and idx.header() == ""


def test_lookup_column_returns_access_recipe():
    idx = SemanticModelIndex(SM)
    out = idx.lookup("online_purchases.fulfillment")
    assert "UNNEST(UNNEST(fulfillment.shipments).items).sku" in out


def test_lookup_unknown_column_suggests_neighbours():
    idx = SemanticModelIndex(SM)
    assert "no column 'net_pad'" in idx.lookup("online_purchases.net_pad")
    assert "net_paid" in idx.lookup("online_purchases.net_pad")   # near-match suggested


def test_search_greps_names_and_descriptions():
    idx = SemanticModelIndex(SM)
    hits = idx.search("state")
    assert "addresses.state" in hits and "dimension:billing_state" in hits


def test_search_regex_and_substring_fallback():
    idx = SemanticModelIndex(SM)
    assert "net_paid" in idx.search("net_.*")          # valid regex
    assert "no matches" in idx.search("zzzz_nope")     # substring miss


def test_joins_reports_cardinality():
    idx = SemanticModelIndex(SM)
    out = idx.joins("addresses")
    assert "many_to_one" in out and "online_purchases" in out


def test_describe_lists_columns_and_joins():
    idx = SemanticModelIndex(SM)
    out = idx.describe("online_purchases")
    assert "grain: one row per order line item" in out
    assert "net_paid" in out and "online_revenue(sum)" in out
    assert "many_to_one" in out


def test_tool_set_names():
    idx = SemanticModelIndex(SM)
    names = {t.name for t in build_model_lookup_tools(index=idx)}
    assert names == {"model_tables", "model_describe", "model_lookup", "model_search",
                     "model_joins", "model_metric", "model_dimension"}
