#!/usr/bin/env python3
"""WIRED-AGENT UAT -- the one-liner analyst (diracdata.prebuilt.data_analyst) with every feature on,
answering a complex RCA question on the `ecommerce` lakehouse. Not a pytest unit test (needs MinIO +
a model key); run it directly:

    PYTHONPATH=src .venv/bin/python tests/wired_agent_uat.py

This is the whole wiring now -- one call. Compare to the manual 9-object assembly it replaces.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from diracdata.prebuilt import data_analyst          # the create_react_agent equivalent
from diracdata.model_providers import FireworksAI     # pick a model, api_key from ENV

QUESTION = ("Which market has the weakest gross profit, and is it a revenue problem or a cost problem? "
            "Show the gross profit by market.")


def main() -> int:
    # ── THE ENTIRE WIRING ─────────────────────────────────────────────────────────────────────────
    analyst = data_analyst(
        schema="ecommerce",                              # data + learned context in the object store
        model=FireworksAI("deepseek-v4-flash"),          # provider class; api_key from ENV
        env_file=str(ROOT / ".env"),                     # this repo keeps creds/config in .env
        conversation="uat-session",                      # checkpoint -> continuity across turns
        memory=True,                                     # experiential memory (curator writes back)
        garden=["fireworks_deepseek_v4_flash", "fireworks_glm_5p2",
                "fireworks_gpt_oss_120b", "fireworks_kimi_k2p7_code"],   # router garden (no sonnet)
        stream=True,                                     # live tool-call / router stream
    )
    # ──────────────────────────────────────────────────────────────────────────────────────────────

    print(f"\nQUESTION: {QUESTION}\n" + "─" * 72)
    ans = analyst.run(QUESTION)
    print("\n" + "═" * 72)
    print(f"ANSWER:\n{ans.answer}")
    print(f"\nPERF: steps={ans.steps} | tokens={ans.tokens}")
    print("═" * 72)
    print("\n✅ every feature (object-store lake, learned context, router garden, checkpoints, memory, "
          "RCA) — wired in ONE call to data_analyst(), answered on ecommerce.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
