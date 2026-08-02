"""Footprint-scoped data-quality probing -- cheap stewardship on exactly the columns and
joins an answer depends on, not a blanket scan of the warehouse.

The SQL a query commits IS the spec for what to check: `analyze_sql_references` gives the
tables, columns, and join pairs it touches, and we run a small battery over just those --
null rates, value plausibility (min/max/avg/stddev, negatives, zeros, a cheap max z-score),
and join health (orphan % = referential integrity, fan-out = grain inflation). The probes are
deterministic and cheap (one aggregate query per table, three counts per join); an LLM judges
what the numbers MEAN. Everything is wrapped so a probe failure degrades to "no annotation",
never breaks the answer.
"""

from __future__ import annotations

from typing import Any

from diracdata.config import Config

_DEFAULTS = Config()
_NUMERIC_HINTS = ("INT", "DEC", "DOUBLE", "FLOAT", "REAL", "NUMERIC", "HUGE")


def probe_footprint(engine: Any, sql: str, *, max_cols: int = _DEFAULTS.footprint_max_cols,
                    max_joins: int = _DEFAULTS.footprint_max_joins) -> dict:
    """Run cheap DQ probes on the columns/joins the SQL references. Returns a compact report
    (or {} on any failure). Purely observational -- no writes, read-only counts."""
    try:
        from diracdata.utils.sql import analyze_sql_references

        table_columns = {t: engine.list_columns(t) for t in engine.list_tables()}
        analysis = analyze_sql_references(sql, table_columns)
    except Exception:  # noqa: BLE001
        return {}

    report: dict[str, Any] = {"row_counts": {}, "columns": [], "joins": [], "flags": []}
    by_table: dict[str, list[str]] = {}
    for ref in analysis.columns:
        if "." not in ref:
            continue
        t, c = ref.split(".", 1)
        by_table.setdefault(t, [])
        if c not in by_table[t]:
            by_table[t].append(c)

    budget = max_cols
    for table, cols in by_table.items():
        if budget <= 0:
            break
        try:
            types = {d["column_name"]: d["column_type"] for d in engine.describe_columns(table)}
        except Exception:  # noqa: BLE001
            continue
        cols = [c for c in cols if c in types][:budget]
        if not cols:
            continue
        budget -= len(cols)
        _probe_table(engine, table, cols, types, report)

    for jp in analysis.join_pairs[:max_joins]:
        _probe_join(engine, jp.left_column, jp.right_column, report)

    return report


def _probe_table(engine: Any, table: str, cols: list[str], types: dict, report: dict) -> None:
    sel = ["COUNT(*) AS __n"]
    for c in cols:
        q = _ident(c)
        sel.append(f'COUNT({q}) AS "nn__{c}"')
        if _is_numeric(types.get(c, "")):
            sel += [f'MIN({q}) AS "min__{c}"', f'MAX({q}) AS "max__{c}"',
                    f'AVG({q}) AS "avg__{c}"', f'STDDEV_POP({q}) AS "std__{c}"',
                    f'SUM(CASE WHEN {q} < 0 THEN 1 ELSE 0 END) AS "neg__{c}"',
                    f'SUM(CASE WHEN {q} = 0 THEN 1 ELSE 0 END) AS "zero__{c}"']
    try:
        res = engine.query(f'SELECT {", ".join(sel)} FROM {_ident(table)}', 1)
    except Exception:  # noqa: BLE001
        return
    if not res.rows:
        return
    row = dict(zip(res.columns, res.rows[0]))
    n = int(_num(row.get("__n")) or 0)
    report["row_counts"][table] = n
    for c in cols:
        nn = _num(row.get(f"nn__{c}"))
        null_pct = round(100 * (1 - nn / n), 2) if n and nn is not None else None
        entry: dict[str, Any] = {"ref": f"{table}.{c}", "null_pct": null_pct}
        if _is_numeric(types.get(c, "")):
            mn, mx, avg, std = (_num(row.get(f"{k}__{c}")) for k in ("min", "max", "avg", "std"))
            neg = int(_num(row.get(f"neg__{c}")) or 0)
            zero = int(_num(row.get(f"zero__{c}")) or 0)
            entry.update(min=mn, max=mx, avg=_r(avg), std=_r(std), negatives=neg, zeros=zero)
            if std and avg is not None and mx is not None:
                entry["max_z"] = _r(abs(mx - avg) / std)
            if neg:
                report["flags"].append(f"{table}.{c}: {neg} negative values")
            if entry.get("max_z") and entry["max_z"] > _DEFAULTS.steward_max_z:
                report["flags"].append(f"{table}.{c}: max is {entry['max_z']}sigma from mean (possible outlier)")
        if null_pct and null_pct > _DEFAULTS.steward_null_pct_flag:
            report["flags"].append(f"{table}.{c}: {null_pct}% NULL")
        report["columns"].append(entry)


def _probe_join(engine: Any, left: str, right: str, report: dict) -> None:
    """Orient the join by which side is the KEY (dimension) and which is the FACT, then check
    referential integrity from the fact's side. A dimension having many unused rows is normal;
    what matters is (a) do FACT rows fail to match the dimension (real RI gap), and (b) is the
    dimension key non-unique so the join INFLATES the fact grain (real fan-out)."""
    try:
        lt, lc = left.split(".", 1)
        rt, rc = right.split(".", 1)
        ln, ld = _counts(engine, lt, lc)
        rn, rd = _counts(engine, rt, rc)
    except Exception:  # noqa: BLE001
        return
    if not ln or not rn:
        return
    # dimension = the more-unique side (its join key identifies rows); fact = the other side.
    l_uniq, r_uniq = ld / ln, rd / rn
    if r_uniq >= l_uniq:
        (ft, fc, fn), (dt, dc) = (lt, lc, ln), (rt, rc)
    else:
        (ft, fc, fn), (dt, dc) = (rt, rc, rn), (lt, lc)
    on = f"F.{_ident(fc)} = D.{_ident(dc)}"
    try:
        orphan = int(_scalar(engine, f'SELECT COUNT(*) FROM {_ident(ft)} F LEFT JOIN {_ident(dt)} D ON {on} '
                                     f'WHERE D.{_ident(dc)} IS NULL') or 0)
        joined = int(_scalar(engine, f'SELECT COUNT(*) FROM {_ident(ft)} F JOIN {_ident(dt)} D ON {on}') or 0)
    except Exception:  # noqa: BLE001
        return
    matched = fn - orphan
    orphan_pct = round(100 * orphan / fn, 2) if fn else None
    fan_out = round(joined / matched, 2) if matched else None
    report["joins"].append({"pair": f"{left} = {right}", "fact": ft, "dimension": dt,
                            "orphan_pct": orphan_pct, "fan_out": fan_out})
    if orphan_pct and orphan_pct > 0.5:
        report["flags"].append(f"{ft} -> {dt}: {orphan_pct}% of {ft} rows have no matching {dt} "
                               f"(referential-integrity gap)")
    if fan_out and fan_out > _DEFAULTS.steward_fanout_max:
        report["flags"].append(f"{ft} joined to {dt}: fan-out {fan_out}x -- {dt}.{dc} is not unique, "
                               f"inflating the grain")


def sanity_check(sql: str, result: dict | None) -> dict:
    """The RESULT-side gate: does the answer the query returned actually make sense? Distinct
    from data_quality (which checks the input columns/joins). Deterministic, generic checks:
    empty result, NULL cells in the answer, a rate/share outside [0,100], a negative count, or a
    dimension whose rows duplicate (grain leak). Flags inform the trust judgment; they don't
    hard-block (a legitimately-empty result or a >100% refund rate is for the reviewer to weigh)."""
    rows = (result or {}).get("rows") or []
    cols = (result or {}).get("columns") or []
    n = (result or {}).get("row_count", len(rows))
    out: dict = {"row_count": n, "flags": []}
    if n == 0:
        out["flags"].append("result is EMPTY (0 rows) -- a filter may match nothing or a join dropped every row")
        return out

    null_cells = sum(1 for r in rows for v in r if v is None)
    if null_cells:
        out["null_cells"] = null_cells
        out["flags"].append(f"{null_cells} NULL cell(s) in the result (a missing COALESCE or an unmatched join?)")

    lower = [str(c).lower() for c in cols]
    for j, name in enumerate(lower):
        nums = [_num(r[j]) for r in rows if j < len(r)]
        nums = [v for v in nums if v is not None]
        if not nums:
            continue
        if any(k in name for k in ("rate", "pct", "percent", "share", "ratio")):
            bad = [v for v in nums if v < 0 or v > 100]
            if bad:
                out["flags"].append(f"column '{cols[j]}' looks like a rate but has {len(bad)} value(s) "
                                    f"outside [0,100] (e.g. {round(bad[0], 3)})")
        if any(k in name for k in ("count", "orders", "buyers", "customers", "quantity", "num", "distinct")):
            if any(v < 0 for v in nums):
                out["flags"].append(f"column '{cols[j]}' looks like a count but has negative value(s)")

    if cols and rows:
        first = [r[0] for r in rows if r]
        if first and all(_num(x) is None for x in first) and len(set(map(str, first))) < len(first):
            out["flags"].append(f"duplicate rows per '{cols[0]}' -- the output grain may have leaked (join fan-out?)")
    return out


def trust_line(dq: dict, sanity: dict | None = None) -> str:
    """A compact two-part data-trust annotation: DATA QUALITY (inputs) and SANITY (output)."""
    parts = []
    if dq:
        bits = []
        rc = dq.get("row_counts") or {}
        show = _DEFAULTS.steward_flags_shown
        if rc:
            bits.append("rows " + ", ".join(f"{t}={n:,}" for t, n in list(rc.items())[:show]))
        joins = dq.get("joins") or []
        clean = [j for j in joins if (j.get("orphan_pct") or 0) <= _DEFAULTS.steward_orphan_pct_max
                 and (j.get("fan_out") or 0) <= _DEFAULTS.steward_fanout_max]
        if joins and len(clean) == len(joins):
            bits.append(f"{len(joins)} join(s) clean")
        flags = dq.get("flags") or []
        bits.append(("flags: " + "; ".join(flags[:show])) if flags else "no input flags")
        parts.append("DATA QUALITY: " + ", ".join(bits))
    if sanity:
        sflags = sanity.get("flags") or []
        parts.append("SANITY: " + ("; ".join(sflags[:_DEFAULTS.steward_flags_shown]) if sflags
                                    else f"{sanity.get('row_count', '?')} rows, no output flags"))
    return " | ".join(parts)


def _is_numeric(coltype: str) -> bool:
    t = (coltype or "").upper()
    return any(h in t for h in _NUMERIC_HINTS)


def _counts(engine: Any, table: str, col: str) -> tuple[int, int]:
    res = engine.query(f'SELECT COUNT(*), COUNT(DISTINCT {_ident(col)}) FROM {_ident(table)}', 1)
    r = res.rows[0] if res.rows else (0, 0)
    return int(r[0] or 0), int(r[1] or 0)


def _scalar(engine: Any, sql: str) -> Any:
    res = engine.query(sql, 1)
    return res.rows[0][0] if res.rows else None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _r(value: Any) -> Any:
    v = _num(value)
    return round(v, 2) if v is not None else None


def _ident(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'
