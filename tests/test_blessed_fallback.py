"""V3-S5 unit: Context.metric()/dimension() fall back to the blessed semantic_layer
when the learned model doesn't have the name. Substrate-only; all surfaces benefit."""

from __future__ import annotations

from types import SimpleNamespace

from diracdata.context.reader import Context


def _ctx(*, learned: dict, blessed: dict) -> Context:
    ws = SimpleNamespace(semantic_layer=blessed)
    ctx = Context(schema="s", store=None, workspace=ws, value_cache=None, settings=None)
    # short-circuit the lazy loader with a fake index
    from diracdata.learning.model_index import SemanticModelIndex
    ctx._index = SemanticModelIndex({"schema": "s", "models": {}, "relationships": [],
                                     "metrics": learned.get("metrics", {}),
                                     "dimensions": learned.get("dimensions", {})})
    return ctx


def test_blessed_metric_surfaces_when_learned_lacks_it():
    ctx = _ctx(learned={"metrics": {"net_revenue": {"description": "learned"}}},
                blessed={"metrics": {"ctr": {"description": "clicks/impressions", "formula": "clicks/impressions"}}})
    out = ctx.metric("ctr")
    assert out.startswith("metric ctr (blessed):")
    assert "clicks/impressions" in out


def test_learned_metric_wins_when_both_have_it():
    ctx = _ctx(learned={"metrics": {"ctr": {"description": "learned ctr"}}},
                blessed={"metrics": {"ctr": {"description": "blessed ctr"}}})
    out = ctx.metric("ctr")
    assert "learned ctr" in out and "blessed" not in out


def test_listing_appends_blessed_only_names():
    ctx = _ctx(learned={"metrics": {"a": {"description": "x"}}},
                blessed={"metrics": {"ctr": {}, "impressions": {}}})
    out = ctx.metric("")
    assert "ctr" in out and "impressions" in out and "blessed only" in out


def test_dimension_fallback_symmetric():
    ctx = _ctx(learned={"dimensions": {"gender": {"expr": "clients.gender"}}},
                blessed={"dimensions": {"channel": {"expr": "t.channel", "primary": True}}})
    assert "dimension channel (blessed)" in ctx.dimension("channel")


def test_no_blessed_layer_is_no_op():
    """Zero-regression: schemas without a blessed layer render exactly as before."""
    ctx = _ctx(learned={"metrics": {"net_revenue": {}}}, blessed={})
    out = ctx.metric("nope")
    assert out.startswith("no metric")     # unchanged legacy render
