"""Agent -- THE entry point. Assembles the single-loop analyst and runs one turn:

  triage (recall + classify: rca vs analytics, fast vs cold)
    -> metric-attribution SEED for an RCA (deterministic: the `attribute` primitive walks the driver
       tree + attributes every requested dimension, reconciled + cited, and injects the brief)
    -> frame intent (skipped when the RCA is already seeded; else tooled, resolving follow-ups)
    -> agentic model route (the garden: nano/mini/haiku/sonnet-5), escalating if a tier can't converge
    -> analyst loop over tools (navigate / run_sql / plan / attribute / subagents), gated finish
       (data-sanity + independent derivation review)
    -> remember (agentic write-back) -> append the full trace to transcript.md
    -> regenerate the running summary.md (agentic).

Every runtime constant comes from `Config`; every prompt from `prompts/`. Durable conversation
memory (transcript.md + summary.md) is optional -- pass a `Conversation` to `run` to enable it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from diracdata.utils.streaming import Sink, null_sink

from diracdata.agents.framing import frame_intent
from diracdata.agents.loop import run_loop
from diracdata.agents.summarizer import make_summarizer
from diracdata.agents.triage import make_triage
from diracdata.config import Config, Stage
from diracdata.models import ModelRegistry
from diracdata.routing import RouteSignals, make_router
from diracdata.memory.working_memory import WorkingMemory
from diracdata.memory.results import ResultStore
from diracdata.prompts import dialect_note, load_prompt
from diracdata.rca.attribution import build_attribution_tool, seed_attribution
from diracdata.agents.subagents import build_subagent_tool
from diracdata.tools import build_control_tools, build_tools, build_transcript_tool
from diracdata.agents.verify import FinishGate, make_sanity_gate, make_verifier, estate_dialects_note
from diracdata.experiences import MemoryConsolidator, make_curator

# The whole analyst prompt: identity + task + the few environment invariants. The golden-SQL checklist
# (sql_rules) lives on the VERIFIER that ENFORCES it, not stacked onto a competent SQL writer.
_CORE = load_prompt("analyst_core")


@dataclass
class Answer:
    question: str
    answer: str
    memory: WorkingMemory
    tokens: int = 0
    steps: int = 0
    verdict: dict | None = None


class Agent:
    """The recall-first, single-loop analyst: triage -> (attribution seed) -> frame -> route -> loop ->
    gated finish -> remember + summarize. One turn per `run`."""

    def __init__(self, *, model: Any, workspace: Any, engine: Any, result_store: ResultStore,
                 sink: Sink = null_sink, config: Config | None = None, max_steps: int | None = None,
                 value_cache: Any = None, asker: Any = None, frame: bool = True, subagents: bool = True,
                 max_subagent_depth: int | None = None, model_registry: ModelRegistry | None = None,
                 experience_book: Any = None, sources: Any = None, bindings: Any = None) -> None:
        self.model = model
        self.workspace = workspace
        self.engine = engine
        self.sources = sources          # optional SourceRegistry; None -> single-source (self.engine)
        self.bindings = bindings         # optional cross-source bindings for the estate map
        self.result_store = result_store
        self.sink = sink
        # Config is the single source of runtime constants; explicit args override just those two.
        self.config = config or Config()
        self.max_steps = max_steps if max_steps is not None else self.config.max_steps
        self.max_subagent_depth = (max_subagent_depth if max_subagent_depth is not None
                                   else self.config.subagent_max_depth)
        self.value_cache = value_cache
        self.asker = asker
        self.frame = frame
        self.subagents = subagents
        self._registry = model_registry or ModelRegistry(self.config)
        self._route = make_router(self.model, self.config, sink=self.sink)
        # Agentic memory (optional): a schema-scoped ExperienceBook + async curator. Off unless both
        # the flag is on AND a book is provided.
        self._book = experience_book
        self._consolidator = None
        if self.config.agentic_memory_enabled and experience_book is not None:
            self._consolidator = MemoryConsolidator(experience_book, sink=self.sink)
            self._curate = make_curator(self.model, self.config, sink=self.sink)

    def _route_signals(self, goal: str, memory: WorkingMemory) -> RouteSignals:
        """Facts the router reasons over: does a proven precedent exist, and the framed intent. Only
        assembled when the router is on."""
        ws = self.workspace
        if not self.config.router_enabled or ws is None:
            return RouteSignals()
        exact = getattr(ws, "exact_match", lambda _g: None)(goal) is not None
        slot = getattr(ws, "slot_match", lambda _g: None)(goal) is not None
        intent = (memory.confirmed_intent or {}).get("intent", "") if memory.confirmed_intent else ""
        return RouteSignals(exact_match=exact, slot_match=slot, intent=intent)

    def _run_analyst(self, plan: Any, tools: list, system_prompt: str, memory: WorkingMemory,
                     gate: Any, observe: Any, task: str | None = None) -> dict:
        """Run the analyst loop under a RunPlan. Router off -> the per-stage authoring model at the
        normal budget. Router on -> the plan's model + budget, with a shortcut hint when a strong
        precedent exists. An explicit `task` (the RCA brief or a recall seed) wins over the router's
        generic shortcut note."""
        if self.config.router_enabled and plan.authoring_profile:
            model = self._registry.get(plan.authoring_profile, max_tokens=plan.max_tokens,
                                       temperature=plan.temperature).model
            max_steps = plan.max_steps
            if task is None and plan.allow_shortcut:
                task = (f"Answer this question:\n{memory.goal}\n\nNOTE: a strong precedent for this exact "
                        "question exists among your examples -- adapt and verify it, do not re-explore.")
        else:
            model = self._stage_model(Stage.AUTHORING)
            max_steps = self.max_steps
        return run_loop(model=model, tools=tools, system_prompt=system_prompt, memory=memory,
                        sink=self.sink, max_steps=max_steps, finish_gate=gate, config=self.config,
                        observe=observe, task=task)

    def _stage_model(self, stage: Stage) -> Any:
        """The model for a stage. With no per-stage override the injected `model` is used as-is (so a
        fake model in tests works and nothing is rebuilt); a stage override is built + cached by the
        registry."""
        from diracdata.config import StageConfig
        if (self.config.stages.get(stage) or StageConfig()) == StageConfig():
            return self.model
        return self._registry.for_stage(stage).model

    def run(self, goal: str, conversation: Any = None) -> Answer:
        """Answer one question. Pass a `Conversation` to enable durable memory: the running summary
        is fed into triage/framing (so follow-ups + entities resolve), and after the turn the full
        trace is appended to transcript.md and the summary regenerated."""
        memory = WorkingMemory(goal=goal)
        events: list[dict] = []

        def _observe(phase: str):
            def hook(name: str, args: dict, result: str) -> None:
                events.append({"phase": phase, "tool": name, "args": args, "result": result})
            return hook

        data_tools = build_tools(workspace=self.workspace, engine=self.engine,
                                 result_store=self.result_store, memory=memory,
                                 value_cache=self.value_cache, asker=self.asker, sources=self.sources,
                                 max_rows=self.config.query_max_rows, config=self.config)
        if conversation is not None:                          # let both phases pull exact past detail
            data_tools = data_tools + [build_transcript_tool(conversation=conversation)]
        dnote = dialect_note(getattr(self.engine, "dialect", ""))
        learned = self._learned_context()
        estate = self._estate_context()

        # 1. TRIAGE -- recall + classify, before framing. Feed it the CONVERSATION SUMMARY first so a
        #    follow-up ("why is store preferred there?") resolves its pronouns and is classified WITH
        #    context, instead of going cold on a bare, context-free question.
        recent = conversation.summary() if conversation is not None else ""
        triage = make_triage(self._stage_model(Stage.FRAMING), sink=self.sink, config=self.config)
        tri = triage(goal, self.workspace, learned=learned, recent=recent)
        self.sink("triage", "info", f"task={tri['task_type']} lane={tri['lane']} :: {tri['reasoning']}")

        # METRIC ATTRIBUTION SEED (deterministic). When triage resolved the metric + periods + the
        # dimensions the user asked about, run the ONE `attribute` primitive up front: it walks the
        # driver tree AND attributes every REQUESTED dimension completely (retry + explicit
        # `unavailable`, never a silent drop), routing SQL through the result store (citable
        # result_ids). Its brief is injected for the analyst to NARRATE -- the requested slices always
        # run (bound, not "remembered"), numbers arrive pre-cited, and there is nothing left to re-derive.
        rca_seed = None
        if (tri["task_type"] == "rca" and self.config.rca_precompute_enabled and tri.get("rca_target")
                and self.workspace and self.workspace.semantic_layer):
            rca_seed = seed_attribution(
                target=tri["rca_target"], workspace=self.workspace, engine=self.engine,
                result_store=self.result_store, memory=memory, config=self.config, sink=self.sink)

        # 2. PROMPT -- the lean core is the whole analyst prompt (no RCA "how-to" skill: the attribution
        #    is a tool + a seeded brief, not a playbook the model has to follow).
        system_prompt = _CORE + "\n\n" + dnote
        if estate:
            system_prompt += "\n\n## " + estate
        if learned:
            system_prompt += ("\n\n## LEARNED KNOWLEDGE FOR THIS SCHEMA (reuse these patterns; honor the "
                              "gotchas/bindings; use RCA leads when investigating a metric)\n" + learned)
        extra = self._extra_context()   # base returns "" (no-op); AgentV6 injects the governed model
        if extra:
            system_prompt += "\n\n## " + extra

        # The verifier also needs the per-source dialects: in a multi-engine estate each query runs in
        # its source's dialect and combine_results runs on the reconciler -- else it flags valid
        # cross-source SQL as "won't execute / fabricated".
        verifier = make_verifier(self._stage_model(Stage.VERIFY), sink=self.sink,
                                 workspace=self.workspace,
                                 dialect_note=estate_dialects_note(self.sources, dnote), config=self.config)
        sanity = (make_sanity_gate(self._stage_model(Stage.VERIFY), sink=self.sink,
                                   workspace=self.workspace, config=self.config)
                  if self.config.sanity_gate_enabled else None)
        gate = FinishGate(memory=memory, verifier=verifier, sanity_verifier=sanity, config=self.config)
        tools = data_tools + build_control_tools(memory=memory, gate=gate)
        tools.extend(self._extra_tools(memory))   # base returns [] (no-op); AgentV6 adds model_* lookups
        sub_tokens: list[int] = []
        if self.subagents:
            tools.extend(build_subagent_tool(
                model=self._stage_model(Stage.AUTHORING), workspace=self.workspace, engine=self.engine,
                result_store=self.result_store, value_cache=self.value_cache, parent_memory=memory,
                system_prompt=system_prompt, sink=self.sink, asker=self.asker, max_steps=self.max_steps,
                depth=0, max_depth=self.max_subagent_depth, on_tokens=sub_tokens.append,
                dialect_note=dnote, config=self.config, sources=self.sources))
        # The `attribute` primitive as a TOOL: for a metric-RCA the analyst can root-cause a defined
        # metric across the dimensions it binds, on the fly ("now by state") -- same complete, cited
        # decomposition as the seed. Present whenever there is a semantic layer to attribute over.
        if tri["task_type"] == "rca" and (self.workspace and self.workspace.semantic_layer):
            tools.extend(build_attribution_tool(
                workspace=self.workspace, engine=self.engine, result_store=self.result_store,
                memory=memory, config=self.config, sink=self.sink))

        tokens = tri.get("tokens", 0)
        # A seeded RCA already HAS the intent (metric + periods + every requested dimension) and the
        # reconciled, cited answer in working memory -- so SKIP framing's open exploration (which would
        # re-derive a often-wrong direction, e.g. binding "age groups" to a raw column).
        if rca_seed:
            memory.confirmed_intent = {"intent": goal, "concepts": []}
        elif self.frame:
            self.sink("framing", "info", "framing intent")
            definitions = self.workspace.definitions_index() if self.workspace else ""
            if estate:
                definitions = (estate + "\n\n" + definitions) if definitions else estate
            tokens += frame_intent(model=self._stage_model(Stage.FRAMING), tools=data_tools,
                                   memory=memory, sink=self.sink, definitions=definitions,
                                   recent_turns=recent, learned=learned,
                                   max_steps=self.config.framing_max_steps, observe=_observe("framing"))

        # 3. RECALL SEED -- the RCA brief wins; else on the fast lane hand the analyst the precedent to
        #    adapt + verify (a learned pattern often has NO clean SQL, so seed off the precedent NAME too).
        task = None
        if rca_seed:
            task = f"Answer this question:\n{goal}\n\n{rca_seed.to_brief()}"
        elif tri["lane"] == "fast" and tri["precedent_sql"]:
            task = (f"Answer this question:\n{goal}\n\nA BLESSED PRECEDENT exists for this pattern -- ADAPT "
                    f"it (rebind the literals/period/values) and VERIFY it still holds; do not re-explore "
                    f"from scratch.\nQ: {tri['precedent_q'] or '(prior solved question)'}\nSQL: {tri['precedent_sql']}")
        elif tri["lane"] == "fast" and tri["precedent_q"]:
            task = (f"Answer this question:\n{goal}\n\nA LEARNED PATTERN matches this problem: "
                    f"'{tri['precedent_q']}'. It is in your LEARNED KNOWLEDGE for this schema -- ADAPT that "
                    f"KNOWN recipe (rebind the period/literals/segments, fill any {{placeholder}}) and VERIFY "
                    f"it; do NOT re-derive the approach or re-explore the schema from scratch.")

        # 4. MODEL ROUTE -- the agentic router picks the garden tier. It must SEE what triage saw (its
        #    task_type AND that a precedent exists) so a precedented RCA short-circuits to a cheaper tier
        #    instead of the top model. The finish gate stays the correctness authority; a tier that
        #    cannot converge is re-routed UPWARD (escalation).
        base_signals = self._route_signals(goal, memory)
        signals = replace(base_signals, task_type=tri["task_type"],
                          exact_match=base_signals.exact_match or tri["lane"] == "fast")
        plan, rtok = self._route(goal, signals)
        tokens += rtok
        if self.config.router_enabled:
            self.sink("router", "info", f"model={plan.authoring_profile or '(global)'} "
                                        f"max_steps={plan.max_steps} tokens={plan.max_tokens} :: {plan.reasoning}")
        analyst = _observe("analyst")
        out = self._run_analyst(plan, tools, system_prompt, memory, gate, analyst, task=task)
        tokens += out["tokens"]
        escalations = 0
        while (gate.result is None and self.config.router_enabled
               and escalations < self.config.router_max_escalations):
            escalations += 1
            plan, rtok = self._route(goal, signals, failed_profile=plan.authoring_profile)
            tokens += rtok
            self.sink("router", "info", f"escalating -> {plan.authoring_profile or '(global)'} :: {plan.reasoning}")
            # Continue, don't restart: the prior pass already populated WORKING MEMORY (plan + stored
            # results). Re-exploring is what burned tokens; tell the stronger model to build on it.
            cont = (f"CONTINUE a partially-completed analysis of:\n{goal}\n\nWORKING MEMORY already holds your "
                    f"prior plan, verified facts, and stored results (r1..). Do NOT re-run exploration, "
                    f"data_health, or queries you have already done -- REUSE those result_ids. Address the "
                    f"reviewer's last rejection reason, then finish.")
            out = self._run_analyst(plan, tools, system_prompt, memory, gate, analyst, task=cont)
            tokens += out["tokens"]
        tokens += gate.tokens + sum(sub_tokens)
        answer = out["text"].strip()
        if conversation is not None:
            tokens += self._record(conversation, goal, events, answer)
        return Answer(question=goal, answer=answer, memory=memory,
                      tokens=tokens, steps=out["steps"], verdict=out.get("verdict"))

    def _extra_context(self) -> str:
        """Hook for a subclass to inject extra system context (AgentV6 injects a tiny pointer to the
        governed model, retrieved on demand via _extra_tools). Base returns "" -> zero change."""
        return ""

    def _extra_tools(self, memory: Any) -> list:
        """Hook for a subclass to register extra tools (AgentV6 adds the model_* lookup tools over the
        governed semantic model). Base returns [] -> zero change to the current agent."""
        return []

    def _learned_context(self) -> str:
        """The curated schema memory (experiences.md) to inject into this turn -- reused patterns,
        bindings, gotchas, RCA leads. Empty unless agentic memory is on and a book exists."""
        if self.config.agentic_memory_enabled and self._book is not None:
            return self._book.read()
        return ""

    def _estate_context(self) -> str:
        """The estate map (sources + dialects + tables + verified bindings) for a MULTI-source run;
        empty for single-source (so nothing changes on the default path)."""
        if self.sources is None or len(self.sources.names()) <= 1:
            return ""
        from diracdata.context.estate import render_estate
        return render_estate(self.sources, default_name=getattr(self.engine, "name", None),
                             bindings=self.bindings)

    def flush_memory(self) -> None:
        """Synchronously drain any pending memory candidates -- call at process exit so a single-shot
        run finishes curating before it exits (between-turn curation stays async)."""
        if self._consolidator is not None:
            self._consolidator.drain(self._curate)

    def _record(self, conversation: Any, goal: str, events: list[dict], answer: str) -> int:
        """Persist the turn: append the full trace to transcript.md, then regenerate the running
        summary.md agentically from (previous summary + this turn). Returns summarizer tokens."""
        prev = conversation.summary()
        turn_md = conversation.append_turn(question=goal, events=events, answer=answer)
        self.sink("summarize", "info", "updating conversation summary")
        summarize = make_summarizer(self._stage_model(Stage.SUMMARIZE), sink=self.sink, config=self.config)
        new_summary, stok = summarize(prev, turn_md)
        conversation.set_summary(new_summary)
        # Agentic memory: enqueue this turn (instant, durable) and drain in the background -- the
        # curator folds any durable knowledge into experiences.md WITHOUT blocking the next turn.
        if self._consolidator is not None:
            self._consolidator.enqueue(turn_md)
            self._consolidator.drain_async(self._curate)
        return stok
