"""LearningCompiler -- the agentic learning agent. Runs the query agent's harness (WorkingMemory +
Plan + run_loop + subagents + an agentic finish gate) to build a governed SemanticModel via write
tools, reviewed for completeness by an agentic FABRIC REVIEWER. One outer loop: plan -> build ->
verify -> (gaps become more work) until the reviewer is satisfied. No deterministic gates."""

from __future__ import annotations

from typing import Any

from diracdata.agents.loop import run_loop
from diracdata.config import Config
from diracdata.learning.compiler import SemanticModel, build_model_tools
from diracdata.learning.tools import build_learning_tools
from diracdata.runtime.working_memory import WorkingMemory
from diracdata.prompts import load_prompt
from diracdata.streaming import collect
from diracdata.utils.streaming import Sink, loads_json, null_sink

_DEFAULTS = Config()
_CORE = load_prompt("learn_core")
_REVIEW = load_prompt("learn_review")


def schema_listing(engine: Any) -> tuple[dict, str]:
    """(schema_cols {table:[col]}, a listing string with TYPES so complex columns are visible)."""
    cols: dict = {}
    lines = []
    for t in engine.list_tables():
        try:
            rows = engine.query(f'DESCRIBE SELECT * FROM "{t}"', 1000).rows
            typed = [(r[0], r[1]) for r in rows]
        except Exception:  # noqa: BLE001
            typed = [(c, "?") for c in engine.list_columns(t)]
        cols[t] = [c for c, _ in typed]
        parts = []
        for c, ty in typed:
            complex_ = any(k in (ty or "").upper() for k in ("STRUCT", "[]", "MAP", "JSON"))
            parts.append(f"{c} {ty}" + (" <<COMPLEX: profile_column + record access_recipe>>" if complex_ else ""))
        lines.append(f"- {t} ({len(typed)} cols): " + "; ".join(parts))
    return cols, "\n".join(lines)


def make_fabric_reviewer(*, model: SemanticModel, schema_cols: dict, review_model: Any,
                         sink: Sink = null_sink, config: Config = _DEFAULTS):
    """Agentic completeness/grounding reviewer. Reads the built model + the coverage and JUDGES whether
    it is complete (every table w/ grain, every column described, complex columns carrying an access
    recipe, joins classified) -- returns (verdict, tokens). Gaps come back as the reject reason."""
    from langchain_core.messages import HumanMessage, SystemMessage

    def verify(answer: str, memory: WorkingMemory) -> tuple[dict, int]:
        cov = model.coverage(schema_cols)
        complex_missing_recipe = []
        for t, cmap in model.columns.items():
            for c, d in cmap.items():
                # a complex column recorded without a recipe is a likely gap (reviewer judges)
                if "access_recipe" not in d and "nested" in (d.get("long", "").lower()):
                    complex_missing_recipe.append(f"{t}.{c}")
        payload = {"schema": model.schema, "coverage": cov,
                   "model_state": model.render(schema_cols),
                   "complex_columns_maybe_missing_recipe": complex_missing_recipe[:20],
                   "agent_finish_note": answer[:600]}
        out = collect(model=review_model, stage="learn-review", sink=sink, config=config,
                      messages=[SystemMessage(content=_REVIEW),
                                HumanMessage(content=__import__("json").dumps(payload, default=str))])
        v = loads_json(out["text"]) or {}
        ok = bool(v.get("ok"))
        return ({"ok": ok, "reason": str(v.get("reason") or "")[:600], "ambiguity": False},
                out["tokens"])

    return verify


class FabricGate:
    """The learning finish gate: on `finish`, run ONLY the agentic fabric reviewer over the built model
    (the artifact IS the model, not cited query results -- so NO result_id/plan-verified pre-checks the
    query-agent gate has). Accept when the reviewer says complete; after too many rejects, accept the
    best with the reviewer's unresolved gaps surfaced (never dead-loop). Interface run_loop expects."""

    def __init__(self, *, memory: Any, reviewer: Any, config: Config) -> None:
        self.memory = memory
        self.reviewer = reviewer
        self.config = config
        self.result: dict | None = None
        self.tokens = 0
        self._rejects = 0
        self._max = config.verify_max_rejects
        self.best_answer = ""

    def submit(self, answer: str, result_ids: list | None = None) -> str:
        answer = (answer or "").strip()
        if not answer:
            return "REJECTED: call finish with a one-line summary once the model is complete."
        self.best_answer = answer
        verdict, tok = self.reviewer(answer, self.memory)
        self.tokens += tok
        if verdict.get("ok"):
            self.result = {"answer": answer, "result_ids": [], "verdict": verdict}
            return "ACCEPTED"
        self._rejects += 1
        if self._rejects >= self._max:
            self.result = {"answer": answer + f"\n\n> unresolved gaps: {verdict.get('reason')}",
                           "result_ids": [], "verdict": {**verdict, "ok": True}}
            return "ACCEPTED"
        return (f"REJECTED [completeness]: {verdict.get('reason')} Close exactly these gaps with your "
                "write tools (describe the missing columns/tables, add access recipes, record joins), "
                "then call finish again.")

    def finalize_best(self, note: str) -> bool:
        if self.result is None and self.best_answer:
            self.result = {"answer": self.best_answer, "result_ids": [], "verdict": {"ok": True, "note": note}}
        return self.result is not None


class LearningCompiler:
    def __init__(self, *, engine: Any, model: Any, sink: Sink = null_sink,
                 config: Config | None = None, max_steps: int | None = None,
                 review_model: Any = None, subagents: bool = True) -> None:
        self.engine = engine
        self.model = model
        self.review_model = review_model or model
        self.sink = sink
        self.config = config or Config()
        self.max_steps = max_steps if max_steps is not None else self.config.max_steps
        self.subagents = subagents

    def compile(self, schema: str, *, context: str = "") -> tuple[SemanticModel, dict]:
        schema_cols, listing = schema_listing(self.engine)
        sm = SemanticModel(schema=schema)
        memory = WorkingMemory(goal=f"Compile the governed SEMANTIC MODEL for schema '{schema}'.")

        system_prompt = _CORE + "\n\n## SCHEMA (types shown; COMPLEX columns need a profiled access recipe)\n" + listing
        if context:
            system_prompt += "\n\n## INPUT ARTIFACTS (blessed definitions + how analysts really query)\n" + context

        reviewer = make_fabric_reviewer(model=sm, schema_cols=schema_cols, review_model=self.review_model,
                                        sink=self.sink, config=self.config)
        gate = FabricGate(memory=memory, reviewer=reviewer, config=self.config)

        from diracdata.tools import build_control_tools
        tools = (build_learning_tools(engine=self.engine)
                 + build_model_tools(model=sm)
                 + build_control_tools(memory=memory, gate=gate))
        sub_tokens: list[int] = []
        if self.subagents:
            from diracdata.learning.subagent import build_learning_subagent_tool
            tools.extend(build_learning_subagent_tool(
                model=sm, engine=self.engine, model_llm=self.model, sink=self.sink,
                config=self.config, max_steps=self.config.learn_max_steps,
                on_tokens=sub_tokens.append))

        task = (f"Compile the semantic model for schema '{schema}'. Work a plan (plan_update): describe "
                f"EVERY table (with a verified grain) and EVERY column (complex columns need an access "
                f"recipe from profile_column), record joins with verified cardinality, then define the "
                f"key metrics/dimensions. When the model is complete, call finish; a reviewer checks it.")
        out = run_loop(model=self.model, tools=tools, system_prompt=system_prompt, memory=memory,
                       sink=self.sink, max_steps=self.max_steps, finish_gate=gate, config=self.config,
                       task=task)
        out["tokens"] = out.get("tokens", 0) + gate.tokens + sum(sub_tokens)
        return sm, out
