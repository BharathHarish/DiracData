"""Context -- the READ facade over a schema's compiled semantic context (loaded from the object store).

`Context.load("ecommerce")` gives one object that answers the questions the analyst (and, later, the
MCP server) asks of the learned context: what tables/columns exist, a column's meaning + nested access
recipe, where a concept lives (grep), the join graph + cardinality, the governed metrics/dimensions,
and proven example queries. It reads only -- the learning agent (diracdata.learning) writes.
"""

from __future__ import annotations

from typing import Any


class Context:
    def __init__(self, *, schema: str, store: Any, workspace: Any, value_cache: Any,
                 settings: Any) -> None:
        self.schema = schema
        self.store = store                 # ContextStore (object store)
        self.workspace = workspace
        self.value_cache = value_cache
        self.settings = settings
        self._index = None                 # lazily built SemanticModelIndex over semantic_model.yaml

    @classmethod
    def load(cls, schema: str, *, settings: Any = None) -> "Context":
        if settings is None:
            from diracdata.config import settings_from_env
            settings = settings_from_env()
        from diracdata.context.fabric import context_store_from_settings
        from diracdata.context.valuecache import ColumnValueCache
        from diracdata.context.workspace import Workspace
        store = context_store_from_settings(settings)
        workspace = Workspace.from_store(store=store, schema=schema)
        return cls(schema=schema, store=store, workspace=workspace,
                   value_cache=ColumnValueCache(store, schema), settings=settings)

    # -- model-index-backed reads (grep / describe / joins / metrics) -------------------------------
    def _model_index(self):
        if self._index is None:
            import yaml
            from diracdata.learning.model_index import SemanticModelIndex
            sm = {}
            if self.store.has(self.schema, "semantic_model.yaml"):
                sm = yaml.safe_load(self.store.read_text(self.schema, "semantic_model.yaml")) or {}
            self._index = SemanticModelIndex(sm)
        return self._index

    def has_model(self) -> bool:
        return not self._model_index().empty()

    def tables(self) -> str:
        return self._model_index().tables()

    def describe(self, table: str) -> str:
        return self._model_index().describe(table)

    def search(self, pattern: str) -> str:
        return self._model_index().search(pattern)

    def joins(self, table: str) -> str:
        return self._model_index().joins(table)

    def metric(self, name: str = "") -> str:
        return self._model_index().metric(name)

    def dimension(self, name: str = "") -> str:
        return self._model_index().dimension(name)

    # -- workspace-backed reads (column detail w/ recipe, examples) ---------------------------------
    def column(self, table: str, column: str) -> dict | None:
        """A column's meaning + value domain, incl. the NESTED/COMPLEX access recipe if any."""
        return self.workspace.column_detail(table, column)

    def find_examples(self, query: str, limit: int = 5) -> list:
        return self.workspace.find_examples(query, limit=limit)
