"""Post-compile enrichment of join edges with BEHAVIOURAL facts the analyst actually needs:

  - match_rate    : share of left rows that find a right row (drives LEFT vs INNER)
  - fan_out_avg   : average right rows per left row (>1 => joining amplifies grain)
  - fan_out_max   : worst-case fan-out (a single left row that matches many rights)
  - disposition   : hint of "INNER" (safe when match~100%) or "LEFT" (preserve left facts)

Pure MEASUREMENT via the engine (no LLM). Called by the Learner AFTER the agentic learning loop
records the joins, so the loop is untouched; enrichment just augments each join dict with new
optional keys. Consumers (Context.joins, the MCP join_path tool) render whatever is present -- joins
without the new keys keep rendering as before (additive; zero regression).
"""

from __future__ import annotations

from typing import Any

from diracdata.config import Config

_DEFAULTS = Config()

# When the match rate is at or above this, orphan risk is negligible => INNER is safe.
_INNER_THRESHOLD = 0.999


def _quote_key_expr(keys: list[str]) -> str:
    """Composite-safe key expression: single quoted col, or coalesced concat for a composite."""
    parts = [f'"{k}"' for k in keys]
    return parts[0] if len(parts) == 1 else "(" + " || '|' || ".join(f"COALESCE({p}::VARCHAR,'')" for p in parts) + ")"


def _measure_one(engine: Any, left: str, lkeys: list[str], right: str, rkeys: list[str],
                 sample: int) -> dict:
    """Return {match_rate, fan_out_avg, fan_out_max, disposition, sampled} for one join, using a
    bounded SAMPLE of the left table so this stays cheap even on big warehouses."""
    lk, rk = _quote_key_expr(lkeys), _quote_key_expr(rkeys)
    sample_sql = f'(SELECT {lk} AS __k FROM "{left}" WHERE {lk} IS NOT NULL USING SAMPLE {int(sample)} ROWS)'

    # match rate: sampled left keys that find at least one right row
    m = engine.query(
        f'SELECT COUNT(*) total, SUM(CASE WHEN r.__rk IS NULL THEN 1 ELSE 0 END) orphan '
        f'FROM {sample_sql} l '
        f'LEFT JOIN (SELECT DISTINCT {rk} AS __rk FROM "{right}" WHERE {rk} IS NOT NULL) r '
        f'ON l.__k = r.__rk', 1).rows
    total, orphan = int(m[0][0] or 0), int(m[0][1] or 0)
    match_rate = 1.0 - (orphan / total) if total else 0.0

    # fan-out: for each DISTINCT left key in the sample, how many right rows does it match?
    # Distinct the sample so a key appearing N times in the sample doesn't get counted N times.
    f = engine.query(
        f'WITH s AS (SELECT DISTINCT __k FROM {sample_sql}), '
        f'per_key AS (SELECT COUNT(*) c FROM s l JOIN "{right}" r ON l.__k = {rk} GROUP BY l.__k) '
        f'SELECT AVG(c), MAX(c) FROM per_key', 1).rows
    avg_f, max_f = (float(f[0][0]) if f and f[0][0] is not None else None,
                    int(f[0][1]) if f and f[0][1] is not None else None)

    disposition = ("INNER" if match_rate >= _INNER_THRESHOLD
                   else "LEFT (keep left rows -- {:.1f}% would be dropped by INNER)".format((1 - match_rate) * 100))
    return {"match_rate": round(match_rate, 4),
            "fan_out_avg": (round(avg_f, 2) if avg_f is not None else None),
            "fan_out_max": max_f,
            "disposition": disposition,
            "sampled": total}


def enrich_joins(*, model: Any, engine: Any, sample: int = _DEFAULTS.learn_max_rows * 20) -> int:
    """Measure and attach behavioural facts to every join in `model.joins`. Idempotent -- rewrites the
    behavioural fields on each call. Failures on a single join are recorded, never raised. Returns the
    number of joins successfully enriched."""
    tables = set(engine.list_tables())
    ok = 0
    for j in model.joins:
        left, right = j.get("left"), j.get("right")
        lkeys = list(j.get("left_keys") or [])
        rkeys = list(j.get("right_keys") or [])
        if not (left in tables and right in tables and lkeys and rkeys):
            j["behaviour_error"] = "missing table or keys"
            continue
        try:
            j.update(_measure_one(engine, left, lkeys, right, rkeys, sample=sample))
            ok += 1
        except Exception as exc:  # noqa: BLE001
            j["behaviour_error"] = f"{type(exc).__name__}: {exc}"
    return ok
