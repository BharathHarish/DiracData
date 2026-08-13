"""Fabric field helpers — additive enrichment for metrics/tables/columns."""
from __future__ import annotations

from typing import Any


def enrich_column_fields(
    col: dict[str, Any] | str,
    *,
    null_meaning: str | None = None,
    sentinel_values: list | None = None,
    boundary_convention: str | None = None,
) -> dict[str, Any]:
    out = {"description": col} if isinstance(col, str) else dict(col)
    if null_meaning:
        out["null_meaning"] = null_meaning
    if sentinel_values is not None:
        out["sentinel_values"] = list(sentinel_values)
    if boundary_convention:
        out["boundary_convention"] = boundary_convention
    return out


def enrich_table_fields(
    table: dict[str, Any] | str,
    *,
    grain: str | None = None,
    scd_type: str | None = None,
    time_column: str | None = None,
    cutoff_notes: str | None = None,
) -> dict[str, Any]:
    out = {"description": table} if isinstance(table, str) else dict(table)
    if grain:
        out["grain"] = grain
    if scd_type:
        out["scd_type"] = scd_type
    if time_column:
        out["time_column"] = time_column
    if cutoff_notes:
        out["cutoff_notes"] = cutoff_notes
    return out


def enrich_metric_fields(
    metric: dict[str, Any],
    *,
    companions: list[str] | None = None,
    binding_status: str | None = None,
    boundary_convention: str | None = None,
) -> dict[str, Any]:
    out = dict(metric)
    if companions is not None:
        out["companions"] = list(companions)
    if binding_status:
        out["binding_status"] = binding_status
    if boundary_convention:
        out["boundary_convention"] = boundary_convention
    return out


def companions_of(metric: dict[str, Any] | None) -> list[str]:
    if not metric:
        return []
    c = metric.get("companions")
    return list(c) if isinstance(c, list) else []
