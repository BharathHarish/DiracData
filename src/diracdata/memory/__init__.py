"""diracdata.memory -- the OPTIONAL persistent experiential memory subsystem (schema-scoped).

Importable + enable-able in an agent (off unless config.agentic_memory_enabled AND a book is passed).
Self-contained and separable:
- `book`         : the curated knowledge doc (`experiences.md`), section-aware.
- `consolidator` : the async candidate queue + background drain thread.
- `curator`      : the agentic curator that folds a turn into the book (LLM judgement, prompts/curate.md).

The agent WRITES via the async curator (append/update/delete) and READS the book back into context.
This is PERSISTENT memory reused across turns -- distinct from diracdata.runtime (per-turn state) and
diracdata.checkpoints (conversation continuity). One-way deps only: config, stores, streaming, prompts.
"""

from diracdata.memory.book import ExperienceBook, SECTIONS
from diracdata.memory.consolidator import MemoryConsolidator
from diracdata.memory.curator import make_curator

__all__ = ["ExperienceBook", "SECTIONS", "MemoryConsolidator", "make_curator"]
