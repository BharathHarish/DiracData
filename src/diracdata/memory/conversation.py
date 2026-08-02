"""Durable conversation memory -- what makes follow-ups work ACROSS turns and sessions.

Two documents per conversation, keyed by id:
  - transcript.md : the FULL agent trace, appended after every turn -- the user's question, every
    tool call (name + args) and its result, and the final answer. The lossless record; the agent
    reads it on demand (the `read_transcript` tool) when a follow-up hinges on a minute detail.
  - summary.md    : a compact RUNNING summary, regenerated after every turn by an LLM (agents/summarizer)
    and fed into the NEXT turn's framing as the conversation memory. Lossy by design -- conclusions,
    bindings, resolved entities, key numbers + their result_ids -- never the play-by-play.

Persistence mirrors the rest of the harness: pass an object store (MinIO/S3) and the two documents
live under `conversations/<id>/` right next to the query results and the compiled fabric -- durable
and portable across machines/sessions. With no store they fall back to local files under
`config.conversations_dir/<id>/` (offline runs, tests). This object owns only persistence + rendering;
the summary TEXT is produced agentically elsewhere.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from diracdata.config import Config

_DEFAULTS = Config()

_TRANSCRIPT = "transcript.md"
_SUMMARY = "summary.md"


class Conversation:
    def __init__(self, conversation_id: str, *, store: Any = None, root: Path | None = None,
                 config: Config = _DEFAULTS) -> None:
        self.id = conversation_id
        self.config = config
        self._store = store                       # object store (durable, portable) -- preferred
        self._prefix = f"conversations/{conversation_id}"
        self.dir: Path | None = None
        if store is None:                         # local fallback (offline / tests)
            self.dir = (root or config.conversations_dir) / conversation_id
            self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def location(self) -> str:
        return f"{self._prefix}/ (object store)" if self._store is not None else str(self.dir)

    # ---- summary (fed into the next turn) --------------------------------------------------
    def summary(self) -> str:
        return self._read(_SUMMARY).strip()

    def set_summary(self, text: str) -> None:
        self._write(_SUMMARY, (text or "").strip() + "\n")

    # ---- transcript (the lossless record) --------------------------------------------------
    def read_transcript(self, tail_chars: int | None = None) -> str:
        text = self._read(_TRANSCRIPT)
        return text[-tail_chars:] if tail_chars else text

    @property
    def turns(self) -> int:
        return self._read(_TRANSCRIPT).count("\n## Turn ")

    def append_turn(self, *, question: str, events: list[dict], answer: str) -> str:
        """Append one turn's full trace to the transcript and return the rendered markdown (so the
        caller can hand it to the summarizer). `events` is an ordered list of {phase, tool, args,
        result} captured by the loop's observe hook."""
        md = self._render_turn(self.turns + 1, question, events, answer)
        self._write(_TRANSCRIPT, self._read(_TRANSCRIPT) + md)   # object stores have no append
        return md

    def _render_turn(self, n: int, question: str, events: list[dict], answer: str) -> str:
        cap = self.config.transcript_result_cap
        lines = [f"\n## Turn {n}", f"**Question:** {question}", ""]
        last_phase = None
        for ev in events:
            phase = ev.get("phase", "analyst")
            if phase != last_phase:
                lines.append(f"### {phase.capitalize()}")
                last_phase = phase
            args = _clip(json.dumps(ev.get("args", {}), default=str, ensure_ascii=False), cap)
            result = _clip(str(ev.get("result", "")), cap)
            lines.append(f"- `{ev.get('tool', '?')}({args})`")
            lines.append(f"  → {result}")
        lines += ["", f"**Answer:** {answer}", "", "---", ""]
        return "\n".join(lines)

    # ---- storage (object store, else local file) -------------------------------------------
    def _read(self, name: str) -> str:
        if self._store is not None:
            key = f"{self._prefix}/{name}"
            return self._store.read_bytes(key).decode("utf-8") if self._store.exists(key) else ""
        path = self.dir / name
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _write(self, name: str, text: str) -> None:
        if self._store is not None:
            self._store.write_bytes(f"{self._prefix}/{name}", text.encode("utf-8"), "text/markdown")
        else:
            (self.dir / name).write_text(text, encoding="utf-8")


def _clip(text: str, cap: int) -> str:
    text = text.replace("\r\n", "\n")
    return text if len(text) <= cap else text[:cap] + f" …[+{len(text) - cap} chars]"
