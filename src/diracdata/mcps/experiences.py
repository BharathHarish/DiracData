"""Durable experience / gotcha writeback for MCP sessions."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


_SECTIONS = ("GOTCHAS", "BINDINGS", "RCA LEADS", "SQL PATTERNS", "VALUE DOMAINS", "PREFERENCES")


def save_experience(
    current_md: str | None,
    *,
    insight: str,
    evidence: str = "",
    section: str = "GOTCHAS",
) -> str:
    """Append a curated bullet to experiences.md (creates file/section as needed)."""
    insight = (insight or "").strip()
    if not insight:
        raise ValueError("insight is required")
    sec = (section or "GOTCHAS").strip().upper()
    if sec not in _SECTIONS:
        sec = "GOTCHAS"
    evidence = (evidence or "").strip()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bullet = f"- [{stamp}] {insight}"
    if evidence:
        bullet += f" (evidence: {evidence})"

    text = current_md or ""
    if not text.strip():
        text = "# Experiences\n\nDurable analyst notes for this fabric.\n"

    header = f"## {sec}"
    if header not in text:
        return text.rstrip() + f"\n\n{header}\n{bullet}\n"

    # Insert after header / before next ##
    parts = re.split(r"(?m)^(## .+)$", text)
    out: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        out.append(part)
        if part.strip() == header and i + 1 < len(parts):
            body = parts[i + 1]
            # avoid exact duplicate
            if insight.lower() not in body.lower():
                body = body.rstrip() + "\n" + bullet + "\n"
            out.append(body)
            i += 2
            continue
        i += 1
    return "".join(out)


def experiences_payload(md: str | None) -> dict[str, Any]:
    return {"ok": True, "chars": len(md or ""), "preview": (md or "")[:500]}
