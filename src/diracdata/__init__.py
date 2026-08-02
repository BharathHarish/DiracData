"""DiracData -- a single-loop analyst agent (coding-agent shape, analyst-first).

One brain, tools for capability, a durable WorkingMemory spine, a DuckDB/parquet result store so
large outputs never flood context, an object-store domain context (descriptions + value domains +
joins + gold/history + business definitions), durable conversation memory, and agentic schema memory
(the async curator maintaining experiences.md).
"""

from diracdata.agent import V4Agent, V4Answer
from diracdata.config import Config
from diracdata.experiences import ExperienceBook
from diracdata.memory.conversation import Conversation
from diracdata.memory.working_memory import WorkingMemory
from diracdata.memory.plan import Plan, PlanItem
from diracdata.memory.results import ResultStore
from diracdata.agents.subagents import build_subagent_tool, run_subagent
from diracdata.context.workspace import Workspace

Agent = V4Agent  # canonical name

__all__ = ["Agent", "V4Agent", "V4Answer", "Config", "Conversation", "Workspace", "WorkingMemory",
           "Plan", "PlanItem", "ResultStore", "ExperienceBook",
           "build_subagent_tool", "run_subagent"]
