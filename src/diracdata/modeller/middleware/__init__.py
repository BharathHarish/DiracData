"""Middleware — inject context, never decide. Composed into the LLM call pipeline."""
from .budgets import BudgetTracker
from .audit  import AuditSink
from .retrieval import inject_experiences
from .checkpoint import Checkpointer
