"""Data health -- opportunistic, low-cost DQ probing with drift-over-time.

The trust model's DATA-SANITY layer. On a table the analyst is about to trust, one CHEAP type-aware
aggregate pass (`probe_table`) measures the shape of the data (nulls, distinct, range, freshness);
that snapshot is appended to a per-table history in the object store (`DQHistory`, last N kept); and
`detect_drift` compares the fresh probe to the previous snapshot(s) and surfaces MEASURED EVIDENCE of
a change (a null spike, a range/row-count jump, stale data) -- never a verdict. The analyst (and the
independent verifier) judge materiality. Facts are measured here; judgement stays agentic.
"""

from __future__ import annotations

from diracdata.quality.drift import detect_drift
from diracdata.quality.history import DQHistory
from diracdata.quality.probe import probe_table

__all__ = ["probe_table", "DQHistory", "detect_drift"]
