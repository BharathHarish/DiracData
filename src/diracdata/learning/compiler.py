"""Agentic learning compiler -- builds a governed SEMANTIC MODEL for a schema on the query agent's
harness (WorkingMemory + Plan + run_loop + subagents + an agentic finish gate). NOT a single-prompt
describe: the model is built INCREMENTALLY via write tools (so nothing is dropped from one big JSON),
and an agentic FABRIC REVIEWER judges completeness/grounding and sends gaps back as more work.

Judgment is the LLM's (describe, classify grain/cardinality, judge completeness); deterministic pieces
are only measurement (profiling, cardinality counts via run_sql) and plumbing (enumerate, persist)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import yaml


# ---- the model being built (mutated by the write tools) -----------------------------------------
@dataclass
class SemanticModel:
    schema: str
    tables: dict = field(default_factory=dict)      # name -> {short, long, grain, kind}
    columns: dict = field(default_factory=dict)      # table -> col -> {short, long, domain, recipe?}
    joins: list = field(default_factory=list)        # [{left, left_keys, right, right_keys, cardinality, verified_by}]
    measures: dict = field(default_factory=dict)     # table -> [{name, agg, additive}]
    metrics: dict = field(default_factory=dict)      # name -> {description, sql/formula}
    dimensions: dict = field(default_factory=dict)   # name -> {expr, via, group?, primary?}

    def coverage(self, schema_cols: dict) -> dict:
        """schema_cols = {table: [col, ...]} (ground truth from the engine). Report what is still
        missing so the reviewer + agent can close gaps -- NOT a gate, just visibility."""
        missing_tables = [t for t in schema_cols if t not in self.tables]
        no_grain = [t for t in self.tables if not (self.tables[t] or {}).get("grain")]
        missing_cols = {}
        for t, cols in schema_cols.items():
            got = set((self.columns.get(t) or {}).keys())
            miss = [c for c in cols if c not in got]
            if miss:
                missing_cols[t] = miss
        joined = {j["left"] for j in self.joins} | {j["right"] for j in self.joins}
        no_joins = [t for t in schema_cols if t not in joined]
        return {"tables_total": len(schema_cols), "tables_described": len(self.tables),
                "missing_tables": missing_tables, "tables_without_grain": no_grain,
                "missing_columns": missing_cols, "tables_with_no_join": no_joins,
                "joins": len(self.joins), "metrics": len(self.metrics), "dimensions": len(self.dimensions)}

    def render(self, schema_cols: dict) -> str:
        """Compact state for the reviewer/agent context -- what's been captured + what's missing."""
        cov = self.coverage(schema_cols)
        return ("SEMANTIC MODEL SO FAR:\n"
                f"  tables described: {cov['tables_described']}/{cov['tables_total']}\n"
                f"  MISSING tables: {cov['missing_tables'] or 'none'}\n"
                f"  tables without a grain: {cov['tables_without_grain'] or 'none'}\n"
                f"  MISSING columns (undescribed): "
                + (", ".join(f"{t}[{len(c)}]" for t, c in cov['missing_columns'].items()) or "none") + "\n"
                f"  joins recorded: {cov['joins']} ; tables with no join: {cov['tables_with_no_join'] or 'none'}\n"
                f"  metrics: {cov['metrics']} ; dimensions: {cov['dimensions']}")

    @classmethod
    def from_doc(cls, doc: dict) -> "SemanticModel":
        """Rebuild a model from a to_yaml() doc (so the metadata/value-domain converters can run over a
        stored semantic_model.yaml, not just a freshly-compiled in-memory one)."""
        sm = cls(schema=(doc or {}).get("schema", ""))
        sm.joins = list((doc or {}).get("relationships") or [])
        sm.metrics = dict((doc or {}).get("metrics") or {})
        sm.dimensions = dict((doc or {}).get("dimensions") or {})
        for t, md in ((doc or {}).get("models") or {}).items():
            md = md or {}
            sm.tables[t] = {k: md.get(k) for k in ("short", "long", "grain", "kind") if md.get(k)}
            sm.columns[t] = dict(md.get("columns") or {})
            if md.get("measures"):
                sm.measures[t] = list(md["measures"])
        return sm

    def to_yaml(self) -> str:
        doc = {"version": 3, "schema": self.schema, "models": {}, "relationships": self.joins,
               "metrics": self.metrics, "dimensions": self.dimensions}
        for t, tdoc in self.tables.items():
            doc["models"][t] = {**{k: v for k, v in tdoc.items() if v},
                                "columns": self.columns.get(t, {}),
                                **({"measures": self.measures[t]} if self.measures.get(t) else {})}
        return yaml.safe_dump(doc, sort_keys=False, width=120)

    # ---- retarget: emit the artifacts the BASE agent already consumes on-demand -------------------
    # The winning channel is describe_columns -> workspace.column_detail(metadata_descriptions.json)
    # and find_examples/value_domains -- NOT a separate semantic_model the agent won't read. So we
    # fold the model's per-column intelligence (esp. the COMPLEX access recipe) into the long
    # description the agent already pulls, and the value domains into value_domains.json.
    def to_metadata_descriptions(self) -> dict:
        """{tables:{t:{short/long}}, columns:{t:{c:{short_description,long_description}}}} -- the exact
        shape workspace loads. A complex column's ACCESS RECIPE is embedded verbatim in long_description
        so a plain describe_columns(table,[col]) hands the analyst the nested-access syntax."""
        tables = {t: {"short_description": (td.get("short") or "").strip(),
                      "long_description": " ".join(filter(None, [
                          (td.get("long") or td.get("short") or "").strip(),
                          f"Grain: {td['grain']}." if td.get("grain") else "",
                          f"Kind: {td['kind']}." if td.get("kind") else ""]))}
                  for t, td in self.tables.items()}
        columns: dict = {}
        for t, cmap in self.columns.items():
            columns[t] = {}
            for c, cd in cmap.items():
                long_ = (cd.get("long") or cd.get("short") or "").strip()
                recipe = (cd.get("access_recipe") or "").strip()
                runnable = (cd.get("runnable_example") or "").strip()
                if recipe:  # push the nested-access syntax into the channel the agent already reads
                    long_ = (long_ + " " if long_ else "") + f"NESTED/COMPLEX — access path(s): {recipe}"
                if runnable:  # V3-S2: verified runnable SELECT (deepest leaf); LLM can copy verbatim
                    dialect = cd.get("runnable_dialect", "duckdb")
                    long_ = long_ + f" Runnable example ({dialect}, verified): {runnable}"
                columns[t][c] = {"short_description": (cd.get("short") or "").strip(),
                                 "long_description": long_}
        return {"tables": tables, "columns": columns}

    def to_value_domains(self) -> dict:
        """{t:{c:<domain>}} from any value_domain the model recorded -- the [values] line describe_columns
        shows. Skipped columns simply have no entry (the agent falls back to live profile_column)."""
        out: dict = {}
        for t, cmap in self.columns.items():
            for c, cd in cmap.items():
                dom = cd.get("value_domain")
                if isinstance(dom, dict) and dom:
                    out.setdefault(t, {})[c] = dom
        return out


def build_model_tools(*, model: SemanticModel) -> list[Any]:
    """The WRITE tools that build the model incrementally. Each mutates `model` and confirms."""
    from langchain.tools import tool

    @tool("describe_table")
    def describe_table(table: str, short_description: str, long_description: str,
                       grain: str, kind: str = "") -> str:
        """Record a table's doc + its GRAIN (what one row IS, e.g. 'one row per order line item' or the
        key set) + kind (fact | dimension | bridge). Grain is required -- verify it with a uniqueness
        query first (run_sql). Call once per table."""
        model.tables[table] = {"short": short_description, "long": long_description,
                               "grain": grain, "kind": kind}
        return f"recorded table {table} (grain: {grain}; kind: {kind or '?'})"

    @tool("describe_column")
    def describe_column(table: str, column: str, short_description: str, long_description: str,
                        access_recipe: str = "", value_domain: dict | None = None) -> str:
        """Record a column's doc. For a COMPLEX column (STRUCT/LIST/MAP/JSON) you MUST pass the
        access_recipe from profile_column (e.g. 'UNNEST(UNNEST(fulfillment.shipments).items).sku') so
        the query agent can reach the nested field. Ground every fact in a profile_column result."""
        model.columns.setdefault(table, {})[column] = {
            "short": short_description, "long": long_description,
            **({"access_recipe": access_recipe} if access_recipe else {}),
            **({"value_domain": value_domain} if isinstance(value_domain, dict) else {})}
        return f"recorded column {table}.{column}" + (" (+access recipe)" if access_recipe else "")

    @tool("record_join")
    def record_join(left_table: str, left_keys: list, right_table: str, right_keys: list,
                    cardinality: str, verified_by: str = "") -> str:
        """Record a join edge + its CARDINALITY -- one of 'many_to_one', 'one_to_one', 'many_to_many'.
        Verify it first with run_sql (max children per parent; orphan rate) and pass a one-line
        `verified_by` note of what you measured. This is what prevents fan-out / chasm double-counting."""
        model.joins.append({"left": left_table, "left_keys": list(left_keys), "right": right_table,
                            "right_keys": list(right_keys), "cardinality": cardinality,
                            "verified_by": verified_by})
        return f"recorded join {left_table}->{right_table} ({cardinality})"

    @tool("define_measure")
    def define_measure(table: str, name: str, agg: str, additive: bool = True, sql: str = "") -> str:
        """Record an additive fact measure on a table (agg = sum/count/avg...; additive controls whether
        it is safe to sum across a fan-out join)."""
        model.measures.setdefault(table, []).append({"name": name, "agg": agg, "additive": additive,
                                                     **({"sql": sql} if sql else {})})
        return f"recorded measure {table}.{name}"

    @tool("define_metric")
    def define_metric(name: str, description: str, sql: str = "", formula: str = "") -> str:
        """Record a governed business metric (a blessed definition, sql or formula over defined
        measures). Reconcile with any hand-authored metrics.yaml rather than contradicting it."""
        model.metrics[name] = {"description": description, **({"sql": sql} if sql else {}),
                               **({"formula": formula} if formula else {})}
        return f"recorded metric {name}"

    @tool("define_dimension")
    def define_dimension(name: str, expr: str, via: list, group: str = "", primary: bool = False) -> str:
        """Record an attribution dimension: the group-by expression + the join route (`via` = list of
        join steps to reach it). group/primary are optional metadata."""
        model.dimensions[name] = {"expr": expr, "via": list(via),
                                  **({"group": group} if group else {}),
                                  **({"primary": True} if primary else {})}
        return f"recorded dimension {name}"

    return [describe_table, describe_column, record_join, define_measure, define_metric, define_dimension]
