"""Typed contracts for the primitive data-agent harness."""

from __future__ import annotations

from enum import StrEnum


class HarnessStage(StrEnum):
    INTENT = "intent"
    DEFINITION_GATE = "definition_gate"
    SQL_AUTHORING = "sql_authoring"
    STEWARD_REVIEW = "steward_review"
    DATA_ENGINEERING = "data_engineering"
    FINAL_EXECUTION = "final_execution"
    ANSWER = "answer"


class ToolPermission(StrEnum):
    RETRIEVAL = "retrieval"
    VALUE_PROBE = "value_probe"
    SQL_DRY_RUN = "sql_dry_run"
    SQL_EXECUTE = "sql_execute"


class TermResolutionStatus(StrEnum):
    DEFINED = "defined"
    USER_PROVIDED = "user_provided"
    UNRESOLVED = "unresolved"
    CONFLICTING = "conflicting"
    INFERRED = "inferred"


class GateDecision(StrEnum):
    PASS = "pass"
    NEEDS_CLARIFICATION = "needs_clarification"
    FAIL = "fail"


class AssumptionImpact(StrEnum):
    SQL_AFFECTING = "sql_affecting"
    NON_SQL_AFFECTING = "non_sql_affecting"
