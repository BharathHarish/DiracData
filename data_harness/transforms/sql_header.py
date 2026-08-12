"""Parse the header comment block of a silver/gold .sql file.

Convention (locked in PLAN §7):
  -- <table_name> : <one-line description>
  -- grain:    <grain expression>
  -- sources:  <comma-separated list of raw.foo / silver.bar refs>
  -- notes:    <optional free text (one line)>
  -- lookback: <optional 'N days' or 'none'>
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class SqlHeader:
    table_name: str
    description: str
    grain: str
    sources: List[str] = field(default_factory=list)
    notes: str = ""
    lookback: Optional[str] = None
    body: str = ""


_HDR_LINE = re.compile(r"^--\s*(.*)$")


def parse(path: str | Path) -> SqlHeader:
    text = Path(path).read_text()
    lines = text.splitlines()
    header_lines: List[str] = []
    body_start = 0
    for i, ln in enumerate(lines):
        stripped = ln.rstrip()
        m = _HDR_LINE.match(stripped)
        if m:
            header_lines.append(m.group(1))
            body_start = i + 1
        elif not stripped:
            body_start = i + 1  # skip blank lines between header and body
        else:
            break
    body = "\n".join(lines[body_start:]).strip()

    # First header line: "<table_name> : <description>"
    if not header_lines:
        raise ValueError(f"{path}: no header comment block")
    first = header_lines[0].strip()
    if ":" not in first:
        raise ValueError(f"{path}: first header line must be '<table_name> : <description>'")
    table_name, description = [s.strip() for s in first.split(":", 1)]

    grain, sources, notes, lookback = "", [], "", None
    for ln in header_lines[1:]:
        low = ln.lower().strip()
        if low.startswith("grain:"):
            grain = ln.split(":", 1)[1].strip()
        elif low.startswith("sources:"):
            raw = ln.split(":", 1)[1].strip()
            sources = [s.strip() for s in raw.split(",") if s.strip()]
        elif low.startswith("notes:"):
            notes = ln.split(":", 1)[1].strip()
        elif low.startswith("lookback:"):
            v = ln.split(":", 1)[1].strip()
            lookback = None if v.lower() in ("none", "-", "") else v

    return SqlHeader(table_name=table_name, description=description, grain=grain,
                     sources=sources, notes=notes, lookback=lookback, body=body)
