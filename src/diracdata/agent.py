"""V4Agent -- THE entry point. Assembles the single-loop analyst and runs one turn:

  frame intent (tooled, resolving follow-ups from conversation memory)
    -> analyst loop over tools (navigate / run_sql / plan / subagents), gated finish
       (plan-verified + faithful + independent verify + golden SQL rules)
    -> remember (agentic write-back)  -> append the full trace to transcript.md
    -> regenerate the running summary.md (agentic).

Every runtime constant comes from `Config`; every prompt from `prompts/`. Durable conversation
memory (transcript.md + summary.md) is optional -- pass a `Conversation` to `run` to enable it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from diracdata.utils.streaming import Sink, null_sink

from diracdata.agents.framing import frame_intent
from diracdata.agents.loop import run_loop
from diracdata.agents.summarizer import make_summarizer
from diracdata.config import Config, Stage
from diracdata.models import ModelRegistry
from diracdata.routing import RouteSignals, make_router
from diracdata.memory.working_memory import WorkingMemory
from diracdata.memory.results import ResultStore
from diracdata.prompts import dialect_note, load_prompt
from diracdata.agents.subagents import build_subagent_tool
from diracdata.tools import build_control_tools, build_tools, build_transcript_tool
from diracdata.agents.verify import FinishGate, make_verifier
from diracdata.experiences import MemoryConsolidator, make_curator

_SYSTEM_PROMPT = load_prompt("analyst") + "\n\n" + load_prompt("sql_rules")


@dataclass
class V4Answer:
    question: str
    answer: str
    memory: WorkingMemory
    tokens: int = 0
    steps: int = 0
    verdict: dict | None = None


class V4Agent:
    def __init__(self, *, model: Any, workspace: Any, engine: Any, result_store: ResultStore,
                 sink: Sink = null_sink, config: Config | None = None, max_steps: int | None = None,
                 value_cache: Any = None, asker: Any = None, frame: bool = True, subagents: bool = True,
                 max_subagent_depth: int | None = None, model_registry: ModelRegistry | None = None,
                 experience_book: Any = None) -> None:
        self.model = model
        self.workspace = workspace
        self.engine = engine
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
                     gate: Any, observe: Any) -> dict:
        """Run the analyst loop under a RunPlan. Router off -> the per-stage authoring model at the
        normal budget (today's path). Router on -> the plan's model + budget, with a shortcut hint
        when a strong precedent exists."""
        if self.config.router_enabled and plan.authoring_profile:
            model = self._registry.get(plan.authoring_profile, max_tokens=plan.max_tokens,
                                       temperature=plan.temperature).model
            max_steps = plan.max_steps
            task = None
            if plan.allow_shortcut:
                task = (f"Answer this question:\n{memory.goal}\n\nNOTE: a strong precedent for this exact "
                        "question exists among your examples -- adapt and verify it, do not re-explore.")
        else:
            model = self._stage_model(Stage.AUTHORING)
            max_steps = self.max_steps
            task = None
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

    def run(self, goal: str, conversation: Any = None) -> V4Answer:
        """Answer one question. Pass a `Conversation` to enable durable memory: the running summary
        is fed into framing (so follow-ups + entities resolve), and after the turn the full trace is
        appended to transcript.md and the summary regenerated."""
        memory = WorkingMemory(goal=goal)
        # The turn's transcript: every tool call across framing + analyst, tagged by phase.
        events: list[dict] = []

        def _observe(phase: str):
            def hook(name: str, args: dict, result: str) -> None:
                events.append({"phase": phase, "tool": name, "args": args, "result": result})
            return hook

        data_tools = build_tools(workspace=self.workspace, engine=self.engine,
                                 result_store=self.result_store, memory=memory,
                                 value_cache=self.value_cache, asker=self.asker,
                                 max_rows=self.config.query_max_rows)
        if conversation is not None:                          # let both phases pull exact past detail
            data_tools = data_tools + [build_transcript_tool(conversation=conversation)]
        dnote = dialect_note(getattr(self.engine, "dialect", ""))
        learned = self._learned_context()
        system_prompt = _SYSTEM_PROMPT + "\n\n" + dnote
        if learned:
            system_prompt += ("\n\n## LEARNED KNOWLEDGE FOR THIS SCHEMA (reuse these patterns; honor the "
                              "gotchas/bindings; use RCA leads when investigating a metric)\n" + learned)
        verifier = make_verifier(self._stage_model(Stage.VERIFY), sink=self.sink,
                                 workspace=self.workspace, dialect_note=dnote, config=self.config)
        gate = FinishGate(memory=memory, verifier=verifier)
        tools = data_tools + build_control_tools(memory=memory, gate=gate)
        sub_tokens: list[int] = []
        if self.subagents:
            tools.append(build_subagent_tool(
                model=self._stage_model(Stage.AUTHORING), workspace=self.workspace, engine=self.engine,
                result_store=self.result_store, value_cache=self.value_cache, parent_memory=memory,
                system_prompt=system_prompt, sink=self.sink, asker=self.asker, max_steps=self.max_steps,
                depth=0, max_depth=self.max_subagent_depth, on_tokens=sub_tokens.append,
                dialect_note=dnote, config=self.config))
        # Conversation memory (the running summary) resolves follow-ups into a standalone intent.
        recent = conversation.summary() if conversation is not None else ""
        tokens = 0
        if self.frame:
            self.sink("framing", "info", "framing intent")
            definitions = self.workspace.definitions_index() if self.workspace else ""
            tokens += frame_intent(model=self._stage_model(Stage.FRAMING), tools=data_tools,
                                   memory=memory, sink=self.sink,
                                   definitions=definitions, recent_turns=recent, learned=learned,
                                   max_steps=self.config.framing_max_steps, observe=_observe("framing"))
        # The main model AGENTICALLY chooses the analyst's model + budget from the catalog. The finish
        # gate stays the correctness authority; if the chosen model can't converge, re-route upward
        # (asking for a stronger model than the one that failed).
        signals = self._route_signals(goal, memory)
        plan, rtok = self._route(goal, signals)
        tokens += rtok
        if self.config.router_enabled:
            self.sink("router", "info", f"model={plan.authoring_profile or '(global)'} "
                                        f"max_steps={plan.max_steps} tokens={plan.max_tokens} "
                                        f"shortcut={plan.allow_shortcut} :: {plan.reasoning}")
        analyst = _observe("analyst")
        out = self._run_analyst(plan, tools, system_prompt, memory, gate, analyst)
        tokens += out["tokens"]
        escalations = 0
        while (gate.result is None and self.config.router_enabled
               and escalations < self.config.router_max_escalations):
            escalations += 1
            plan, rtok = self._route(goal, signals, failed_profile=plan.authoring_profile)
            tokens += rtok
            self.sink("router", "info", f"escalating -> {plan.authoring_profile or '(global)'} "
                                        f":: {plan.reasoning}")
            out = self._run_analyst(plan, tools, system_prompt, memory, gate, analyst)
            tokens += out["tokens"]
        tokens += gate.tokens + sum(sub_tokens)
        answer = out["text"].strip()
        if conversation is not None:
            tokens += self._record(conversation, goal, events, answer)
        return V4Answer(question=goal, answer=answer, memory=memory,
                        tokens=tokens, steps=out["steps"], verdict=out.get("verdict"))

    def _learned_context(self) -> str:
        """The curated schema memory (experiences.md) to inject into this turn -- reused patterns,
        bindings, gotchas, RCA leads. Empty unless agentic memory is on and a book exists."""
        if self.config.agentic_memory_enabled and self._book is not None:
            return self._book.read()
        return ""

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
