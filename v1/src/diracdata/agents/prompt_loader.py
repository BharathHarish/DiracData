"""Prompt loading helpers for data analyst agents."""

from __future__ import annotations

from pathlib import Path


PROMPT_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT_V1_PATH = PROMPT_DIR / "SYSTEM_PROMPT_V1.md"
INTENT_FRAME_PROMPT_V1_PATH = PROMPT_DIR / "INTENT_FRAME_PROMPT_V1.md"
SQL_PLAN_PROMPT_V1_PATH = PROMPT_DIR / "SQL_PLAN_PROMPT_V1.md"
TRUTH_COMPILER_PROMPT_V1_PATH = PROMPT_DIR / "TRUTH_COMPILER_PROMPT_V1.md"
SQL_REFLECTION_PROMPT_V1_PATH = PROMPT_DIR / "SQL_REFLECTION_PROMPT_V1.md"
CANDIDATE_INTENT_PROMPT_V1_PATH = PROMPT_DIR / "CANDIDATE_INTENT_PROMPT_V1.md"


def load_system_prompt_v1() -> str:
    return SYSTEM_PROMPT_V1_PATH.read_text(encoding="utf-8")


def load_intent_frame_prompt_v1() -> str:
    return INTENT_FRAME_PROMPT_V1_PATH.read_text(encoding="utf-8")


def load_sql_plan_prompt_v1() -> str:
    return SQL_PLAN_PROMPT_V1_PATH.read_text(encoding="utf-8")


def load_truth_compiler_prompt_v1() -> str:
    return TRUTH_COMPILER_PROMPT_V1_PATH.read_text(encoding="utf-8")


def load_sql_reflection_prompt_v1() -> str:
    return SQL_REFLECTION_PROMPT_V1_PATH.read_text(encoding="utf-8")


def load_candidate_intent_prompt_v1() -> str:
    return CANDIDATE_INTENT_PROMPT_V1_PATH.read_text(encoding="utf-8")
