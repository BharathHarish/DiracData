"""Structured JSON logger — one line per action, grep-friendly."""
from __future__ import annotations
import json
import sys
import time
from typing import Any


def _now_ms() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time()*1000)%1000:03d}Z"


class JsonLog:
    def __init__(self, stream=sys.stderr):
        self.stream = stream

    def _emit(self, level: str, event: str, **fields: Any) -> None:
        line = {"ts": _now_ms(), "level": level, "event": event, **fields}
        self.stream.write(json.dumps(line, default=str) + "\n")
        self.stream.flush()

    def info(self, event: str, **fields: Any) -> None: self._emit("INFO", event, **fields)
    def warn(self, event: str, **fields: Any) -> None: self._emit("WARN", event, **fields)
    def error(self, event: str, **fields: Any) -> None: self._emit("ERROR", event, **fields)


log = JsonLog()
