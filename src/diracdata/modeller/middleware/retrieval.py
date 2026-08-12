"""Retrieval middleware — inject relevant experiences into the prompt.

At round start (before framing), read experiences.md and pass it whole into
the system context. For a lean MVP we don't do semantic search — the whole
file is small (~a few KB) and the agent can filter mentally.

If experiences.md grows large in the future, this is where semantic search
would live (e.g., embedding lookup by round intent → top-K experiences).
Never decides anything. Just shapes context.
"""
from __future__ import annotations
from typing import Optional


def inject_experiences(experiences_md: str, current_focus: Optional[str] = None) -> str:
    """Return a context block to prepend to the system prompt.

    For now: paste all experiences verbatim. Empty → return empty (no wasted tokens).
    """
    if not experiences_md or not experiences_md.strip():
        return ""
    return (
        "\n\n## Your accumulated experiences (from past rounds)\n\n"
        "These are heuristics you (or past rounds of yourself) recorded. "
        "Use them to inform judgement — but don't obey them blindly.\n\n"
        f"{experiences_md}\n"
    )
