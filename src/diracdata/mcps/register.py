"""MCP resource URI helpers — noun-shaped fabric content."""
from __future__ import annotations

import json
from typing import Any, Optional


def parse_dirac_uri(uri: str) -> dict[str, Any]:
    """Parse dirac resource URIs.

    Schemes:
      index://{catalog}
      index://{catalog}/{db}
      metric://{catalog}/{db}/{name}
      table://{catalog}/{db}/{table}
      example://{catalog}/{db}/{id}
      dialect://{catalog}/{db}
      ontology://{catalog}/{db}
      experiences://{catalog}/{db}

    Schema-MCP (no catalog segment) variants:
      metric://{schema}/{name}
      table://{schema}/{table}
      dialect://{schema}
      index://{schema}
    """
    u = (uri or "").strip()
    if "://" not in u:
        raise ValueError(f"invalid resource uri: {uri!r}")
    scheme, rest = u.split("://", 1)
    parts = [p for p in rest.split("/") if p]
    return {"scheme": scheme.lower(), "parts": parts, "uri": u}


def render_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


def catalog_resource_body(rt: Any, uri: str) -> str:
    """Resolve a catalog-scoped resource against _CatalogRuntime."""
    from diracdata.mcps.dialect import dialect_card
    from diracdata.mcps.ontology import empty_channel_maps, empty_ontology

    parsed = parse_dirac_uri(uri)
    scheme = parsed["scheme"]
    parts: list[str] = parsed["parts"]  # type: ignore[assignment]

    if scheme == "index":
        if len(parts) == 1:
            # index://catalog
            text = rt.cs.get_catalog_text(rt.catalog, "catalog.md") or ""
            if not text:
                dbs = rt.cs.list_databases(rt.catalog)
                text = f"# {rt.catalog}\n\nDatabases: {', '.join(dbs)}\n"
            return text
        if len(parts) >= 2:
            db = parts[1]
            text = rt.cs.get_text(rt.catalog, db, "database.md") or ""
            if not text:
                text = f"# {db}\n\n(no database.md yet)\n"
            return text
        raise ValueError(f"index uri needs catalog or catalog/db: {uri}")

    if scheme == "dialect":
        db = parts[1] if len(parts) >= 2 else (rt._current_db or "")
        engine_kind = "duckdb+sqlite" if rt._is_sqlite_catalog() else "duckdb"
        card = dialect_card(engine_kind=engine_kind)
        card["catalog"] = rt.catalog
        card["database"] = db
        return render_json(card)

    if scheme == "ontology":
        db = parts[1] if len(parts) >= 2 else rt.resolve_db(None)
        ont = rt.cs.get(rt.catalog, db, "ontology.yaml", default=None)
        maps = rt.cs.get(rt.catalog, db, "channel_maps.yaml", default=None)
        # YAML may be stored as text
        if ont is None:
            raw = rt.cs.get_text(rt.catalog, db, "ontology.yaml")
            ont = _yaml_or(raw, empty_ontology())
        if maps is None:
            raw = rt.cs.get_text(rt.catalog, db, "channel_maps.yaml")
            maps = _yaml_or(raw, empty_channel_maps())
        return render_json({"database": db, "ontology": ont, "channel_maps": maps})

    if scheme == "experiences":
        db = parts[1] if len(parts) >= 2 else rt.resolve_db(None)
        return rt.cs.get_text(rt.catalog, db, "experiences.md") or "# Experiences\n\n(none yet)\n"

    if scheme == "metric":
        if len(parts) < 3:
            raise ValueError(f"metric uri needs catalog/db/name: {uri}")
        db, name = parts[1], parts[2]
        return render_json(_metric_payload(rt, db, name))

    if scheme == "table":
        if len(parts) < 3:
            raise ValueError(f"table uri needs catalog/db/table: {uri}")
        db, table = parts[1], parts[2]
        meta = rt.cs.get(rt.catalog, db, "metadata_descriptions.json", default={}) or {}
        tables = meta.get("tables") or {}
        entry = tables.get(table) or {}
        return render_json({"database": db, "table": table, "meta": entry})

    if scheme == "example":
        if len(parts) < 3:
            raise ValueError(f"example uri needs catalog/db/id: {uri}")
        db, eid = parts[1], parts[2]
        gold = rt.cs.get(rt.catalog, db, "gold_pairs.json", default=[]) or []
        hit = None
        if isinstance(gold, list):
            for i, g in enumerate(gold):
                if str(i) == eid or (isinstance(g, dict) and str(g.get("id")) == eid):
                    hit = g
                    break
        return render_json({"database": db, "id": eid, "example": hit})

    raise ValueError(f"unsupported resource scheme: {scheme}")


def schema_resource_body(rt: Any, uri: str) -> str:
    """Resolve schema-MCP resources (Context runtime)."""
    from diracdata.mcps.dialect import dialect_from_engine
    from diracdata.mcps.fabric_fields import companions_of

    parsed = parse_dirac_uri(uri)
    scheme = parsed["scheme"]
    parts: list[str] = parsed["parts"]  # type: ignore[assignment]
    schema = rt.default_schema

    if scheme == "dialect":
        card = dialect_from_engine(rt.engine)
        card["schema"] = schema
        return render_json(card)

    if scheme == "index":
        # compact table list
        return rt.ctx(None).tables()

    if scheme == "metric":
        name = parts[-1] if parts else ""
        raw = rt.ctx(None).metric(name)
        # attach companions if workspace has structured metric
        try:
            m = rt.ctx(None).workspace.metric(name) if name else None
            if isinstance(m, dict) and companions_of(m):
                return render_json({"text": raw, "companions": companions_of(m)})
        except Exception:  # noqa: BLE001
            pass
        return raw if isinstance(raw, str) else render_json(raw)

    if scheme == "table":
        table = parts[-1] if parts else ""
        return rt.ctx(None).describe(table)

    if scheme == "experiences":
        store = getattr(rt.ctx(None).workspace, "store", None)
        # best-effort
        return "# Experiences\n\n(use save_experience tool)\n"

    if scheme == "ontology":
        return render_json({"schema": schema, "note": "ontology.yaml not configured for schema MCP"})

    raise ValueError(f"unsupported schema resource: {uri}")


def _metric_payload(rt: Any, db: str, name: str) -> dict[str, Any]:
    import yaml
    from diracdata.mcps.fabric_fields import companions_of

    text = rt.cs.get_text(rt.catalog, db, "semantic_layer.yaml") or ""
    sl = yaml.safe_load(text) or {} if text else {}
    metrics = sl.get("metrics") or []
    hit = None
    if isinstance(metrics, list):
        for m in metrics:
            if isinstance(m, dict) and m.get("name") == name:
                hit = m
                break
    elif isinstance(metrics, dict):
        hit = metrics.get(name)
        if isinstance(hit, dict):
            hit = {"name": name, **hit}
    return {
        "database": db,
        "name": name,
        "metric": hit,
        "companions": companions_of(hit if isinstance(hit, dict) else None),
    }


def _yaml_or(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        import yaml
        return yaml.safe_load(text) or default
    except Exception:  # noqa: BLE001
        return default
