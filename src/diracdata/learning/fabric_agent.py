"""The learning agent (Phase 1): an agentic per-table loop that measures a table with its tools
and writes a data dictionary. Same shape as the query agent's analyst loop -- explore with tools,
then commit -- but the deliverable is context fabric, not an answer.

It emits, per table, sharp/nuanced descriptions and value domains, GROUNDED in the tool results
it saw (it must `profile_column` before it describes). The harness only assembles what the agent
emits; it does not profile on the agent's behalf.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from diracdata.config import Config
from diracdata.learning.tools import build_learning_tools
from diracdata.prompts import load_prompt
from diracdata.utils.streaming import Sink, loads_json, null_sink, stream_and_collect, to_ai_message

_DEFAULTS = Config()
_PROMPT = load_prompt("learn_table")
_JOIN_PROMPT = load_prompt("learn_joins")


@dataclass
class LearningResult:
    metadata: dict                       # {tables: {...}, columns: {...}}
    value_domains: dict                  # {table: {col: {...}}}
    join_graph: list | None = None       # [{left_table, left_col, right_table, right_col, grain, ...}] (Phase 2)
    tokens: int = 0
    tables: list[str] = field(default_factory=list)


class LearningAgent:
    def __init__(self, *, engine: Any, model: Any, sink: Sink = null_sink,
                 max_steps: int = _DEFAULTS.learn_max_steps) -> None:
        self.engine = engine
        self.model = model
        self.sink = sink
        self.max_steps = max_steps
        self.tools = build_learning_tools(engine=engine)

    def _loop(self, messages: list[Any]) -> tuple[str, int]:
        """Shared agentic tool loop: explore with tools, then commit final text. On the last turn
        tools are withdrawn so the agent must emit its answer."""
        from langchain_core.messages import ToolMessage

        bound = self.model.bind_tools(self.tools)
        by_name = {t.name: t for t in self.tools}
        tokens = 0
        for i in range(self.max_steps):
            model = self.model if i == self.max_steps - 1 else bound
            out = stream_and_collect(model=model, stage="learn", sink=self.sink, messages=messages)
            tokens += out["tokens"]
            messages.append(out.get("message") or to_ai_message(out["text"], out["tool_calls"]))
            if not out["tool_calls"] or i == self.max_steps - 1:
                return out["text"], tokens
            for call in out["tool_calls"]:
                name, args = call.get("name", ""), call.get("args", {}) or {}
                self.sink("learn", "tool_call", f"{name}({json.dumps(args)[:_DEFAULTS.learn_call_display]})")
                tool = by_name.get(name)
                obs = str(tool.invoke(args)) if tool else f"no such tool: {name}"
                self.sink("learn", "tool_result", obs[:_DEFAULTS.learn_result_display])
                messages.append(ToolMessage(content=obs[:_DEFAULTS.learn_obs_cap], tool_call_id=call.get("id", name)))
        return "", tokens

    def learn_table(self, table: str) -> tuple[dict, int]:
        from langchain_core.messages import HumanMessage, SystemMessage
        text, tokens = self._loop([SystemMessage(content=_PROMPT),
                                   HumanMessage(content=f"Compile the dictionary for table: {table}")])
        return loads_json(text), tokens

    def learn_joins(self) -> tuple[list, int]:
        """Discover + verify the join graph for the whole schema -> (verified edges, tokens)."""
        from langchain_core.messages import HumanMessage, SystemMessage
        listing = "\n".join(f"- {t}: {', '.join(self.engine.list_columns(t))}" for t in self.engine.list_tables())
        text, tokens = self._loop([SystemMessage(content=_JOIN_PROMPT),
                                   HumanMessage(content=f"Tables and columns:\n{listing}")])
        edges = [e for e in ((loads_json(text).get("joins")) or []) if _valid_edge(e)]
        return edges, tokens

    def compile(self, tables: list[str] | None = None, *, with_joins: bool = True) -> LearningResult:
        tables = tables or self.engine.list_tables()
        metadata: dict = {"tables": {}, "columns": {}}
        domains: dict = {}
        tokens = 0
        for table in tables:
            self.sink("learn", "info", f"learning {table}")
            parsed, tok = self.learn_table(table)
            tokens += tok
            table_doc, col_docs, col_domains = _assemble(parsed, self.engine.list_columns(table), table)
            metadata["tables"][table] = table_doc
            metadata["columns"][table] = col_docs
            domains[table] = col_domains
            self.sink("learn", "info", f"compiled {table}: {len(col_docs)} columns described")
        join_graph: list | None = None
        if with_joins:
            self.sink("learn", "info", "discovering + verifying joins")
            join_graph, jtok = self.learn_joins()
            tokens += jtok
            self.sink("learn", "info", f"verified {len(join_graph)} join edge(s)")
        return LearningResult(metadata=metadata, value_domains=domains, join_graph=join_graph,
                              tokens=tokens, tables=list(tables))


# ---- assembly (harness-side: never invents facts, only fills a safe fallback) -------------
def _assemble(parsed: dict, real_columns: list[str], table: str) -> tuple[dict, dict, dict]:
    tbl = parsed.get("table") if isinstance(parsed.get("table"), dict) else {}
    cols_in = parsed.get("columns") if isinstance(parsed.get("columns"), dict) else {}
    table_doc = {
        "short_description": _clean(tbl.get("short_description")) or f"Table {table}.",
        "long_description": _clean(tbl.get("long_description")) or f"Table {table}.",
    }
    col_docs: dict = {}
    col_domains: dict = {}
    for col in real_columns:
        d = cols_in.get(col) if isinstance(cols_in.get(col), dict) else {}
        col_docs[col] = {
            "short_description": _clean(d.get("short_description")) or f"{col}.",
            "long_description": _clean(d.get("long_description")) or f"Column {col} of {table}.",
        }
        dom = d.get("value_domain")
        col_domains[col] = dom if isinstance(dom, dict) else {}
    return table_doc, col_docs, col_domains


def write_artifacts(result: LearningResult, *, schema: str, store: Any) -> list[str]:
    """Persist the compiled fabric to the object store (never local files). `store` is a FabricStore."""
    keys = [store.put(schema, "metadata_descriptions.json", result.metadata),
            store.put(schema, "value_domains.json", result.value_domains)]
    if result.join_graph is not None:
        keys.append(store.put(schema, "join_graph.json", result.join_graph))
    return keys


def _valid_edge(e: Any) -> bool:
    return isinstance(e, dict) and all(e.get(k) for k in ("left_table", "left_col", "right_table", "right_col"))


def _clean(text: Any) -> str:
    return " ".join(str(text).split()) if text else ""
