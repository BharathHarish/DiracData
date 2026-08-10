"""AgentV6 -- the query agent that consumes the LEARNED GOVERNED SEMANTIC MODEL (semantic_model.yaml
compiled by scripts/learn2.py, stored in the object store / MinIO). Everything about the base Agent is
reused verbatim; V6 changes two hooks only, so the current Agent cannot regress:

- `_extra_context()` injects a TINY pointer (counts + how to look things up), NOT the whole model. The
  model is 100KB+ on a real estate -- dumping it every turn is pure token overhead (measured: neutral-
  to-worse on flat schemas). See memory `v6-efficiency-is-nested-specific`.
- `_extra_tools()` registers the `model_*` lookup tools (SemanticModelIndex) so the analyst RETRIEVES
  grain / join cardinality / complex-column access recipes on demand, like grep -- only what the query
  touches, cited. Retrieval scales to any warehouse size; whole-model injection does not."""

from __future__ import annotations

from typing import Any

from diracdata.agent import Agent
from diracdata.learning.model_index import SemanticModelIndex, build_model_lookup_tools


class AgentV6(Agent):
    def __init__(self, *, semantic_model: dict | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.semantic_model = semantic_model or {}
        self._index = SemanticModelIndex(self.semantic_model)

    def _extra_context(self) -> str:
        return self._index.header()

    def _extra_tools(self, memory: Any) -> list:
        if self._index.empty():
            return []
        return build_model_lookup_tools(index=self._index)
