"""Fabric completeness / health checks (pure over CatalogStore-shaped accessors)."""
from __future__ import annotations

import re
from typing import Any, Callable, Optional


GetText = Callable[[str, str], Optional[str]]  # (db, name) -> text
GetJson = Callable[[str, str], Any]            # (db, name) -> obj
ListDbs = Callable[[], list[str]]
ListTables = Callable[[str], list[str]]       # optional live tables


_STUB_MARKERS = (
    "todo",
    "tbd",
    "placeholder",
    "not yet",
    "coming soon",
    "# database",
    "# catalog",
)


def _is_stub_md(text: str | None, *, min_chars: int = 200) -> bool:
    if not text or not str(text).strip():
        return True
    t = str(text).strip()
    if len(t) < min_chars:
        return True
    low = t.lower()
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    # Heading-only / near-empty bodies
    if len(lines) <= 1:
        return True
    if len(lines) == 2 and len(lines[1]) < min_chars:
        return True
    # Explicit stub language on a still-short doc
    if any(m in low for m in _STUB_MARKERS) and len(t) < 800:
        return True
    return False


def _table_meta(meta: dict, table: str) -> dict:
    tables = meta.get("tables") or {}
    entry = tables.get(table) or {}
    if isinstance(entry, str):
        return {"description": entry}
    return dict(entry) if isinstance(entry, dict) else {}


def _grain_of(tmeta: dict) -> str | None:
    grain = tmeta.get("grain") or tmeta.get("verified_grain")
    if grain:
        return str(grain)
    # Learner often embeds grain in long_description: "Grain: one row per ..."
    for key in ("long_description", "description", "short_description"):
        text = tmeta.get(key) or ""
        m = re.search(r"(?i)\bgrain\s*:\s*([^\n.]+)", str(text))
        if m:
            return m.group(1).strip()
        if re.search(r"(?i)\bone row per\b", str(text)):
            return str(text)
    return None


def _columns_for(meta: dict, table: str) -> dict:
    t = _table_meta(meta, table)
    cols = t.get("columns")
    if isinstance(cols, dict):
        return cols
    # alternate shape: meta["columns"][table][col]
    top = (meta.get("columns") or {}).get(table)
    return top if isinstance(top, dict) else {}


def completeness_check(
    *,
    db: str,
    get_text: GetText,
    get_json: GetJson,
    list_tables: ListTables | None = None,
    bind_metrics: bool = False,
    engine: Any = None,
) -> dict[str, Any]:
    """Return completeness report for one database fabric."""
    meta = get_json(db, "metadata_descriptions.json") or {}
    if not isinstance(meta, dict):
        meta = {}
    joins = get_json(db, "join_facts.json")
    if joins is None:
        joins = []
    if not isinstance(joins, list):
        joins = []

    db_md = get_text(db, "database.md")
    sl_text = get_text(db, "semantic_layer.yaml") or get_text(db, "semantic_layer.yml") or ""
    sm_text = get_text(db, "semantic_model.yaml") or ""
    if not joins and sm_text:
        joins = _joins_from_semantic_model(sm_text)
    if not sl_text and sm_text:
        sl_text = sm_text

    tables_live = list(list_tables(db)) if list_tables else list((meta.get("tables") or {}).keys())
    tables_missing_grain: list[str] = []
    columns_missing_desc: list[str] = []
    empty_tables: list[str] = []

    for table in tables_live:
        tmeta = _table_meta(meta, table)
        grain = _grain_of(tmeta)
        desc = tmeta.get("description") or tmeta.get("short_description") or tmeta.get("long_description")
        if not grain and not desc:
            # no table entry at all
            tables_missing_grain.append(table)
        elif not grain:
            tables_missing_grain.append(table)

        cols = _columns_for(meta, table)
        if not cols and table in (meta.get("tables") or {}):
            # table known but no columns described
            columns_missing_desc.append(f"{table}.*")
        else:
            for col, cmeta in cols.items():
                if isinstance(cmeta, str):
                    ok = bool(cmeta.strip())
                elif isinstance(cmeta, dict):
                    ok = bool(
                        cmeta.get("description")
                        or cmeta.get("short_description")
                        or cmeta.get("long_description")
                    )
                else:
                    ok = False
                if not ok:
                    columns_missing_desc.append(f"{table}.{col}")

    joins_without_facts: list[str] = []
    for j in joins:
        if not isinstance(j, dict):
            continue
        card = j.get("cardinality") or j.get("card")
        label = (
            f"{j.get('left_table')}.{j.get('left_col')}->"
            f"{j.get('right_table')}.{j.get('right_col')}"
        )
        if not card:
            joins_without_facts.append(label)

    metrics_unbound: list[str] = []
    metrics_formula_only: list[str] = []
    metrics = _metrics_from_yaml(sl_text)
    if bind_metrics and engine is not None:
        from diracdata.mcps.bind_check import metric_bind_check

        for m in metrics:
            sql = m.get("sql") or ""
            name = m.get("name") or "?"
            if not sql:
                if m.get("formula"):
                    metrics_formula_only.append(name)
                else:
                    metrics_unbound.append(name)
                continue
            res = metric_bind_check(engine, sql)
            if not res.get("ok"):
                metrics_unbound.append(name)
    else:
        # Without engine: flag metrics that have empty SQL (formula-only noted separately)
        for m in metrics:
            if (m.get("sql") or "").strip():
                continue
            if m.get("formula"):
                metrics_formula_only.append(m.get("name") or "?")
            else:
                metrics_unbound.append(m.get("name") or "?")

    indexes_stub = _is_stub_md(db_md)
    missing_database_md = not (db_md and str(db_md).strip())

    ok = not (
        tables_missing_grain
        or columns_missing_desc
        or metrics_unbound
        or joins_without_facts
        or indexes_stub
        or missing_database_md
        or empty_tables
    )
    # If fabric has no tables at all and no live list, treat as incomplete
    if not tables_live and not (meta.get("tables") or {}):
        ok = False

    return {
        "ok": ok,
        "database": db,
        "tables_missing_grain": tables_missing_grain,
        "columns_missing_desc": columns_missing_desc[:200],
        "metrics_unbound": metrics_unbound,
        "metrics_formula_only": metrics_formula_only,
        "joins_without_facts": joins_without_facts,
        "indexes_stub": indexes_stub,
        "empty_tables": empty_tables,
        "missing_database_md": missing_database_md,
        "n_tables": len(tables_live),
        "n_metrics": len(metrics),
        "n_joins": len(joins),
    }


def fabric_health(
    *,
    catalog: str,
    list_dbs: ListDbs,
    get_catalog_text: Callable[[str], Optional[str]],
    get_text: GetText,
    get_json: GetJson,
    list_tables: ListTables | None = None,
    engine_for: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Catalog-level health: stub indexes + per-DB completeness rollup."""
    cat_md = get_catalog_text("catalog.md")
    missing_catalog_md = not (cat_md and str(cat_md).strip())
    catalog_stub = _is_stub_md(cat_md, min_chars=120)

    dbs = list(list_dbs())
    empty_databases: list[str] = []
    per_db: dict[str, Any] = {}
    unbound_metrics: list[str] = []

    for db in dbs:
        eng = engine_for(db) if engine_for else None
        report = completeness_check(
            db=db,
            get_text=get_text,
            get_json=get_json,
            list_tables=list_tables,
            bind_metrics=eng is not None,
            engine=eng,
        )
        per_db[db] = {"ok": report["ok"], "summary": {
            "tables_missing_grain": len(report["tables_missing_grain"]),
            "columns_missing_desc": len(report["columns_missing_desc"]),
            "metrics_unbound": len(report["metrics_unbound"]),
            "indexes_stub": report["indexes_stub"],
        }}
        if report["n_tables"] == 0:
            empty_databases.append(db)
        for m in report["metrics_unbound"]:
            unbound_metrics.append(f"{db}.{m}")

    ok = (
        not missing_catalog_md
        and not catalog_stub
        and not empty_databases
        and all(v["ok"] for v in per_db.values())
    ) if dbs else False

    return {
        "ok": ok,
        "catalog": catalog,
        "missing_catalog_md": missing_catalog_md,
        "catalog_stub": catalog_stub,
        "empty_databases": empty_databases,
        "unbound_metrics": unbound_metrics[:100],
        "databases": per_db,
        "n_databases": len(dbs),
    }


def _metrics_from_yaml(text: str) -> list[dict]:
    if not (text or "").strip():
        return []
    try:
        import yaml
        sl = yaml.safe_load(text) or {}
    except Exception:  # noqa: BLE001
        return []
    metrics = sl.get("metrics")
    if isinstance(metrics, list):
        out = []
        for m in metrics:
            if isinstance(m, dict) and m.get("name"):
                out.append(m)
            elif isinstance(m, str):
                out.append({"name": m, "sql": ""})
        return out
    if isinstance(metrics, dict):
        return [
            {"name": k, **(v if isinstance(v, dict) else {"sql": ""})}
            for k, v in metrics.items()
        ]
    return []


def _joins_from_semantic_model(text: str) -> list[dict]:
    """Normalize semantic_model.yaml relationships → join_facts-like dicts."""
    if not (text or "").strip():
        return []
    try:
        import yaml
        sm = yaml.safe_load(text) or {}
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for j in sm.get("joins") or []:
        if isinstance(j, dict):
            out.append(j)
    for j in sm.get("relationships") or []:
        if not isinstance(j, dict):
            continue
        out.append({
            "left_table": j.get("left_table") or j.get("from_table") or j.get("left"),
            "left_col": j.get("left_col") or j.get("from_col") or j.get("left_key"),
            "right_table": j.get("right_table") or j.get("to_table") or j.get("right"),
            "right_col": j.get("right_col") or j.get("to_col") or j.get("right_key"),
            "cardinality": j.get("cardinality") or j.get("card") or j.get("type"),
            "disposition": j.get("disposition") or j.get("join_type"),
        })
    return out
