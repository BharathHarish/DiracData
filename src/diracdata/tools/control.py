"""Control tools: the agent's plan/TODO and the gated `finish`. `plan_update` maintains the
verification spine; `finish` submits through the FinishGate (plan-verified + faithful + independently
reviewed). Learning is not the agent's concern here -- the async curator learns from the turn trace.
"""

from __future__ import annotations

from typing import Any

from diracdata.agents.verify import FinishGate
from diracdata.memory.working_memory import WorkingMemory


def build_control_tools(*, memory: WorkingMemory, gate: FinishGate) -> list[Any]:
    from langchain.tools import tool

    @tool("plan_update")
    def plan_update(action: str, id: str = "", goal: str = "", status: str = "",
                    result_id: str = "", number: str = "", note: str = "") -> str:
        """Maintain your TODO -- the verification spine. action='add' (with goal) creates an item;
        action='set' (with id) changes its status/evidence. Statuses: pending, in_progress, done (a
        number exists), verified (independently confirmed), blocked (needs ask_user). You CANNOT
        finish until every item is `verified`. Use this for multi-part or RCA questions."""
        if action == "add":
            item = memory.plan.add(goal)
            return f"added {item.id}: {goal}"
        if action == "set":
            ev = {}
            if result_id:
                ev["result_id"] = result_id
            if number:
                ev["number"] = number
            item = memory.plan.update(id, status=status or None, evidence=ev or None, note=note or None)
            return f"updated {item.id} -> {item.status}" if item else f"no such plan item: {id}"
        return "action must be 'add' or 'set'."

    @tool("finish")
    def finish(answer: str, result_ids: list[str]) -> str:
        """Submit your FINAL answer, citing the result_id(s) whose numbers back it. GATED: accepted
        only if every plan item is verified, the figures trace to query results, and an independent
        reviewer confirms it honors the confirmed intent. If REJECTED you get the reason -- fix it and
        finish again."""
        return gate.submit(answer, result_ids or [])

    return [plan_update, finish]


def build_transcript_tool(*, conversation: Any) -> Any:
    """The `read_transcript` tool: read the exact, lossless transcript of THIS conversation when a
    follow-up hinges on a minute detail the running summary omits (an exact number, filter, or an
    earlier turn's SQL). The summary is the default memory; reach for this only when it isn't enough."""
    from langchain.tools import tool

    @tool("read_transcript")
    def read_transcript(tail_chars: int = 0) -> str:
        """Read the full transcript of the current conversation -- every prior turn's question, tool
        calls, results, and answers. Use ONLY when a follow-up depends on an exact detail the summary
        you were given doesn't carry. Pass tail_chars>0 to read just the most recent N characters."""
        text = conversation.read_transcript(tail_chars=tail_chars or None)
        return text or "(transcript is empty -- this is the first turn.)"

    return read_transcript
