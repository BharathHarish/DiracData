"""grader — Spider 2.0-Lite execution-accuracy grader for the SQLite subset.

Faithful port of `xlang-ai/Spider2 / spider2-lite / evaluation_suite / evaluate.py`
(specifically `compare_multi_pandas_table` + `compare_pandas_table`), stripped of
the google.cloud.bigquery + snowflake code paths — SQLite only.

Comparison semantics (per upstream):
  - condition_cols is a LIST-PER-VARIANT of integer column indices into the gold CSV.
    Example: [[5], [4]] means variant 'a' compares gold column 5, variant 'b' compares gold column 4.
  - For each gold variant we transpose the selected columns → list of column-vectors.
  - Each gold column-vector must find some MATCHING pred column-vector (pred keeps all cols).
  - vectors_match: element-wise equality with numeric tolerance 1e-2; None/NaN → 0; if
    ignore_order=True the two vectors are sorted before comparison.
  - PASS = every gold column matched some pred column, for at least one gold variant.
"""
from __future__ import annotations
import io
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd


@dataclass
class Verdict:
    instance_id: str
    passed:      bool
    matched_variant: Optional[str]
    reason:      str
    n_pred_rows: int
    n_gold_rows: int


_TOLERANCE = 1e-2


def _normalize(v):
    if pd.isna(v):
        return 0
    return v


def _vectors_match(v1, v2, ignore_order: bool = False) -> bool:
    v1 = [_normalize(x) for x in v1]
    v2 = [_normalize(x) for x in v2]
    if ignore_order:
        v1 = sorted(v1, key=lambda x: (x is None, str(x), isinstance(x, (int, float))))
        v2 = sorted(v2, key=lambda x: (x is None, str(x), isinstance(x, (int, float))))
    if len(v1) != len(v2):
        return False
    for a, b in zip(v1, v2):
        if pd.isna(a) and pd.isna(b):
            continue
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and \
           not isinstance(a, bool) and not isinstance(b, bool):
            if not math.isclose(float(a), float(b), abs_tol=_TOLERANCE):
                return False
        elif a != b:
            return False
    return True


def _read_csv(csv_bytes: bytes) -> pd.DataFrame:
    if not csv_bytes:
        return pd.DataFrame()
    try:
        return pd.read_csv(io.BytesIO(csv_bytes))
    except Exception:
        return pd.DataFrame()


def _compare_one_variant(pred: pd.DataFrame, gold: pd.DataFrame,
                          condition_cols: List[int], ignore_order: bool) -> bool:
    """Faithful port of upstream compare_pandas_table.

    condition_cols: list of integer column INDICES into gold. Empty list → use all gold columns.
    Returns True if every gold column-vector finds a matching pred column-vector.
    """
    if pred.empty or gold.empty:
        return False
    if condition_cols:
        try:
            gold_cols_df = gold.iloc[:, condition_cols]
        except (IndexError, KeyError):
            return False
    else:
        gold_cols_df = gold

    t_gold = gold_cols_df.transpose().values.tolist()   # list of column-vectors
    t_pred = pred.transpose().values.tolist()

    for gold_vec in t_gold:
        if not any(_vectors_match(gold_vec, pred_vec, ignore_order) for pred_vec in t_pred):
            return False
    return True


def _normalise_multi_condition(multi_condition_cols: Any, n_variants: int) -> List[List[int]]:
    """Upstream:
      - None / [] / [[]] / [None] → [[] for each variant]
      - Single flat list (e.g. [5]) when there are multiple variants → replicate to each variant
      - Already list-per-variant (e.g. [[5], [4]]) → use as-is
    """
    if multi_condition_cols in (None, [], [[]], [None]):
        return [[] for _ in range(n_variants)]
    # If any inner element isn't a list, treat the whole thing as a flat list and replicate
    if n_variants > 1 and not all(isinstance(sub, list) for sub in multi_condition_cols):
        return [list(multi_condition_cols) for _ in range(n_variants)]
    # Already list-per-variant. Pad or trim to n_variants.
    out = list(multi_condition_cols)
    while len(out) < n_variants:
        out.append([])
    return out[:n_variants]


def grade_one(
    instance_id: str,
    prediction_csv: bytes,
    gold_variants: Dict[str, bytes],
    eval_meta: Dict[str, Any],
) -> Verdict:
    """Grade a prediction against all gold variants (a, b, c, …).

    Returns Verdict.passed = True if the prediction matches ANY gold variant.
    """
    pred_df = _read_csv(prediction_csv)
    if pred_df.empty:
        return Verdict(instance_id, False, None, "prediction empty", 0, 0)

    # Deterministic variant order for reporting (a, b, c, ...)
    sorted_variants = sorted(gold_variants.items())
    if not sorted_variants:
        return Verdict(instance_id, False, None, "no gold variants", len(pred_df), 0)

    ignore_order   = bool(eval_meta.get("ignore_order", True))
    condition_cols = _normalise_multi_condition(
        eval_meta.get("condition_cols"), n_variants=len(sorted_variants)
    )

    n_gold_max = 0
    last_reason = ""
    for i, (variant, gold_bytes) in enumerate(sorted_variants):
        gold_df = _read_csv(gold_bytes)
        n_gold_max = max(n_gold_max, len(gold_df))
        if gold_df.empty:
            last_reason = f"variant={variant}: gold empty"; continue
        cc = condition_cols[i] if i < len(condition_cols) else []
        passed = _compare_one_variant(pred_df, gold_df, cc, ignore_order)
        if passed:
            return Verdict(instance_id, True, variant,
                           f"matched variant {variant} (cols {cc or 'all'}, "
                           f"ignore_order={ignore_order})",
                           len(pred_df), len(gold_df))
        last_reason = f"variant={variant}: no gold column found a matching pred column (cols {cc})"
    return Verdict(instance_id, False, None, last_reason, len(pred_df), n_gold_max)


def grade_run(store, run_id: str, backend: str = "local") -> Dict[str, Any]:
    """Grade every instance in a run. Reads pred + gold from MinIO. Writes summary."""
    from .store import SpiderStore
    assert isinstance(store, SpiderStore)
    instances = store.list_instances(backend=backend)
    verdicts: List[Verdict] = []
    for inst in instances:
        iid = inst["instance_id"]
        pred_bytes = store.get_prediction(run_id, iid)
        if pred_bytes is None:
            verdicts.append(Verdict(iid, False, None, "no prediction submitted", 0, 0)); continue
        gold_variants = store.get_gold_csvs(iid)
        if not gold_variants:
            verdicts.append(Verdict(iid, False, None, "no gold csvs found", 0, 0)); continue
        eval_meta = store.get_eval_index(iid)
        verdicts.append(grade_one(iid, pred_bytes, gold_variants, eval_meta))

    n_pass = sum(1 for v in verdicts if v.passed)
    result = {
        "run_id":    run_id,
        "backend":   backend,
        "total":     len(verdicts),
        "passed":    n_pass,
        "accuracy":  round(n_pass / max(1, len(verdicts)), 4),
        "verdicts":  [v.__dict__ for v in verdicts],
    }
    store.put_result(run_id, result)
    return result
