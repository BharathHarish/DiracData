"""Prompt loading. Every agent/verify/learning prompt lives in `prompts/*.md` -- version-controlled,
editable at runtime (a change to a `.md` takes effect on the next run), never inline in code. Loaded
lazily and cached.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """The text of `prompts/<name>.md`."""
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def dialect_note(dialect: str) -> str:
    """The concrete dialect specifics for the running engine, from `prompts/dialect_<name>.md`;
    a generic instruction is returned for a dialect that has no file yet."""
    name = (dialect or "").lower().strip()
    if (_DIR / f"dialect_{name}.md").exists():
        return load_prompt(f"dialect_{name}")
    return (f"TARGET DIALECT = {dialect or 'unknown'}. Use this engine's exact date/time functions and its "
            "array/list index base (0- vs 1-based); verify any unfamiliar function with a tiny run_sql probe.")
