"""Learner -- the one-call COMPILER facade. `Learner(schema, model).learn()` runs the agentic learning
harness over the estate and WRITES the compiled context to the object store (the artifacts the query
agent consumes on demand): metadata_descriptions.json (+ complex access recipes) + value_domains.json,
with semantic_model.yaml kept as the governance record. Learn once; read many via diracdata.context.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from diracdata.config import settings_from_env
from diracdata.utils.streaming import Sink, null_sink


class Learner:
    def __init__(self, *, schema: str, model: Any, settings: Any = None, subagents: bool = True,
                 max_steps: int | None = None, engine: Any = None) -> None:
        self.schema = schema
        self.settings = settings if settings is not None else settings_from_env()
        if isinstance(model, str):                       # a profile id -> set it as the base + build
            self.settings = replace(self.settings, agent_model_profile=model)
        self._model_arg = model
        self.subagents = subagents
        self.max_steps = max_steps
        # An injected engine lets non-parquet sources (e.g. an ATTACHed SQLite database for a Spider
        # 2.0 catalog) be learned with the same harness. When None, we build the parquet lake engine.
        self._engine = engine

    def _model(self) -> Any:
        m = self._model_arg
        if hasattr(m, "build"):                          # a model_providers Provider
            return m.build()
        if isinstance(m, str):
            from diracdata.models import chat_model
            return chat_model(m, settings=self.settings)
        return m                                         # already a chat model

    def learn(self, *, context: str = "", sink: Sink = null_sink) -> dict:
        from diracdata.context.fabric import context_store_from_settings
        from diracdata.engines import DuckDBEngine
        from diracdata.learning.agent2 import LearningCompiler

        engine = self._engine if self._engine is not None else \
            DuckDBEngine.from_settings(self.settings, self.schema)
        store = context_store_from_settings(self.settings)
        if not context:                                  # default: fold in any blessed metric tree
            context = self._blessed_context(store)

        compiler = LearningCompiler(engine=engine, model=self._model(), sink=sink,
                                    config=self.settings, max_steps=self.max_steps,
                                    subagents=self.subagents)
        sm, out = compiler.compile(self.schema, context=context)

        # V3-S1: enrich every recorded join with BEHAVIOURAL facts (match rate, fan-out, LEFT/INNER
        # hint). Deterministic MEASUREMENT via the engine; agent judgment untouched.
        from diracdata.learning.join_facts import enrich_joins
        try:
            n = enrich_joins(model=sm, engine=engine)
            sink("learn", "info", f"enriched {n}/{len(sm.joins)} joins with behavioural facts")
        except Exception as exc:  # noqa: BLE001
            sink("learn", "info", f"join enrichment skipped: {type(exc).__name__}: {exc}")

        # V3-S2: verify each COMPLEX column's recipe by generating a CTE-staged runnable SELECT and
        # running it; the working snippet lands on the column so the analyst can copy it verbatim.
        from diracdata.learning.recipe_verify import enrich_recipes
        try:
            r = enrich_recipes(model=sm, engine=engine)
            n_complex = sum(1 for cmap in sm.columns.values() for cd in cmap.values()
                            if isinstance(cd, dict) and cd.get("access_recipe"))
            sink("learn", "info", f"verified {r}/{n_complex} complex-column recipes are runnable")
        except Exception as exc:  # noqa: BLE001
            sink("learn", "info", f"recipe verification skipped: {type(exc).__name__}: {exc}")

        # PRIMARY output = the artifacts the base agent reads on demand (describe_columns / find_examples).
        meta = sm.to_metadata_descriptions()
        cur_meta = store.get(self.schema, "metadata_descriptions.json") or {}
        for t, cols in meta["columns"].items():
            cur_meta.setdefault("columns", {}).setdefault(t, {}).update(cols)
        cur_meta.setdefault("tables", {}).update(meta["tables"])
        store.put(self.schema, "metadata_descriptions.json", cur_meta)
        domains = sm.to_value_domains()
        if domains:
            cur_dom = store.get(self.schema, "value_domains.json") or {}
            for t, cols in domains.items():
                cur_dom.setdefault(t, {}).update(cols)
            store.put(self.schema, "value_domains.json", cur_dom)
        store._store.write_text(store._fabric_key(self.schema, "semantic_model.yaml"), sm.to_yaml(),
                                content_type="text/yaml")
        cov = sm.coverage({t: engine.list_columns(t) for t in engine.list_tables()})
        store.put(self.schema, "coverage_report.json", cov)
        return {"coverage": cov, "tokens": out.get("tokens"), "steps": out.get("steps")}

    def _blessed_context(self, store: Any) -> str:
        for name in ("semantic_layer.yaml", "semantic_layer.yml"):
            if store.has(self.schema, name):
                return ("BLESSED METRICS / DIMENSIONS (align to these, do not contradict):\n"
                        + (store.read_text(self.schema, name) or "")[:6000])
        return ""
