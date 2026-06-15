#!/usr/bin/env python3
"""Compile a question into a compact semantic-catalog context packet."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "v2"
sys.path.insert(0, str(V2_ROOT / "src"))

from diracdata_v2.llms import ChatModelFactory  # noqa: E402
from diracdata_v2.semantic_catalog import LLMIntentFrameExtractor, SemanticCatalogCompiler  # noqa: E402
from diracdata_v2.settings import settings_from_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--semantic-catalog", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--max-cards", type=int, default=24)
    parser.add_argument("--max-patterns", type=int, default=6)
    parser.add_argument("--context-compiler-mode", choices=["deterministic", "agentic"], default=None)
    parser.add_argument("--context-compiler-model-profile", default=None)
    args = parser.parse_args()

    if args.context_compiler_model_profile:
        os.environ["DIRACDATA_CONTEXT_COMPILER_MODEL_PROFILE"] = args.context_compiler_model_profile
    if args.context_compiler_mode:
        os.environ["DIRACDATA_CONTEXT_COMPILER_MODE"] = args.context_compiler_mode
    settings = settings_from_env(args.env_file)
    intent_extractor = None
    if settings.context_compiler_mode.strip().lower() == "agentic":
        intent_model = ChatModelFactory(settings=settings).create_chat_model(
            profile_id=settings.context_compiler_model_profile,
        )
        intent_extractor = LLMIntentFrameExtractor(model=intent_model)
    compiler = SemanticCatalogCompiler.from_file(Path(args.semantic_catalog), intent_extractor=intent_extractor)
    packet = compiler.compile(
        args.question,
        max_cards=args.max_cards,
        max_patterns=args.max_patterns,
    )
    print(json.dumps(packet.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
