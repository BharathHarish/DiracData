"""diracdata.experiences -- the OPTIONAL agentic-memory subsystem (schema-scoped).

Self-contained and separable (a framework consumer can omit it):
- `book`         : the curated knowledge doc (`experiences.md`), section-aware.
- `consolidator` : the async candidate queue + background drain thread.
- `curator`      : the agentic curator that folds a turn into the book (LLM judgement, prompts/curate.md).

The agent WRITES via the async curator (append/update/delete) and READS the book back into context.
One-way dependencies only: config, the object store, streaming, prompts. Nothing here imports the agent.
"""

from diracdata.experiences.book import ExperienceBook, SECTIONS
from diracdata.experiences.consolidator import MemoryConsolidator
from diracdata.experiences.curator import make_curator

__all__ = ["ExperienceBook", "SECTIONS", "MemoryConsolidator", "make_curator"]
