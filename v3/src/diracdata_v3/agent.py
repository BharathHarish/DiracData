"""The analyst: one reflective agent with a full toolset, an independent verify, and a
write-back. Not a pipeline of personas -- a single loop that works the way an analyst does.

  analyst (a streamed ReAct loop over all the tools): understand the question (look up what
          the business terms/metrics MEAN and bind to them), recall precedent, PROBE the data
          with small SQLs before committing (grain, nulls, negatives, out-of-range values),
          build the query up as CTEs, then commit a final SELECT.
  verify  (one independent model call): a second pair of eyes that did NOT write the SQL and
          SEES the result -- it checks both correctness (entity/grain/filter/join) and data
          plausibility (anomalies, impossible magnitudes). On a real problem it sends the
          analyst back to fix it.
  record  (deterministic): a verified, novel query is written back to the experience store so
          the next question can reuse it.

The only non-agentic path is a 0-token deterministic replay of an exact/near GOLD match --
gold pairs are offline evals and must return their answer every time, for free.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from diracdata_v2.query import DuckDBEngine
from diracdata_v2.semantic_catalog.sql_analysis import analyze_sql_references
from diracdata_v2.tools.sql import validate_sql

from diracdata_v3.stewardship import probe_footprint, sanity_check, trust_line
from diracdata_v3.streaming import Sink, null_sink, stream_and_collect
from diracdata_v3.tools import build_tools
from diracdata_v3.workspace import Workspace

_ANALYST_PROMPT = """You are a sharp data analyst. Answer the question with ONE read-only SQL query, working the
way a careful analyst works: VERIFY THE DATA FIRST, TRUST IT LATER. Never write a final query
blind, and never guess a table or column name -- look it up.

KNOW THE DATA (tiered retrieval -- drill down, don't dump):
- `get_tables()` lists every table; `get_tables(t)` describes one.
- `get_columns(t)` lists a table's columns compactly (name, description, example values). Read
  this to CHOOSE the right column -- especially among near-synonyms (e.g. many *_price / *_paid
  columns). If unsure which one, `describe_column(t, c)` gives the full description, and
  `profile_column(t, c)` shows the real distinct values (confirm exact casing/codes BEFORE you
  filter on them, so a filter can't silently match nothing).
- If the question names a business term/metric, `define` it and bind to that SQL verbatim.
- BEWARE LOOK-ALIKE COLUMNS. When the question describes a cohort or entity by BEHAVIOR ("first-ever
  ONLINE purchase", "active buyer", "churned", "new customer"), do NOT assume a conveniently-named
  column matches it -- read the column's real meaning with `describe_column` first. A `first_sale_*`
  date may span ALL channels (not just online); a "current" profile may differ from the
  "at-purchase" one. Prefer the defined term (`define`) or derive the behavior from the fact table
  (e.g. first ONLINE purchase = MIN sale year over online_purchases) rather than a look-alike column.
- `find_examples` to reuse a proven prior query. `join_path` to join correctly (2/3/4-way).

BUILD VERIFY-FIRST, as CTEs -- check each piece before you trust it:
- Build the query up one CTE at a time and `run_sql` the intermediate result. Confirm each step
  returns a trustworthy shape: sensible ROW COUNT (a filter that returns 0 rows, or all rows, is
  suspicious -- investigate why), the GRAIN you expect (a join that fans out inflates COUNT/SUM
  -- verify with `data_check` or a COUNT before/after), and no surprise NULLs.
- Handle NULLs deliberately: SUM/AVG/COUNT(col) silently ignore NULLs; if a NULL should count as
  0 or 'absent', COALESCE it. If a key filter column has NULLs, decide whether they belong in or
  out and say so.
- Spend this effort on the LOAD-BEARING pieces (the main filters, joins, and measure), not every
  trivial column. Then `data_check` the assembled query to confirm the inputs are clean.

When counting distinct entities across a one-to-many join, COUNT(DISTINCT key).

When confident, end your message with exactly:
   FINAL_SQL:
   <the single read-only SELECT>
   ANSWER: <one line, plain words, with the number>
   CHECKS: <one line: what you actually verified -- columns confirmed, filter/grain checked, NULLs handled, reconciles>

Ask a question ONLY as a last resort -- if the question has two materially different BUSINESS
meanings that would give different numbers and nothing settles which. Then STOP and end with:
   CLARIFY: <one plain-language question about MEANING -- never mention tables, columns, joins,
             SQL, or grain; the user is non-technical>
Never clarify anything technical (joins, keys, grain, dedup, which column) -- those are your job; decide them by looking them up."""

_VERIFY_PROMPT = """You are an independent reviewer. You did NOT write this SQL. SEMANTIC correctness matters far
more than SQL mechanics: a query can be flawless SQL and still answer the WRONG QUESTION because
it used a column that means something else. Catch that first.

You are given the question, the SQL, the RESULT, COLUMN_MEANINGS (the real description of every
column the SQL uses), DEFINED_TERMS_AVAILABLE (business terms/metrics with fixed meanings),
CONFIRMED_INTENT when present (the user's framed meaning AND any answer the user gave to a
clarifying question), and two deterministic stewardship reports (DATA_QUALITY on the inputs,
SANITY on the output).

Judge it and return JSON only:
{"ok": true|false, "reason": "<one line>", "worth_remembering": true|false}

Check in this order:
0. CONFIRMED_INTENT (if present) is AUTHORITATIVE -- it is what the user actually meant, including
   anything they clarified when asked. Set ok=false if the SQL contradicts it in any way: a column,
   filter, grain, cohort, or measure that does not match the confirmed meaning. This overrides a
   merely-plausible reading of the raw question.
1. SEMANTIC MATCH (most important). Using COLUMN_MEANINGS, does every column MEAN what the question
   (and CONFIRMED_INTENT) needs? Set ok=false if a column is used as a proxy for a DIFFERENT concept
   -- e.g. a "first sale across ALL channels" date used to mean "first ONLINE purchase"; a gross/list
   column where a net one was asked; a shipping field used for billing; a company-wide cohort where
   an online-only one was asked. If the question uses a term that has a DEFINED_TERM, the SQL must
   bind to that definition, not a convenient look-alike column. Name the exact mismatch.
2. MECHANICS. Wrong entity/grain, a dropped/invented filter, the wrong join, over/under-counting.
3. THE PROBES. A DATA_QUALITY or SANITY flag that would DISTORT this answer (material orphans on a
   relied-on join, fan-out inflating a COUNT/SUM, an elevated NULL rate on a filtered column, an
   empty result, NULL cells in the answer, an unexplained out-of-range rate, a leaked grain). A
   flag that does NOT affect the asked number should be NOTED but isn't necessarily a fail -- judge
   materiality (a >100% refund rate is real if refunds truly exceed sales).

Name the single most important problem in one line. Do not rewrite the SQL.

Set worth_remembering=true only if this is a genuinely reusable, non-trivial query pattern a future
question could reuse. Bias to false."""


@dataclass
class V3Answer:
    question: str
    final_sql: str
    result: dict[str, Any] | None
    answered: bool
    note: str = ""
    checks: str = ""
    clarify: str = ""
    attempts: int = 1
    tokens: int = 0
    route: str = "authored"
    remembered: bool = False
    trust: str = ""
    injected_precedent: list = field(default_factory=list)
    stage_tokens: dict = field(default_factory=dict)


class Analyst:
    def __init__(
        self,
        *,
        model: Any,
        steward_model: Any | None = None,
        workspace: Workspace,
        engine: DuckDBEngine,
        experience_store: Any | None = None,
        join_store: Any | None = None,
        value_cache: Any | None = None,
        sink: Sink = null_sink,
        max_rows: int = 1000,
        max_attempts: int = 2,
    ) -> None:
        self.model = model
        self.verify_model = steward_model or model
        self.workspace = workspace
        self.engine = engine
        self.experience_store = experience_store
        self.join_store = join_store
        self.sink = sink
        self.max_rows = max_rows
        self.max_attempts = max(1, max_attempts)
        self.tools = build_tools(workspace=workspace, engine=engine, max_rows=100, value_cache=value_cache)

    # ---- the entry point --------------------------------------------------------------
    def answer(self, question: str, confirmed_intent: str = "") -> V3Answer:
        # confirmed_intent = the framed meaning + any clarification the USER gave when asked.
        # It is authoritative and is handed to BOTH the analyst (build to it) and verify (check to it).
        stage_tokens: dict[str, int] = {}

        # Deterministic GOLD fast paths (0 tokens): a gold pair is an offline eval and must
        # return its answer every time. (Experiences are surfaced to the analyst via
        # find_examples, not replayed here.)
        gold = self.workspace.exact_match(question)
        if gold is not None and (res := self._execute(gold.sql)) is not None:
            self.sink("analyst", "info", "exact gold match -> deterministic replay (0 tokens)")
            return V3Answer(question, gold.sql, res, True, note="exact gold replay", route="exact_replay")
        gold_slot = self.workspace.slot_match(question)
        if gold_slot is not None and (res := self._execute(gold_slot[0])) is not None:
            self.sink("analyst", "info", "gold slot match -> deterministic literal swap (0 tokens)")
            return V3Answer(question, gold_slot[0], res, True, note="gold slot adaptation", route="slot_adapt")

        # The analyst loop: understand -> recall -> probe -> build, then verify, then record.
        feedback = ""
        final_sql = ""
        for attempt in range(1, self.max_attempts + 1):
            sql, checks, clarify, atok = self._analyst(question, feedback, confirmed_intent=confirmed_intent)
            stage_tokens["analyst"] = stage_tokens.get("analyst", 0) + atok

            if clarify:
                self.sink("analyst", "info", f"ambiguous -> asking the user: {clarify}")
                return V3Answer(question, "", None, False, clarify=clarify, route="clarify",
                                attempts=attempt, tokens=sum(stage_tokens.values()), stage_tokens=stage_tokens)
            if not sql:
                return V3Answer(question, "", None, False, note="no SQL produced", attempts=attempt,
                                tokens=sum(stage_tokens.values()), stage_tokens=stage_tokens)
            final_sql = sql
            result = self._execute(sql)
            if result is None:
                feedback = "the SQL did not execute; recheck columns/joins with describe_table and try again."
                continue

            # Two stewardship gates before the answer is trusted:
            #   data quality -- are the INPUT columns/joins clean (null rates, orphans, fan-out)?
            #   sanity       -- is the OUTPUT plausible (not empty, no NULL cells, rates in range)?
            # Both are deterministic, footprint/result-scoped, and feed the independent verify.
            dq = probe_footprint(self.engine, sql)
            sanity = sanity_check(sql, result)
            trust = trust_line(dq, sanity)
            if trust:
                self.sink("analyst", "info", f"trust: {trust}")

            verdict, vtok = self._verify(question, sql, result, dq, sanity, confirmed_intent)
            stage_tokens["verify"] = stage_tokens.get("verify", 0) + vtok
            if verdict["ok"]:
                self._learn_joins(sql)
                remembered = self._record(question, sql, bool(verdict.get("worth_remembering")))
                return V3Answer(question, sql, result, True, note=verdict["reason"], checks=checks,
                                attempts=attempt, tokens=sum(stage_tokens.values()), route="authored",
                                remembered=remembered, trust=trust, stage_tokens=stage_tokens)
            feedback = verdict["reason"]

        return V3Answer(question, final_sql, self._execute(final_sql), False, note=feedback,
                        attempts=self.max_attempts, tokens=sum(stage_tokens.values()), stage_tokens=stage_tokens)

    # ---- the analyst loop (streamed ReAct over all tools) -----------------------------
    def _analyst(self, question: str, feedback: str, budget: int = 16,
                 confirmed_intent: str = "") -> tuple[str, str, str, int]:
        """Probe-then-commit. The analyst explores with tools, but the loop has a hard budget:
        as it runs low it is told to stop exploring and commit, and on the final turn tools are
        withdrawn so it MUST answer. This is what keeps a free agent from wandering forever."""
        from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

        parts = [f"QUESTION: {question}"]
        if confirmed_intent:
            parts.append("CONFIRMED INTENT (authoritative -- the user's framed and clarified meaning; "
                         "bind to these exact meanings, do NOT substitute a look-alike column):\n" + confirmed_intent)
        if feedback:
            parts.append(f"A prior attempt was rejected: {feedback}. Investigate and fix it.")
        bound = self.model.bind_tools(self.tools)
        by_name = {t.name: t for t in self.tools}
        messages: list[Any] = [SystemMessage(content=_ANALYST_PROMPT), HumanMessage(content="\n\n".join(parts))]
        tokens = 0
        nudged = False
        for i in range(budget):
            last_turn = i == budget - 1
            # Final turn: withdraw the tools so the model cannot explore further -- it must commit.
            model = self.model if last_turn else bound
            out = stream_and_collect(model=model, stage="analyst", sink=self.sink, messages=messages)
            tokens += out["tokens"]
            messages.append(out.get("message") or _as_ai(out["text"], out["tool_calls"]))
            if not out["tool_calls"] or last_turn:
                text = out["text"]
                clarify = _extract_marker(text, "CLARIFY")
                return _extract_marker(text, "FINAL_SQL"), _extract_marker(text, "CHECKS"), clarify, tokens
            for call in out["tool_calls"]:
                name, args = call.get("name", ""), call.get("args", {}) or {}
                self.sink("analyst", "tool_call", f"{name}({json.dumps(args)[:200]})")
                tool = by_name.get(name)
                obs = str(tool.invoke(args)) if tool else f"no such tool: {name}"
                self.sink("analyst", "tool_result", obs[:400])
                messages.append(ToolMessage(content=obs[:12000], tool_call_id=call.get("id", name)))
            # Running low on budget: tell it to stop exploring and commit its best query.
            if not nudged and i >= budget - 4:
                messages.append(HumanMessage(content=(
                    "You have gathered enough context. STOP exploring -- do not call more tools. "
                    "Reply now with your best FINAL_SQL / ANSWER / CHECKS (or CLARIFY if truly ambiguous).")))
                nudged = True
        return "", "", "", tokens

    # ---- verify (independent -- sees the MEANING of what was used, not just names) -----
    def _verify(self, question: str, sql: str, result: dict[str, Any],
                dq: dict | None = None, sanity: dict | None = None,
                confirmed_intent: str = "") -> tuple[dict, int]:
        from langchain_core.messages import HumanMessage, SystemMessage

        # The MEANING of every column this SQL actually uses -- names alone hide semantic errors
        # (a "first sale across all channels" date used to mean "first ONLINE purchase" reads fine
        # as SQL). Descriptions are what let the reviewer catch a wrong-concept column.
        col_meanings = []
        try:
            analysis = analyze_sql_references(sql, self.workspace._table_columns)
            for ref in sorted(analysis.columns):
                if "." not in ref:
                    continue
                t, c = ref.split(".", 1)
                d = self.workspace.column_detail(t, c)
                if d and d.get("description"):
                    col_meanings.append(f"{ref}: {d['description'][:220]}")
        except Exception:  # noqa: BLE001
            pass
        defined = self.workspace.definitions_index() if getattr(self.workspace, "semantic_layer", None) else ""

        sample = {"columns": result["columns"], "rows": result["rows"][:20], "row_count": result["row_count"]}
        payload = {"question": question, "sql": sql,
                   "column_meanings": col_meanings or "(none resolved)",
                   "defined_terms_available": defined or "(none configured)",
                   "result": sample}
        if confirmed_intent:
            payload["confirmed_intent"] = confirmed_intent
        if dq:
            payload["data_quality_probes"] = dq
        if sanity:
            payload["sanity_probes"] = sanity
        out = stream_and_collect(
            model=self.verify_model, stage="verify", sink=self.sink,
            messages=[SystemMessage(content=_VERIFY_PROMPT),
                      HumanMessage(content=json.dumps(payload, default=str))],
        )
        verdict = _loads_json(out["text"])
        ok = str(verdict.get("ok")).lower() != "false" and verdict.get("ok") is not False
        return {"ok": bool(ok), "reason": str(verdict.get("reason") or ""),
                "worth_remembering": bool(verdict.get("worth_remembering"))}, out["tokens"]

    # ---- record (deterministic write-back) --------------------------------------------
    def _record(self, question: str, sql: str, worth_remembering: bool) -> bool:
        """Wire the learning back: a verified, novel, worth-keeping query becomes an
        experience the next question can find and reuse. Objective novelty gate -- never
        duplicate gold/experience the workspace already covers."""
        if self.experience_store is None or not worth_remembering:
            return False
        if (self.workspace.exact_match(question) or self.workspace.slot_match(question)
                or self.workspace.has_experience_question(question)
                or self.experience_store.has_question(question)):
            return False
        self.experience_store.append(question=question, sql=sql, route="authored")
        self.workspace.add_experience(question, sql)
        self.sink("analyst", "info", "learned: recorded this query as a reusable experience")
        return True

    def _learn_joins(self, sql: str) -> None:
        """After a verified query, record any join edge the graph didn't already have -- so a
        join the analyst had to discover (incl. a non-convention or bridge join) is available
        next time. Deterministic: parse the SQL's join pairs."""
        if self.join_store is None:
            return
        try:
            analysis = analyze_sql_references(sql, self.workspace._table_columns)
        except Exception:  # noqa: BLE001
            return
        for pair in getattr(analysis, "join_pairs", None) or []:
            left, right = pair.left_column, pair.right_column
            if "." in left and "." in right and self.workspace.learn_join(left, right):
                self.join_store.append(left=left, right=right)
                self.sink("analyst", "info", f"learned new join: {left} = {right}")

    # ---- helpers ----------------------------------------------------------------------
    def _execute(self, sql: str) -> dict[str, Any] | None:
        clean = (sql or "").strip().rstrip(";")
        if not clean or validate_sql(clean, available_tables=set(self.engine.list_tables())).get("status") != "ok":
            return None
        try:
            r = self.engine.query(clean, max_rows=self.max_rows)
        except Exception:  # noqa: BLE001
            return None
        return {"columns": r.columns, "rows": [list(row) for row in r.rows], "row_count": len(r.rows)}


# Back-compat alias: the package and CLI import V3Agent.
V3Agent = Analyst


def _extract_marker(text: str, marker: str) -> str:
    m = re.search(rf"{marker}:\s*(.+?)(?:\n[A-Z_]+:|\Z)", text or "", flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    val = m.group(1).strip()
    if val.startswith("```"):
        val = re.sub(r"^```(?:sql)?\s*", "", val, flags=re.IGNORECASE)
        val = re.sub(r"\s*```$", "", val).strip()
    return val.rstrip(";")


def _loads_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        s, e = raw.find("{"), raw.rfind("}")
        if s < 0 or e < s:
            return {}
        try:
            value = json.loads(raw[s : e + 1])
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _as_ai(text: str, tool_calls: list) -> Any:
    from langchain_core.messages import AIMessage

    return AIMessage(content=text or "", tool_calls=tool_calls or [])
