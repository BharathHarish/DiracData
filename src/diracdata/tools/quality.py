"""Data-health tools -- the analyst's (and subagents') window into the DATA-SANITY trust layer.

- `data_health(table, [columns], source)`: run a FRESH cheap one-pass probe now, compare it to the
  stored history, APPEND it (keeping the last N), and return the current shape + measured drift
  evidence. Opportunistic -- call it on a table a number materially rests on.
- `read_dq_history(table, source)`: read the stored snapshot series so the agent can inspect the
  trend and decide for itself.

Both are read-mostly (the only write is appending a snapshot to the DQ history) and source-aware, so
they work the same on a single engine or across a multi-source estate. Findings are evidence; whether
they matter to the answer is the agent's / verifier's agentic judgement.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from diracdata.config import Config
from diracdata.quality import DQHistory, detect_drift, probe_table

_DEFAULTS = Config()


def build_quality_tools(*, engine: Any, store: Any, schema: str, sources: Any = None,
                        memory: Any = None, config: Config = _DEFAULTS) -> list[Any]:
    from langchain.tools import tool

    hist = DQHistory(store, schema=schema, keep=config.dq_history_keep)

    def _register(snapshot: dict) -> None:
        """A probe runs real SQL, so its measured numbers (row count, null%, distinct, range, mean) are
        faithful -- register them so the agent can report a DQ fact without the finish gate flagging it."""
        if memory is None:
            return
        nums: list[Any] = [snapshot.get("row_count")]
        for col in (snapshot.get("columns") or {}).values():
            nums += [col.get(k) for k in ("null_pct", "distinct", "min", "max", "avg")]
        memory.register_numbers([nums])

    def _record_finding(snapshot: dict, drift: list) -> None:
        """Write a concise DQ-ledger line into working memory so the SANITY gate can SEE which tables/
        columns were actually probed (else it re-demands a check that already ran -> a false caveat)."""
        if memory is None:
            return
        cols = snapshot.get("columns") or {}
        parts = [f"{name} null {c.get('null_pct')}%" for name, c in cols.items() if c.get("null_pct") is not None]
        tag = f"DRIFT: {len(drift)} finding(s)" if drift else "no drift"
        memory.add_fact(f"data_health[{snapshot.get('source')}.{snapshot.get('table')}]: "
                        f"{snapshot.get('row_count')} rows; {', '.join(parts[:8]) or 'probed'}; {tag}")
    multi = sources is not None and len(sources.names()) > 1
    default_name = getattr(engine, "name", None) or "default"

    def _resolve(table: str, source: str | None):
        """(engine, source_name) for a table -- explicit source, else the source that has it, else default."""
        if source:
            return sources.get(source), source
        if not multi:
            return engine, default_name
        for nm in sources.names():
            if table in set(sources.get(nm).list_tables()):
                return sources.get(nm), nm
        return engine, default_name

    @tool("data_health")
    def data_health(table: str, columns: list[str] | None = None, source: str | None = None) -> str:
        """Check the DATA HEALTH of a table a number rests on -- opportunistic and cheap. Runs a FRESH
        one-pass, type-aware probe (row count, per-column null rate + distinct, numeric range + mean,
        and latest-timestamp freshness), compares it to the stored history, records it, and returns the
        current shape plus any DRIFT evidence vs the previous run (a null spike, a range/row-count jump,
        a distinct collapse, stale data). Pass the KEY columns to keep it cheap; source= for a
        non-default store. Findings are EVIDENCE -- weigh whether they're material to your answer;
        read_dq_history to see the full trend. These DQ facts are CONTEXT -- report them in your CHECKS
        line if material, but do NOT pass this table as a finish result_id (it is not a query result)."""
        eng, nm = _resolve(table, source)
        if eng is None or table not in set(eng.list_tables()):
            return f"No such table: {table}" + (f" in source '{nm}'" if multi else "") + ". Call get_tables()."
        try:
            snap = probe_table(eng, table, columns, sample_pct=config.dq_sample_pct)
        except Exception as exc:  # noqa: BLE001 -- a probe failure is reported, never fatal to the turn
            return f"data_health could not probe {table}: {type(exc).__name__}: {exc}"
        prior = hist.read(nm, table)
        drift = detect_drift(snap, prior, drift_pct=config.dq_drift_pct, null_delta=config.dq_drift_null_delta)
        snapshot = {"run_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "source": nm, "table": table, **snap}
        hist.append(nm, table, snapshot)
        _register(snapshot)                       # DQ facts are measured -> faithful to report
        _record_finding(snapshot, drift)          # DQ ledger -> visible to the sanity gate
        return json.dumps({"snapshot": snapshot, "drift": drift, "history_len": len(prior) + 1}, default=str)

    @tool("read_dq_history")
    def read_dq_history(table: str, source: str | None = None) -> str:
        """Read the stored DATA-HEALTH history for a table (up to the last N probes, oldest to newest)
        so you can inspect the trend over time and judge whether the current shape is normal or a drift.
        Returns the snapshot series; source= for a non-default store. Run data_health first if empty."""
        _, nm = _resolve(table, source)
        series = hist.read(nm, table)
        if not series:
            return (f"No DQ history yet for {table}" + (f" in source '{nm}'" if multi else "")
                    + f" -- run data_health('{table}') to record the first snapshot.")
        return json.dumps({"source": nm, "table": table, "kept": config.dq_history_keep,
                           "snapshots": series}, default=str)

    return [data_health, read_dq_history]
