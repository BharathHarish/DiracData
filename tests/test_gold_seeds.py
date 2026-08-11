"""V3-S4 unit: seed gold_pairs.json in the object store is picked up by Workspace.from_store."""

from __future__ import annotations

from typing import Any

from diracdata.context.workspace import Workspace


class _FakeStore:
    """Minimal ContextStore stand-in: returns whatever we prime it with per key."""
    def __init__(self, blobs: dict) -> None:
        self._b = blobs

    def get(self, schema: str, name: str, default: Any = None) -> Any:
        return self._b.get(f"{schema}/{name}", default)

    def has(self, schema: str, name: str) -> bool:
        return f"{schema}/{name}" in self._b

    def read_text(self, schema: str, name: str, default: Any = None) -> Any:
        return self._b.get(f"{schema}/{name}", default)


def test_from_store_loads_gold_pairs_from_object_store():
    store = _FakeStore({
        "s/metadata_descriptions.json": {"tables": {"campaigns": {"short": "c"}},
                                          "columns": {"campaigns": {"touchpoints": {"short": "tp"}}}},
        "s/value_domains.json": {},
        "s/join_graph.json": [],
        "s/gold_pairs.json": [
            {"nl_query": "best click-through-rate channel from touchpoints",
             "sql": "SELECT tp.channel FROM (SELECT UNNEST(touchpoints) tp FROM campaigns)"},
            {"nl_query": "campaign to merchandise", "sql": "SELECT * FROM campaigns"},
        ],
    })
    ws = Workspace.from_store(store=store, schema="s")
    hits = ws.find_examples("channel click-through", limit=5)
    assert hits, "gold seeds must surface for a matching NL query"
    assert any("touchpoints" in h.sql for h in hits)


def test_missing_gold_pairs_is_a_no_op():
    """Zero-regression: schemas without gold_pairs.json still work (empty list is the default)."""
    store = _FakeStore({"s/metadata_descriptions.json": {}, "s/value_domains.json": {},
                         "s/join_graph.json": []})
    ws = Workspace.from_store(store=store, schema="s")
    assert ws.find_examples("anything") == []
