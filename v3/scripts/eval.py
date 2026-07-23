#!/usr/bin/env python3
"""Grade v3 against an eval pack, reusing v2's (fixed) value-based result oracle.

The premise under test: a question that closely matches a learned gold pair must return
the gold answer -- gold pairs are offline evals, and v3 should replay them. This runner
answers each case through v3 and compares its result to the expected snapshot.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "v2" / "src"))
sys.path.insert(0, str(ROOT / "v3" / "src"))

from diracdata_v2.evals.result_oracle import (  # noqa: E402
    ResultComparisonMode,
    ResultSnapshot,
    compare_snapshots,
    result_hash,
    snapshot_from_dict,
)
from diracdata_v2.llms.model_factory import ChatModelFactory  # noqa: E402
from diracdata_v2.query import DuckDBEngine  # noqa: E402
from diracdata_v2.settings import settings_from_env  # noqa: E402

from diracdata_v3 import V3Agent, Workspace  # noqa: E402


def _actual_snapshot(result: dict, mode: ResultComparisonMode) -> ResultSnapshot:
    cols = tuple(result.get("columns") or ())
    rows = tuple(tuple(r) for r in (result.get("rows") or ()))
    return ResultSnapshot(
        columns=cols,
        rows=rows,
        row_count=len(rows),
        comparison_mode=mode,
        result_hash=result_hash(columns=cols, rows=rows, comparison_mode=mode, row_count=len(rows), truncated=False),
        truncated=False,
    )


def _grade(case: dict, ans) -> tuple[bool, str]:
    if case.get("expected_behavior") == "abstain":
        # trap: correct outcome is to NOT confidently answer
        ok = (not ans.answered) or bool(ans.clarify)
        return ok, "abstained" if ok else "answered a trap"
    expected = case.get("expected_result")
    if not expected:
        return False, "no expected_result in case"
    if not ans.answered or ans.result is None:
        return False, f"no answer (clarify={bool(ans.clarify)}, steward={ans.steward_status})"
    exp = snapshot_from_dict(expected)
    act = _actual_snapshot(ans.result, exp.comparison_mode)
    cmp = compare_snapshots(exp, act)
    return cmp.passed, cmp.reason


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval-pack", default="v2/evals/result_packs/retail_analytics_balanced_eval.jsonl")
    ap.add_argument("--case-id", action="append", default=[])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--schema", default="retail_analytics")
    ap.add_argument("--model-profile", default="bedrock_zai_glm_5_ap_south_1")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    args = ap.parse_args()

    cases = [json.loads(l) for l in (ROOT / args.eval_pack).open(encoding="utf-8") if l.strip()]
    if args.case_id:
        cases = [c for c in cases if c.get("case_id") in set(args.case_id)]
    if args.limit:
        cases = cases[: args.limit]

    settings = replace(settings_from_env(args.env_file), agent_model_profile=args.model_profile)
    factory = ChatModelFactory(settings=settings)
    brain = factory.create_chat_model(profile_id=args.model_profile)
    steward = factory.create_chat_model(profile_id=args.model_profile)
    workspace = Workspace.load(
        metadata_path=ROOT / "v2/context/retail_analytics_metadata_descriptions.json",
        gold_pairs_path=ROOT / "v2/evals/Goldset_retail_queries.csv",
        query_history_path=ROOT / "v2/data/query_history/retail_analytics_query_history.csv",
        docs_paths=[ROOT / "v2/context/retail_analytics_metrics.yaml"],
    )
    engine = DuckDBEngine(data_root=ROOT / "v2" / "data", schema_name=args.schema)
    agent = V3Agent(brain_model=brain, steward_model=steward, workspace=workspace, engine=engine)

    passed, rows = 0, []
    for i, case in enumerate(cases, 1):
        cid = case.get("case_id")
        print(f"[{i}/{len(cases)}] {cid}: {case.get('question')}", file=sys.stderr)
        ans = agent.answer(str(case["question"]))
        ok, reason = _grade(case, ans)
        passed += int(ok)
        rows.append({"case_id": cid, "pass": ok, "reason": reason, "tokens": ans.tokens,
                     "answered": ans.answered, "attempts": ans.attempts})
        print(f"    -> {'PASS' if ok else 'FAIL'} ({reason}) tokens={ans.tokens}", file=sys.stderr)

    print(json.dumps({
        "cases": len(cases),
        "passed": passed,
        "pass_rate": round(passed / len(cases), 3) if cases else 0.0,
        "avg_tokens": int(sum(r["tokens"] for r in rows) / len(rows)) if rows else 0,
        "results": rows,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
