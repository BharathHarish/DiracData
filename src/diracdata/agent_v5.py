"""V5Agent -- the recall-first, skill-loading evolution of V4Agent.

Same FREE machinery as v4 (framing, the analyst ReAct loop, the finish gate, the independent verifier,
sub-agents, the result store) -- but a different ORCHESTRATION:

  1. TRIAGE (recall + classify), BEFORE framing: one cheap model call scores the question against the
     workspace's precedents and classifies the analytical task.
       - task_type: "rca" (root-cause of a defined/decomposable metric) vs "analytics" (everything else).
       - lane: "fast" (a blessed precedent to adapt + verify) vs "cold".
  2. PROGRESSIVE PROMPT: a LEAN analyst core is always loaded; the Metric-RCA SKILL body is appended
     ONLY when task_type == "rca". Ordinary analytics never carries the RCA machinery (cheaper, and the
     data-sanity/attribution guidance is prominent exactly where it matters).
  3. RECALL SEED: on the fast lane the precedent SQL is injected as an adapt-and-verify task, so the
     analyst doesn't re-explore from scratch.

V4Agent is untouched -- v5 subclasses it and overrides `run`, reusing __init__, the per-stage models,
the estate/learned context, the record/learn writeback, and every free helper. Nothing about v4 changes.
"""

from __future__ import annotations

from typing import Any

from diracdata.agent import V4Agent, V4Answer
from diracdata.agents.framing import frame_intent
from diracdata.agents.loop import run_loop
from diracdata.agents.subagents import build_subagent_tool
from diracdata.agents.triage import make_triage
from diracdata.agents.verify import FinishGate, make_sanity_gate, make_verifier, estate_dialects_note
from diracdata.config import Stage
from diracdata.memory.working_memory import WorkingMemory
from diracdata.prompts import dialect_note, load_prompt
from diracdata.tools import build_control_tools, build_tools, build_transcript_tool

_CORE = load_prompt("analyst_core") + "\n\n" + load_prompt("sql_rules")
_RCA_SKILL = load_prompt("skill_rca")


class V5Agent(V4Agent):
    """Recall-first, one-skill (Metric-RCA), progressive-disclosure analyst. Everything else is the
    v4 core loop, reused verbatim."""

    def run(self, goal: str, conversation: Any = None) -> V4Answer:
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
        if conversation is not None:
            data_tools = data_tools + [build_transcript_tool(conversation=conversation)]
        dnote = dialect_note(getattr(self.engine, "dialect", ""))
        learned = self._learned_context()
        estate = self._estate_context()

        # 1. TRIAGE -- recall + classify, before framing.
        triage = make_triage(self._stage_model(Stage.FRAMING), sink=self.sink, config=self.config)
        tri = triage(goal, self.workspace)
        self.sink("triage", "info", f"task={tri['task_type']} lane={tri['lane']} :: {tri['reasoning']}")

        # 2. PROGRESSIVE PROMPT -- lean core always; the RCA skill body only for a metric-RCA.
        system_prompt = _CORE + "\n\n" + dnote
        if tri["task_type"] == "rca":
            system_prompt += "\n\n" + _RCA_SKILL
        if estate:
            system_prompt += "\n\n## " + estate
        if learned:
            system_prompt += ("\n\n## LEARNED KNOWLEDGE FOR THIS SCHEMA (reuse these patterns; honor the "
                              "gotchas/bindings; use RCA leads when investigating a metric)\n" + learned)

        verifier = make_verifier(self._stage_model(Stage.VERIFY), sink=self.sink,
                                 workspace=self.workspace,
                                 dialect_note=estate_dialects_note(self.sources, dnote), config=self.config)
        sanity = (make_sanity_gate(self._stage_model(Stage.VERIFY), sink=self.sink,
                                   workspace=self.workspace, config=self.config)
                  if self.config.sanity_gate_enabled else None)
        gate = FinishGate(memory=memory, verifier=verifier, sanity_verifier=sanity, config=self.config)
        tools = data_tools + build_control_tools(memory=memory, gate=gate)
        sub_tokens: list[int] = []
        if self.subagents:
            tools.extend(build_subagent_tool(
                model=self._stage_model(Stage.AUTHORING), workspace=self.workspace, engine=self.engine,
                result_store=self.result_store, value_cache=self.value_cache, parent_memory=memory,
                system_prompt=system_prompt, sink=self.sink, asker=self.asker, max_steps=self.max_steps,
                depth=0, max_depth=self.max_subagent_depth, on_tokens=sub_tokens.append,
                dialect_note=dnote, config=self.config, sources=self.sources))

        recent = conversation.summary() if conversation is not None else ""
        tokens = tri.get("tokens", 0)
        if self.frame:
            self.sink("framing", "info", "framing intent")
            definitions = self.workspace.definitions_index() if self.workspace else ""
            if estate:
                definitions = (estate + "\n\n" + definitions) if definitions else estate
            tokens += frame_intent(model=self._stage_model(Stage.FRAMING), tools=data_tools,
                                   memory=memory, sink=self.sink, definitions=definitions,
                                   recent_turns=recent, learned=learned,
                                   max_steps=self.config.framing_max_steps, observe=_observe("framing"))

        # 3. RECALL SEED -- on the fast lane, hand the analyst the precedent to adapt + verify.
        task = None
        if tri["lane"] == "fast" and tri["precedent_sql"]:
            task = (f"Answer this question:\n{goal}\n\nA BLESSED PRECEDENT exists for this pattern -- ADAPT "
                    f"it (rebind the literals/period/values) and VERIFY it still holds; do not re-explore "
                    f"from scratch.\nQ: {tri['precedent_q'] or '(prior solved question)'}\nSQL: {tri['precedent_sql']}")

        out = run_loop(model=self._stage_model(Stage.AUTHORING), tools=tools, system_prompt=system_prompt,
                       memory=memory, sink=self.sink, max_steps=self.max_steps, finish_gate=gate,
                       config=self.config, observe=_observe("analyst"), task=task)
        tokens += out["tokens"] + gate.tokens + sum(sub_tokens)
        answer = out["text"].strip()
        if conversation is not None:
            tokens += self._record(conversation, goal, events, answer)
        return V4Answer(question=goal, answer=answer, memory=memory,
                        tokens=tokens, steps=out["steps"], verdict=out.get("verdict"))
