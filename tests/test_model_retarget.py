"""The retarget: a compiled SemanticModel folds into the artifacts the base agent consumes on-demand
(metadata_descriptions.json / value_domains.json), with COMPLEX access recipes embedded in the long
description describe_columns serves. No separate semantic layer needed at query time."""

from diracdata.learning.compiler import SemanticModel

DOC = {
    "schema": "fintech_complex",
    "models": {
        "orders": {
            "grain": "one row per order", "kind": "fact", "short": "orders", "long": "one order",
            "columns": {
                "amount": {"short": "order amount", "long": "gross order amount in cents"},
                "fulfillment": {"short": "shipments", "long": "struct of shipments",
                                "access_recipe": "UNNEST(UNNEST(fulfillment.shipments).items).sku",
                                "value_domain": {"complete": False, "values": ["WH-CENTRAL"]}},
            },
        },
    },
    "relationships": [], "metrics": {}, "dimensions": {},
}


def test_from_doc_roundtrips_columns_and_grain():
    sm = SemanticModel.from_doc(DOC)
    assert sm.tables["orders"]["grain"] == "one row per order"
    assert set(sm.columns["orders"]) == {"amount", "fulfillment"}


def test_metadata_embeds_complex_access_recipe():
    sm = SemanticModel.from_doc(DOC)
    meta = sm.to_metadata_descriptions()
    ful = meta["columns"]["orders"]["fulfillment"]["long_description"]
    assert "NESTED/COMPLEX" in ful
    assert "UNNEST(UNNEST(fulfillment.shipments).items).sku" in ful       # recipe verbatim
    # a plain scalar column carries no recipe noise
    assert "NESTED/COMPLEX" not in meta["columns"]["orders"]["amount"]["long_description"]


def test_metadata_shape_matches_consumed_format():
    meta = SemanticModel.from_doc(DOC).to_metadata_descriptions()
    assert set(meta) == {"tables", "columns"}
    assert "Grain:" in meta["tables"]["orders"]["long_description"]
    assert set(meta["columns"]["orders"]["amount"]) == {"short_description", "long_description"}


def test_value_domains_extracted_only_when_present():
    dom = SemanticModel.from_doc(DOC).to_value_domains()
    assert dom["orders"]["fulfillment"]["values"] == ["WH-CENTRAL"]
    assert "amount" not in dom.get("orders", {})     # no domain recorded -> agent falls back to profiling
