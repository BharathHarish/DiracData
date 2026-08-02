"""ExperienceBook -- the schema-scoped, curated knowledge doc. Same architecture as the conversation
`summary.md`, but at SCHEMA scope and maintained by the async curator (agents/curator) rather than the
summarizer: a human-readable `experiences/<schema>/experiences.md` in the object store, organized into
sections per knowledge KIND (SQL patterns, RCA leads, gotchas, bindings, value domains, preferences).

This module owns only persistence + section editing; WHAT to keep is the curator's LLM judgement.
"""

from __future__ import annotations

import re
from typing import Any

# Seed sections (the curator may add more). Order is the canonical render order.
SECTIONS: tuple[str, ...] = (
    "SQL PATTERNS", "RCA LEADS", "GOTCHAS", "BINDINGS", "VALUE DOMAINS", "PREFERENCES",
)

_HEADER = re.compile(r"^## (.+?)\s*$", re.MULTILINE)


class ExperienceBook:
    def __init__(self, schema: str, store: Any) -> None:
        self.schema = schema
        self._store = store
        self.key = f"experiences/{schema}/experiences.md"

    @property
    def location(self) -> str:
        return f"{self.key} (object store)"

    def read(self) -> str:
        if self._store is not None and self._store.exists(self.key):
            return self._store.read_bytes(self.key).decode("utf-8")
        return ""

    def write(self, text: str) -> None:
        self._store.write_bytes(self.key, (text or "").strip().encode("utf-8") + b"\n", "text/markdown")

    # ---- section editing (what the curator's update_experiences tool drives) ---------------
    def sections(self) -> dict[str, str]:
        """Parse the doc into {SECTION_NAME: body}. Preserves order of appearance."""
        text = self.read()
        out: dict[str, str] = {}
        matches = list(_HEADER.finditer(text))
        for i, m in enumerate(matches):
            name = m.group(1).strip().upper()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            out[name] = text[start:end].strip()
        return out

    def update_section(self, name: str, body: str) -> None:
        """Replace (or add) one section's body and re-render the whole doc. Empty body drops it."""
        name = name.strip().upper()
        secs = self.sections()
        if body.strip():
            secs[name] = body.strip()
        else:
            secs.pop(name, None)
        self.write(self._render(secs))

    def _render(self, secs: dict[str, str]) -> str:
        # canonical sections first (in SECTIONS order), then any extras the curator added
        ordered = [s for s in SECTIONS if s in secs] + [s for s in secs if s not in SECTIONS]
        return "\n\n".join(f"## {s}\n{secs[s]}".rstrip() for s in ordered if secs.get(s))
