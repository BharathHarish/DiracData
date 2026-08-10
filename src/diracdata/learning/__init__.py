"""The learning agent -- an agentic loop that compiles verified, lossless context fabric for the
query agent. It measures the schema with tools (never a scripted profiler) and writes the
dictionary + value domains it observed.

Phase 1: per-table dictionary (descriptions + value domains).
"""

from diracdata.learning.fabric_agent import LearningAgent, LearningResult, write_artifacts
from diracdata.learning.learner import Learner
from diracdata.learning.profiler import column_facts

__all__ = ["Learner", "LearningAgent", "LearningResult", "write_artifacts", "column_facts"]
