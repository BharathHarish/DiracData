#!/usr/bin/env python3
"""End-to-end proof for v5 with everything wired ON: agentic DQ gate + experiences + conversation
continuity. Runs TWO turns on one conversation so continuity is exercised, with agentic memory enabled
(default now) so the curator writes experiences.md.

    PYTHONPATH=src .venv/bin/python scripts/e2e_v5.py --model-profile openai_gpt_5_4_mini

Turn 1 is a join-fragile query (online_purchases -> household_profiles on a nullable ref, sliced by a
dimension) so data-sanity genuinely matters -- we watch whether the agent probes, and whether the
verifier GATES on it (a REJECTED whose reason cites data/health/join/null, followed by a data_health
reprobe). Turn 2 is a pronoun follow-up ("...how many of those were female?") so continuity from the
summary is exercised. After the turns we print: the DQ-gate trace, the conversation transcript/summary
locations + sizes, and the experiences.md the curator wrote.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diracdata.agent_v5 import V5Agent  # noqa: E402
from diracdata.config import settings_from_env  # noqa: E402
from diracdata.context.fabric import fabric_store_from_settings  # noqa: E402
from diracdata.context.valuecache import ColumnValueCache  # noqa: E402
from diracdata.context.workspace import Workspace  # noqa: E402
from diracdata.engines import SourceRegistry  # noqa: E402
from diracdata.execution import make_executor  # noqa: E402
from diracdata.experiences import ExperienceBook  # noqa: E402
from diracdata.memory.conversation import Conversation  # noqa: E402
from diracdata.memory.results import ResultStore  # noqa: E402
from diracdata.utils.duckdb_engine import DuckDBEngine  # noqa: E402
from diracdata.utils.model_factory import ChatModelFactory  # noqa: E402
from diracdata.utils.object_store import object_store_from_settings  # noqa: E402

_DQ_WORDS = ("data_health", "data health", "null", "join", "drop", "probe", "sanity", "orphan", "health")


class CapSink:
    def __init__(self):
        self.events: list[tuple[str, str, str]] = []
        self.tools: dict[str, int] = {}

    def __call__(self, stage: str, kind: str, text: str) -> None:
        self.events.append((stage, kind, str(text)))
        if kind == "tool_call":
            name = str(text).split("(", 1)[0].strip() or "?"
            self.tools[name] = self.tools.get(name, 0) + 1
            print(f"  >> {name}", file=sys.stderr, flush=True)

    def dq_gate_trace(self) -> dict:
        """Did the verifier REJECT on data-sanity grounds, and did the agent reprobe afterwards?"""
        rejects, dq_rejects, reprobe = [], [], False
        seen_reject = False
        for stage, kind, text in self.events:
            low = text.lower()
            if kind == "tool_result" and low.startswith("rejected"):
                rejects.append(text)
                if any(w in low for w in _DQ_WORDS):
                    dq_rejects.append(text)
                    seen_reject = True
            if kind == "tool_call" and seen_reject and text.split("(", 1)[0].strip() in ("data_health", "data_check"):
                reprobe = True
        return {"rejects": len(rejects), "dq_rejects": dq_rejects, "reprobe_after_dq_reject": reprobe}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="retail_analytics")
    ap.add_argument("--model-profile", default="openai_gpt_5_4_mini")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    args = ap.parse_args()

    settings = replace(settings_from_env(args.env_file), agent_model_profile=args.model_profile,
                       stream_tokens=False)
    assert settings.agentic_memory_enabled, "expected agentic memory ON by default"
    model = ChatModelFactory(settings=settings).create_chat_model(profile_id=args.model_profile)
    engine = DuckDBEngine(data_root=Path(args.data_root), schema_name=args.schema)
    registry = SourceRegistry.of(engine)
    fabric = fabric_store_from_settings(settings)
    obj_store = object_store_from_settings(settings)
    gold = ROOT / "data" / "evals" / "Goldset_retail_queries.csv"
    workspace = Workspace.from_store(store=fabric, schema=args.schema,
                                     gold_pairs_path=gold if gold.exists() else None)
    value_cache = ColumnValueCache(fabric, args.schema)
    book = ExperienceBook(args.schema, obj_store)
    before_book = book.read()
    conv = Conversation(f"e2e-v5-{args.model_profile}", store=obj_store, config=settings)

    def new_agent(sink):
        rs = ResultStore(engine=engine, store=obj_store, schema=engine.name, sources=registry,
                         preview_rows=settings.preview_rows, preview_all_max=settings.preview_all_max,
                         reconciler_memory_limit=settings.reconciler_memory_limit,
                         reconciler_temp_dir=settings.reconciler_temp_dir,
                         reconciler_threads=settings.reconciler_threads, executor=make_executor(settings))
        return V5Agent(model=model, workspace=workspace, engine=engine, result_store=rs, sink=sink,
                       config=settings, value_cache=value_cache, asker=lambda q: "", sources=registry,
                       experience_book=book)

    turns = [
        "How many online Music customers were there in TX in 2001, broken down by household income band?",
        "And how many of those customers were female?",
    ]
    results = []
    agent = None
    for i, q in enumerate(turns, 1):
        print(f"\n===== TURN {i}: {q} =====", file=sys.stderr)
        sink = CapSink()
        agent = new_agent(sink)
        ans = agent.run(q, conversation=conv)
        results.append((q, ans, sink))

    # Let the async curator finish; then synchronously drain anything still pending for a deterministic read.
    print("\n--- waiting for curator to fold experiences ---", file=sys.stderr)
    try:
        if agent is not None and getattr(agent, "_consolidator", None) is not None:
            for _ in range(24):
                if not agent._consolidator.pending():
                    break
                time.sleep(2)
            agent._consolidator.drain(agent._curate)   # flush any leftover, synchronously
    except Exception as exc:  # noqa: BLE001
        print(f"(curator drain note: {exc})", file=sys.stderr)
    time.sleep(2)
    after_book = book.read()

    print("\n" + "=" * 78)
    print(f"E2E v5  ::  schema={args.schema}  model={args.model_profile}")
    print("=" * 78)
    for i, (q, ans, sink) in enumerate(results, 1):
        gate = sink.dq_gate_trace()
        print(f"\nTURN {i}: {q}")
        print(f"  steps={ans.steps}  tokens={ans.tokens:,}  caveat={(ans.verdict or {}).get('accepted_with_caveat', False)}")
        print(f"  tools: {sink.tools}")
        print(f"  DQ-GATE: verify_rejects={gate['rejects']}  dq_grounded_rejects={len(gate['dq_rejects'])}  "
              f"reprobed_after_dq_reject={gate['reprobe_after_dq_reject']}")
        for r in gate["dq_rejects"][:2]:
            print(f"    · gate reason: {r[:180]}")
        print(f"  answer: {(ans.answer or '')[:300]}")

    print("\n--- CONVERSATION CONTINUITY ---")
    print(f"  location: {conv.location}   turns={conv.turns}")
    print(f"  transcript chars={len(conv.read_transcript())}  summary chars={len(conv.summary())}")
    print(f"  summary (head): {conv.summary()[:400]}")

    print("\n--- EXPERIENCES (agentic memory) ---")
    print(f"  book: {book.location}")
    print(f"  chars before={len(before_book)}  after={len(after_book)}  grew={len(after_book) > len(before_book)}")
    delta = after_book[len(before_book):] if after_book.startswith(before_book) else after_book
    print(f"  written/updated (tail):\n{delta[-800:] if delta else '(no change captured — see book above)'}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
