"""Central tool registry — maps agent-facing tool names to Python callables,
with JSON schemas for OpenAI-compatible tool calling.

Each tool is registered with:
  - name (agent-facing)
  - description (fed to the LLM)
  - parameter schema (JSON Schema)
  - callable (Python function, receives kwargs from the JSON args)

Tools accept simple JSON-serialisable kwargs. Everything else is bound via
partial (cfg, s3, con) at registration time.
"""
from __future__ import annotations
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, Dict, List, Optional
import duckdb

from .config import ModellerConfig
from . import read_tools as R
from . import fingerprint as F
from . import similarity as S
from . import engines as E
from . import validation as V
from . import write_tools as W
from . import ledger as L


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    fn: Callable[..., Any]

    def openai_schema(self) -> Dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }}


def _obj(props: Dict[str, Dict], required: Optional[List[str]] = None) -> Dict:
    return {
        "type": "object",
        "properties": props,
        "required": required or [],
        "additionalProperties": False,
    }


def _str(desc: str)  -> Dict: return {"type": "string",  "description": desc}
def _int(desc: str)  -> Dict: return {"type": "integer", "description": desc}
def _num(desc: str)  -> Dict: return {"type": "number",  "description": desc}
def _bool(desc: str) -> Dict: return {"type": "boolean", "description": desc}
def _list_str(desc: str) -> Dict:
    return {"type": "array", "items": {"type": "string"}, "description": desc}


class Registry:
    """Tool registry — call `.build(cfg, s3, con)` to bind stateful args, then
    `.schemas()` for the LLM's tool list and `.dispatch(name, args)` at call time."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def add(self, tool: Tool):
        self._tools[tool.name] = tool

    def schemas(self) -> List[Dict[str, Any]]:
        return [t.openai_schema() for t in self._tools.values()]

    def names(self) -> List[str]:
        return list(self._tools.keys())

    def dispatch(self, name: str, args: Dict[str, Any]) -> Any:
        t = self._tools.get(name)
        if not t:
            return {"error": f"unknown tool: {name}"}
        try:
            return t.fn(**args)
        except TypeError as ex:
            return {"error": f"argument error: {ex}"}
        except Exception as ex:
            return {"error": f"{type(ex).__name__}: {ex}"}


def build_registry(cfg: ModellerConfig, s3, con: duckdb.DuckDBPyConnection,
                   *, control_tools: Dict[str, Callable] = None) -> Registry:
    """Bind stateful args (cfg/s3/con) via partial and return a ready-to-dispatch registry."""
    r = Registry()
    control_tools = control_tools or {}

    # ---------- observation ----------
    r.add(Tool("list_lineage",
        "Read lineage.json — structural map of reference/raw/silver/gold tables + edges. No PK/FK metadata.",
        _obj({}),
        partial(R.list_lineage, cfg, s3)))

    r.add(Tool("list_query_patterns",
        "Aggregate query_history by template_id → per-template runs/costs/layer_mix. "
        "Filters: archetype (bi|analyst|ops|rca), since_days, min_cost_ms. All optional. Sorted by total cost.",
        _obj({
            "archetype":  _str("optional: bi | analyst | ops | rca"),
            "since_days": _int("optional: only queries from the last N days"),
            "min_cost_ms":_int("optional: only patterns with avg cost >= this"),
        }),
        lambda archetype=None, since_days=None, min_cost_ms=None:
            R.list_query_patterns(cfg, con, archetype=archetype, since_days=since_days, min_cost_ms=min_cost_ms)))

    r.add(Tool("get_pattern_cost",
        "Full cost profile for one template_id: n_runs, mean/p50/p95/p99 ms, tables_touched, layer_mix, sample_sql.",
        _obj({"template_id": _str("template_id from list_query_patterns")}, ["template_id"]),
        lambda template_id: R.get_pattern_cost(cfg, con, template_id)))

    r.add(Tool("get_layer_mix_distribution",
        "Overall workload shape — where cost goes across raw/silver/gold. Read to understand landscape.",
        _obj({}),
        partial(R.get_layer_mix_distribution, cfg, con)))

    r.add(Tool("describe_table_layout",
        "On-disk layout of a parquet table/glob: columns, file_count, total_bytes, avg_file_mb, "
        "partition_keys, compression, row_group_count.",
        _obj({"uri": _str("s3://bucket/path/**/*.parquet")}, ["uri"]),
        lambda uri: R.describe_table_layout(cfg, con, uri, s3=s3)))

    r.add(Tool("describe_column_stats",
        "Stats for one column of a parquet table: distinct_count, null_ratio, min/max, sample_values.",
        _obj({"uri": _str("s3://bucket/path/**/*.parquet"),
              "column": _str("column name")}, ["uri", "column"]),
        lambda uri, column: R.describe_column_stats(cfg, con, uri, column)))

    r.add(Tool("sample_rows",
        "Read first n rows of a parquet table as list of dicts. Quick data peek.",
        _obj({"uri": _str("s3://…"), "n": _int("row count, default 5")}, ["uri"]),
        lambda uri, n=5: R.sample_rows(cfg, con, uri, n)))

    r.add(Tool("list_prior_proposals",
        "Your own past proposals — FULL JSON blobs. Prefer proposal_index() for a compact view; "
        "use this only when you need full SQL/evidence for one or two proposals. "
        "Filter by status: pending_review | approved | rejected | superseded | withdrawn. Optional.",
        _obj({"status": _str("optional filter")}),
        lambda status=None: R.list_prior_proposals(cfg, s3, status=status)))

    r.add(Tool("proposal_index",
        "COMPACT view of every prior proposal — one row per proposal with target_name, grain_key, "
        "status, days_ago, matched_templates, projected_saving. Use this FIRST before drafting to "
        "reason about dedup / supersession. If target_name + grain_key match your intended "
        "proposal, decide agentically whether to skip, supersede, defer, or draft anyway.",
        _obj({}),
        lambda: L.proposal_index(cfg, s3)))

    r.add(Tool("recent_decisions",
        "Human decisions on prior proposals (approved / rejected / superseded / withdrawn) with "
        "reasons + days_ago. Read this to learn what humans have accepted or rejected and why. "
        "since_days optional — you decide what 'recent' means.",
        _obj({"since_days": _num("optional: only decisions within the last N days")}),
        lambda since_days=None: L.recent_decisions(cfg, s3, since_days=since_days)))

    r.add(Tool("deferral_index",
        "Compact deferral ledger — patterns you (or past rounds) chose to skip, with "
        "is_reconsider_due bool derived from the reconsider_at timestamp. Read alongside "
        "proposal_index; decide agentically whether to revisit expired deferrals.",
        _obj({}),
        lambda: L.deferral_index(cfg, s3)))

    # ---------- engine + dialect ----------
    r.add(Tool("list_supported_engines",
        "Engines the modeller has facts about: duckdb, iceberg, delta, snowflake, databricks, trino, spark.",
        _obj({}),
        E.list_supported_engines))

    r.add(Tool("describe_engine_capabilities",
        "For one engine: {display_name, write_model, capabilities: {acid, schema_evolution, time_travel, merge_into, incremental_refresh, materialized_views}}.",
        _obj({"engine": _str("one of list_supported_engines")}, ["engine"]),
        E.describe_engine_capabilities))

    r.add(Tool("list_optimisation_primitives",
        "Optimisation primitives on this engine. kind (optional) filters: "
        "layout | encoding | index | cache | maintenance | runtime | correctness | streaming | prebuild.",
        _obj({"engine": _str("engine name"), "kind": _str("optional filter")}, ["engine"]),
        lambda engine, kind=None: E.list_optimisation_primitives(engine, kind=kind)))

    r.add(Tool("list_layout_options",
        "File layout options for the engine: file_size_mb range, row_group_rows range, compressions.",
        _obj({"engine": _str("engine name")}, ["engine"]),
        E.list_layout_options))

    r.add(Tool("describe_sql_dialect",
        "Dialect notes for the engine: function name diffs, MERGE support, syntax quirks.",
        _obj({"engine": _str("engine name")}, ["engine"]),
        E.describe_sql_dialect))

    # ---------- design ----------
    r.add(Tool("fingerprint_sql",
        "Canonical shape of a SQL: tables, joins, filters, aggregations, group_by, order_by, layer_mix.",
        _obj({"sql": _str("SQL string"),
              "dialect": _str("optional dialect, default 'duckdb'")}, ["sql"]),
        lambda sql, dialect="duckdb": F.fingerprint_sql(sql, dialect)))

    r.add(Tool("similarity",
        "Weighted Jaccard 0.0-1.0 between two SQL fingerprints.",
        _obj({"fp_a": {"type": "object", "description": "first fingerprint"},
              "fp_b": {"type": "object", "description": "second fingerprint"}},
             ["fp_a", "fp_b"]),
        lambda fp_a, fp_b: S.similarity(fp_a, fp_b)))

    # ---------- validation ----------
    r.add(Tool("validate_syntax",
        "Parse SQL against the given dialect. Returns {status: 'ok' | 'error', error?}.",
        _obj({"sql": _str("SQL string"),
              "engine": _str("dialect, default 'duckdb'")}, ["sql"]),
        lambda sql, engine="duckdb": V.validate_syntax(sql, engine)))

    r.add(Tool("dry_run",
        "Execute SQL wrapped in LIMIT n. Returns rows_returned, elapsed_ms, scan_bytes_est, sample_rows[:10].",
        _obj({"sql": _str("SQL string"), "limit": _int("row limit, default 1000")}, ["sql"]),
        lambda sql, limit=1000: V.dry_run(cfg, con, sql, limit)))

    r.add(Tool("explain_plan",
        "DuckDB EXPLAIN plan for the SQL (text tree). Doesn't execute.",
        _obj({"sql": _str("SQL string")}, ["sql"]),
        lambda sql: V.explain_plan(cfg, con, sql)))

    r.add(Tool("estimate_scan_bytes",
        "Estimated bytes to scan for the SQL (upper bound; partition pruning reduces in practice).",
        _obj({"sql": _str("SQL string")}, ["sql"]),
        lambda sql: V.estimate_scan_bytes(cfg, con, sql)))

    r.add(Tool("run_sql",
        "Escape-hatch exploration. Returns first N rows as list of dicts.",
        _obj({"sql": _str("SQL string"), "limit": _int("row limit, default 200")}, ["sql"]),
        lambda sql, limit=200: V.run_sql(cfg, con, sql, limit)))

    # ---------- write ----------
    r.add(Tool("write_proposal",
        "Commit a proposal for a new gold materialisation. See system prompt for required fields. "
        "Adds proposal_id + created_at + status='pending_review' if missing.",
        _obj({"payload": {"type": "object", "description": "Full proposal JSON per system.md spec"}},
             ["payload"]),
        lambda payload: W.write_proposal(cfg, s3, payload)))

    r.add(Tool("mark_proposal",
        "Update status of a prior proposal. decision ∈ {approved, rejected, superseded, withdrawn}.",
        _obj({"proposal_id": _str("id from list_prior_proposals"),
              "decision":    _str("approved | rejected | superseded | withdrawn"),
              "reason":      _str("optional short reason")},
             ["proposal_id", "decision"]),
        lambda proposal_id, decision, reason="": W.mark_proposal(cfg, s3, proposal_id, decision, reason)))

    r.add(Tool("defer",
        "Record 'looked at this pattern this round but chose not to propose'. reason required.",
        _obj({"pattern_id":     _str("template_id or cluster label"),
              "reason":         _str("why not propose"),
              "reconsider_at":  _str("optional ISO timestamp")},
             ["pattern_id", "reason"]),
        lambda pattern_id, reason, reconsider_at=None: W.defer(cfg, s3, pattern_id, reason, reconsider_at)))

    r.add(Tool("list_deferrals",
        "Read the deferral ledger — patterns you (or past rounds) chose not to propose.",
        _obj({}),
        partial(W.list_deferrals, cfg, s3)))

    r.add(Tool("write_experience",
        "Append a learned heuristic to experiences.md (long-term memory across rounds).",
        _obj({"insight": _str("one-sentence lesson"),
              "evidence": _str("optional short evidence")},
             ["insight"]),
        lambda insight, evidence="": W.write_experience(cfg, s3, insight, evidence)))

    r.add(Tool("read_experiences",
        "Read the full experiences.md content — long-term learned heuristics.",
        _obj({}),
        partial(W.read_experiences, cfg, s3)))

    # ---------- loop control (bound at runtime by caller — via control_tools) ----------
    if "finish" in control_tools:
        r.add(Tool("finish",
            "Call when you're done with this round. Pass a short reason.",
            _obj({"reason": _str("why you're finishing (e.g., 'proposed 2, deferred 1')")}, ["reason"]),
            control_tools["finish"]))

    if "ask_user" in control_tools:
        r.add(Tool("ask_user",
            "Escape hatch — pause and defer a question to human. Use rarely, only on genuine ambiguity.",
            _obj({"question": _str("question for the human")}, ["question"]),
            control_tools["ask_user"]))

    if "finish_framing" in control_tools:
        r.add(Tool("finish_framing",
            "Framing phase: return your hypothesis {focus_patterns, round_intent, skip_patterns, engine_focus}.",
            _obj({"hypothesis": {"type": "object", "description": "Hypothesis dict per framing.md"}},
                 ["hypothesis"]),
            control_tools["finish_framing"]))

    if "finish_verify" in control_tools:
        r.add(Tool("finish_verify",
            "Verify phase: return your verdict {verdict: commit|revise|discard, findings, revised_fields?}.",
            _obj({"verdict": {"type": "object", "description": "Verdict dict per verify.md"}},
                 ["verdict"]),
            control_tools["finish_verify"]))

    if "finish_curation" in control_tools:
        r.add(Tool("finish_curation",
            "Curator phase: call when you've written all experiences you want to persist.",
            _obj({"reason": _str("short summary")}, ["reason"]),
            control_tools["finish_curation"]))

    return r
