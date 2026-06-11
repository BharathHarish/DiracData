"""Middleware for the v0 ReAct data analyst agent."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
import json
import re
from typing import Any

from typing_extensions import NotRequired

from diracdata.agents.artifacts import LearnedArtifactRepository
from diracdata.agents.prompt_loader import load_sql_reflection_prompt_v1
from diracdata.config.settings import DiracDataSettings
from diracdata.grounding.business import BusinessGroundingError, BusinessGroundingRepository
from diracdata.llms import agent_chat_model_from_settings
from diracdata.query_engines.base import QueryEngine
from diracdata.retrieval import CandidateBindingSearchService, compact_candidate_binding_context
from diracdata.storage.object_store import ObjectStore

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import HumanMessage, ToolMessage


class DataAnalystAgentState(AgentState):
    """State extensions used by DiracData middleware."""

    dirac_sql_guard_retries: NotRequired[int]
    dirac_protocol_guard_retries: NotRequired[int]
    dirac_probe_quality_guard_retries: NotRequired[int]
    dirac_semantic_guard_retries: NotRequired[int]
    dirac_sql_craft_guard_retries: NotRequired[int]
    dirac_answer_guard_retries: NotRequired[int]
    dirac_reflection_retries: NotRequired[int]


@dataclass(frozen=True)
class DataAnalystMiddlewareConfig:
    """Runtime dependencies needed by agent middleware."""

    settings: DiracDataSettings
    repository: LearnedArtifactRepository
    object_store: ObjectStore
    query_engine: QueryEngine
    base_system_prompt: str
    reflection_model: object | None = None
    candidate_search_service: CandidateBindingSearchService | None = None
    max_sql_guard_retries: int = 2


def build_data_analyst_middleware(
    *,
    config: DataAnalystMiddlewareConfig,
) -> list[object]:
    """Build middleware that keeps the create_agent loop compiler-aware."""
    from langchain.agents.middleware import dynamic_prompt

    runtime_config = _config_with_candidate_search(config)

    @dynamic_prompt
    def data_analyst_dynamic_prompt(request: object) -> str:
        return build_dynamic_system_prompt(
            config=runtime_config,
            messages=list(getattr(request, "messages", [])),
        )

    return [
        data_analyst_dynamic_prompt,
        SQLReflectionMiddleware(config=runtime_config),
        SQLExecutionGuardMiddleware(config=runtime_config),
    ]


def _config_with_candidate_search(
    config: DataAnalystMiddlewareConfig,
) -> DataAnalystMiddlewareConfig:
    if config.candidate_search_service is not None:
        return config
    if not config.settings.agent_candidate_search_enabled:
        return config
    return replace(
        config,
        candidate_search_service=CandidateBindingSearchService(
            settings=config.settings,
            object_store=config.object_store,
        ),
    )


def build_dynamic_system_prompt(
    *,
    config: DataAnalystMiddlewareConfig,
    messages: list[object],
) -> str:
    """Create the system prompt for the next model call."""
    question = latest_user_question(messages)
    sql_observation = latest_run_sql_observation(messages)
    evidence = sql_evidence_ledger(messages)
    sections = [
        config.base_system_prompt.strip(),
        _runtime_context(config),
        _schema_runtime_context(config),
        _business_grounding_context(config, question),
        _compiled_context_contract(config, question),
        _candidate_binding_context(config, question),
        _react_sql_contract(config),
        _analyst_protocol_context(
            config=config,
            question=question,
            evidence=evidence,
        ),
    ]
    if sql_observation is not None and sql_observation.get("status") == "error":
        sections.append(_sql_repair_context(sql_observation))
    elif evidence["final_sql_ok"] and not _missing_required_probe_checks(
        question,
        evidence,
        config=config,
    ):
        sections.append(_truth_context())
    return "\n\n".join(section for section in sections if section)


class SQLReflectionMiddleware(AgentMiddleware[DataAnalystAgentState]):
    """Reflect on proposed final SQL before executing potentially expensive queries."""

    state_schema = DataAnalystAgentState

    def __init__(self, *, config: DataAnalystMiddlewareConfig) -> None:
        self.config = config
        self._reflection_model = config.reflection_model

    def wrap_tool_call(self, request: object, handler: Callable[[object], ToolMessage]) -> ToolMessage:
        if not self.config.settings.agent_reflection_enabled:
            return handler(request)

        tool_call = getattr(request, "tool_call", {})
        if not _is_final_run_sql_tool_call(tool_call):
            return handler(request)

        state = getattr(request, "state", {})
        messages = _state_messages(state)
        if _reflection_retry_count(messages) >= self.config.settings.agent_reflection_max_retries:
            return handler(request)

        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
        sql = str(args.get("sql") or "") if isinstance(args, dict) else ""
        if not sql.strip():
            return handler(request)

        question = latest_user_question(messages)
        reflection = self._reflect(question=question, sql=sql, messages=messages)
        if reflection is None or reflection.get("decision") != "revise":
            return handler(request)

        return _reflection_tool_message(
            tool_call=tool_call,
            sql=sql,
            sql_dialect=self.config.settings.sql_dialect,
            reflection=reflection,
        )

    def _reflect(
        self,
        *,
        question: str,
        sql: str,
        messages: list[object],
    ) -> dict[str, Any] | None:
        model = self._reflection_model or self._create_reflection_model()
        packet = _reflection_review_packet(
            config=self.config,
            question=question,
            sql=sql,
            messages=messages,
        )
        try:
            model_messages = [
                {"role": "system", "content": load_sql_reflection_prompt_v1()},
                {"role": "user", "content": json.dumps(packet, indent=2, sort_keys=True)},
            ]
            try:
                response = model.invoke(
                    model_messages,
                    config={"callbacks": [], "metadata": {"dirac_internal": "sql_reflection"}},
                )
            except TypeError:
                response = model.invoke(model_messages)
        except Exception:
            return None
        return _parse_reflection_response(_content_text(getattr(response, "content", response)))

    def _create_reflection_model(self) -> object:
        if self._reflection_model is not None:
            return self._reflection_model
        settings = self.config.settings
        reflection_settings = replace(
            settings,
            agent_model_profile=(
                settings.agent_reflection_model_profile
                if settings.agent_reflection_model_profile is not None
                else settings.agent_model_profile
            ),
            agent_llm_provider=(
                settings.agent_reflection_llm_provider
                if settings.agent_reflection_llm_provider is not None
                else settings.agent_llm_provider
            ),
            agent_llm_model=(
                settings.agent_reflection_llm_model
                if settings.agent_reflection_llm_model is not None
                else settings.agent_llm_model
            ),
            agent_llm_max_tokens=settings.agent_reflection_llm_max_tokens,
            agent_llm_temperature=settings.agent_reflection_llm_temperature,
        )
        self._reflection_model = agent_chat_model_from_settings(reflection_settings)
        return self._reflection_model


class SQLExecutionGuardMiddleware(AgentMiddleware[DataAnalystAgentState]):
    """Prevent premature final answers for data questions without SQL evidence."""

    state_schema = DataAnalystAgentState

    def __init__(self, *, config: DataAnalystMiddlewareConfig) -> None:
        self.config = config

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: DataAnalystAgentState, runtime: object) -> dict[str, Any] | None:
        messages = list(state.get("messages", []))
        if not messages:
            return None

        last_message = messages[-1]
        if _message_has_tool_calls(last_message):
            return None
        if not _is_ai_message(last_message):
            return None

        question = latest_user_question(messages)
        if not requires_sql_evidence_for_config(self.config, question):
            return None
        evidence = sql_evidence_ledger(messages)
        if not evidence["final_sql_ok"]:
            retry_count = int(state.get("dirac_sql_guard_retries", 0))
            if retry_count >= self.config.max_sql_guard_retries:
                return None

            return {
                "messages": [HumanMessage(_sql_guard_message(self.config))],
                "dirac_sql_guard_retries": retry_count + 1,
                "jump_to": "model",
            }

        semantic_violations = semantic_sql_violations(
            config=self.config,
            question=question,
            evidence=evidence,
        )
        if semantic_violations:
            retry_count = int(state.get("dirac_semantic_guard_retries", 0))
            if retry_count >= self.config.max_sql_guard_retries:
                return None
            return {
                "messages": [
                    HumanMessage(
                        _semantic_sql_guard_message(
                            config=self.config,
                            violations=semantic_violations,
                            evidence=evidence,
                        )
                    )
                ],
                "dirac_semantic_guard_retries": retry_count + 1,
                "jump_to": "model",
            }

        missing_probe_checks = _missing_required_probe_checks(
            question,
            evidence,
            config=self.config,
        )
        if missing_probe_checks:
            retry_count = int(state.get("dirac_protocol_guard_retries", 0))
            if retry_count >= self.config.max_sql_guard_retries:
                return None
            return {
                "messages": [
                    HumanMessage(
                        _analyst_protocol_guard_message(
                            config=self.config,
                            missing_checks=missing_probe_checks,
                            evidence=evidence,
                        )
                    )
                ],
                "dirac_protocol_guard_retries": retry_count + 1,
                "jump_to": "model",
            }

        probe_quality_issues = probe_quality_violations(
            config=self.config,
            question=question,
            evidence=evidence,
        )
        if probe_quality_issues:
            retry_count = int(state.get("dirac_probe_quality_guard_retries", 0))
            if retry_count >= self.config.max_sql_guard_retries:
                return None
            return {
                "messages": [
                    HumanMessage(
                        _probe_quality_guard_message(
                            config=self.config,
                            violations=probe_quality_issues,
                            evidence=evidence,
                        )
                    )
                ],
                "dirac_probe_quality_guard_retries": retry_count + 1,
                "jump_to": "model",
            }

        sql_craft_issues = sql_craft_violations(
            question=question,
            evidence=evidence,
            config=self.config,
        )
        if sql_craft_issues:
            retry_count = int(state.get("dirac_sql_craft_guard_retries", 0))
            if retry_count >= self.config.max_sql_guard_retries:
                return None
            return {
                "messages": [
                    HumanMessage(
                        _sql_craft_guard_message(
                            config=self.config,
                            violations=sql_craft_issues,
                            evidence=evidence,
                        )
                    )
                ],
                "dirac_sql_craft_guard_retries": retry_count + 1,
                "jump_to": "model",
            }

        answer_violations = answer_shape_violations(
            question=question,
            evidence=evidence,
            answer=_content_text(getattr(last_message, "content", "")),
        )
        if not answer_violations:
            return None

        retry_count = int(state.get("dirac_answer_guard_retries", 0))
        if retry_count >= self.config.max_sql_guard_retries:
            return None
        return {
            "messages": [
                HumanMessage(
                    _answer_shape_guard_message(
                        config=self.config,
                        violations=answer_violations,
                        evidence=evidence,
                    )
                )
            ],
            "dirac_answer_guard_retries": retry_count + 1,
            "jump_to": "model",
        }


def latest_user_question(messages: list[object]) -> str:
    for message in reversed(messages):
        if _message_role(message) in {"human", "user"} and not _is_runtime_guard_message(message):
            return _content_text(getattr(message, "content", ""))
    return ""


def has_successful_run_sql_after_latest_user(messages: list[object]) -> bool:
    for message in reversed(messages):
        if _message_role(message) in {"human", "user"} and not _is_runtime_guard_message(message):
            return False
        observation = _run_sql_observation(message)
        if observation is not None and observation.get("status") == "ok":
            return True
    return False


def sql_evidence_ledger(messages: list[object]) -> dict[str, Any]:
    """Summarize SQL evidence collected since the latest real user question."""
    observations = []
    for message in _messages_after_latest_real_user(messages):
        observation = _run_sql_observation(message)
        if observation is not None:
            observations.append(observation)

    successful_probes = [
        observation
        for observation in observations
        if observation.get("status") == "ok" and observation.get("purpose") == "probe"
    ]
    successful_final = [
        observation
        for observation in observations
        if observation.get("status") == "ok" and observation.get("purpose") == "final"
    ]
    failed_sql = [observation for observation in observations if observation.get("status") == "error"]
    completed_checks = sorted(
        {
            str(observation.get("check_name"))
            for observation in successful_probes
            if observation.get("check_name")
        }
    )
    return {
        "sql_observations": observations,
        "probe_sql_ok": bool(successful_probes),
        "final_sql_ok": bool(successful_final),
        "successful_probe_count": len(successful_probes),
        "successful_final_count": len(successful_final),
        "failed_sql_count": len(failed_sql),
        "completed_probe_checks": completed_checks,
        "latest_final_sql": successful_final[-1] if successful_final else None,
        "latest_sql": observations[-1] if observations else None,
    }


def latest_run_sql_observation(messages: list[object]) -> dict[str, Any] | None:
    for message in reversed(messages):
        observation = _run_sql_observation(message)
        if observation is not None:
            return observation
    return None


def requires_sql_evidence(question: str) -> bool:
    """Heuristic gate for business questions that should not be answered from memory."""
    normalized = f" {question.lower()} "
    triggers = {
        " count ",
        " how many ",
        " total ",
        " sum ",
        " average ",
        " avg ",
        " compare ",
        " by ",
        " rate ",
        " ratio ",
    }
    return any(trigger in normalized for trigger in triggers)


def requires_sql_evidence_for_config(
    config: DataAnalystMiddlewareConfig,
    question: str,
) -> bool:
    return requires_sql_evidence(question) or bool(
        _business_grounding_matches(config=config, question=question)
    )


def requires_analyst_protocol(
    question: str,
    *,
    config: DataAnalystMiddlewareConfig | None = None,
) -> bool:
    normalized = f" {question.lower()} "
    complexity_triggers = {
        " compare ",
        " by ",
        " rate ",
        " ratio ",
        " segment ",
        " slice ",
        " filter ",
        " breakdown ",
        " cohort ",
    }
    requires_evidence = (
        requires_sql_evidence_for_config(config, question)
        if config is not None
        else requires_sql_evidence(question)
    )
    if requires_evidence and _has_metric_grounding_match(config=config, question=question):
        return True
    return requires_evidence and any(
        trigger in normalized for trigger in complexity_triggers
    )


def required_probe_checks(
    question: str,
    *,
    config: DataAnalystMiddlewareConfig | None = None,
) -> list[str]:
    if not requires_analyst_protocol(question, config=config):
        return []
    checks = ["base_population", "filter_selectivity", "join_fanout"]
    normalized = question.lower()
    if _question_mentions_time(question):
        checks.append("freshness")
    if " by " in f" {normalized} " or "compare" in normalized:
        checks.append("dimension_quality")
    return checks


def _missing_required_probe_checks(
    question: str,
    evidence: dict[str, Any],
    *,
    config: DataAnalystMiddlewareConfig | None = None,
) -> list[str]:
    required = required_probe_checks(question, config=config)
    completed = set(evidence.get("completed_probe_checks", []))
    return [check for check in required if check not in completed]


def _runtime_context(config: DataAnalystMiddlewareConfig) -> str:
    settings = config.settings
    return "\n".join(
        [
            "<dirac_runtime_context>",
            f"current_date: {date.today().isoformat()}",
            f"catalog: {settings.catalog}",
            f"database: {settings.database}",
            f"schema: {settings.schema}",
            f"query_engine: {settings.query_engine}",
            f"sql_dialect: {settings.sql_dialect}",
            f"final_result_max_rows: {settings.agent_sql_max_rows}",
            "</dirac_runtime_context>",
        ]
    )


def _schema_runtime_context(config: DataAnalystMiddlewareConfig) -> str:
    if config.settings.agent_inline_schema_context:
        return _schema_context(config)
    return "\n".join(
        [
            "<schema_context>",
            "Full schema is not inlined by default. Use schema_info_tool, get_table_descriptions, get_table_columns_tool, and get_column_description to discover scoped tables and columns before writing SQL.",
            "</schema_context>",
        ]
    )


def _schema_context(config: DataAnalystMiddlewareConfig) -> str:
    try:
        table_names = sorted(config.query_engine.list_tables())
    except Exception as exc:  # noqa: BLE001 - prompt context should not crash agent creation
        return f"<available_schema status=\"error\">{exc}</available_schema>"

    lines = ["<available_schema>"]
    for table_name in table_names:
        table_description = ""
        try:
            table_description = config.repository.table_descriptions(table_name).get(table_name, "")
        except Exception:
            table_description = ""
        lines.append(f"table: {table_name}")
        if table_description:
            lines.append(f"  meaning: {table_description}")
        try:
            columns = config.query_engine.describe_table(table_name)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  columns_error: {exc}")
            continue
        for column in columns:
            column_name = str(getattr(column, "name", ""))
            data_type = str(getattr(column, "data_type", ""))
            lines.append(f"  - {column_name}: {data_type}")
    lines.append("</available_schema>")
    return "\n".join(lines)


def _reflection_review_packet(
    *,
    config: DataAnalystMiddlewareConfig,
    question: str,
    sql: str,
    messages: list[object],
) -> dict[str, Any]:
    evidence = sql_evidence_ledger(messages)
    probe_summaries = []
    for observation in evidence.get("sql_observations", []):
        if not isinstance(observation, dict) or observation.get("purpose") != "probe":
            continue
        probe_summaries.append(
            {
                "check_name": observation.get("check_name"),
                "sql": _compact_sql_template(observation.get("sql")),
                "columns": observation.get("columns"),
                "rows": observation.get("rows"),
            }
        )
    return {
        "task": "Review proposed final SQL for semantic relevance before execution.",
        "sql_dialect": config.settings.sql_dialect,
        "user_question": question,
        "proposed_final_sql": sql,
        "business_grounding": _business_grounding_matches(config=config, question=question),
        "compiled_context_contract": _compiled_context_payload(config=config, question=question),
        "candidate_binding": compact_candidate_binding_context(
            _candidate_search_result(config=config, question=question)
        ),
        "profile_value_candidates": value_constraints_from_question(
            config=config,
            question=question,
        ),
        "schema_evidence": _reflection_schema_evidence(config=config, sql=sql),
        "prior_probe_evidence": probe_summaries,
        "instructions": [
            "Judge semantic match only; do not perform syntax review.",
            "A predicate is suspicious if it filters a different business entity than the user's noun phrase.",
            "Prior probes may contain mistakes; do not approve final SQL only because probes used the same predicate.",
            "Return revise only when the issue could change the answer.",
        ],
    }


def _reflection_schema_evidence(
    *,
    config: DataAnalystMiddlewareConfig,
    sql: str,
) -> dict[str, Any]:
    tables = _referenced_tables_in_sql(sql, dialect=config.settings.sql_dialect)
    if not tables:
        return {"tables": [], "note": "No referenced tables could be parsed from SQL."}
    table_payloads = []
    for table_name in tables[:12]:
        table_description = ""
        columns: dict[str, str] = {}
        try:
            table_description = config.repository.table_descriptions(table_name).get(table_name, "")
        except Exception:
            table_description = ""
        try:
            columns = config.repository.table_columns(table_name)
        except Exception:
            columns = {}
        table_payloads.append(
            {
                "table_name": table_name,
                "short_description": table_description,
                "columns": columns,
            }
        )
    return {"tables": table_payloads}


def _referenced_tables_in_sql(sql: str, *, dialect: str) -> list[str]:
    try:
        import sqlglot
        from sqlglot import exp

        expressions = sqlglot.parse(sql, read=dialect)
    except Exception:
        return []
    tables = []
    for expression in expressions:
        for table in expression.find_all(exp.Table):
            name = table.name
            if name:
                tables.append(str(name))
    return _dedupe_strings(tables)


def _is_final_run_sql_tool_call(tool_call: object) -> bool:
    if not isinstance(tool_call, dict):
        return False
    if tool_call.get("name") != "run_sql_tool":
        return False
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return False
    purpose = str(args.get("purpose") or "").strip().lower()
    check_name = str(args.get("check_name") or "").strip().lower()
    return purpose == "final" or check_name == "final_result"


def _state_messages(state: object) -> list[object]:
    if isinstance(state, dict):
        messages = state.get("messages", [])
    else:
        messages = getattr(state, "messages", [])
    return list(messages) if isinstance(messages, list) else []


def _reflection_retry_count(messages: list[object]) -> int:
    count = 0
    for message in _messages_after_latest_real_user(messages):
        observation = _run_sql_observation(message)
        if observation is None:
            continue
        if observation.get("error_type") == "semantic_reflection":
            count += 1
    return count


def _reflection_tool_message(
    *,
    tool_call: dict[str, Any],
    sql: str,
    sql_dialect: str,
    reflection: dict[str, Any],
) -> ToolMessage:
    payload = {
        "status": "error",
        "error_type": "semantic_reflection",
        "sql": sql,
        "sql_dialect": sql_dialect,
        "purpose": "final",
        "check_name": "final_result",
        "reflection": reflection,
        "error": "Final SQL was not executed because semantic reflection found a possible mismatch with the user's business intent.",
        "observation": (
            "Semantic reflection blocked this final SQL before execution. "
            "Treat this as a tool observation, repair the SQL if needed, and call run_sql_tool again."
        ),
        "repair_instruction": _reflection_repair_instruction(reflection),
    }
    return ToolMessage(
        content=json.dumps(payload, sort_keys=True),
        name="run_sql_tool",
        tool_call_id=str(tool_call.get("id") or "semantic_reflection"),
        status="error",
    )


def _reflection_repair_instruction(reflection: dict[str, Any]) -> str:
    issues = reflection.get("issues")
    if not isinstance(issues, list) or not issues:
        return "Review the final SQL predicates against the user question and retrieved grounding."
    suggestions = []
    for issue in issues[:4]:
        if not isinstance(issue, dict):
            continue
        message = str(issue.get("message") or "").strip()
        fix = str(issue.get("suggested_fix") or "").strip()
        if message and fix:
            suggestions.append(f"{message} Suggested fix: {fix}")
        elif message:
            suggestions.append(message)
        elif fix:
            suggestions.append(fix)
    return " ".join(suggestions) or "Repair the semantic mismatch before final SQL execution."


def _parse_reflection_response(text: str) -> dict[str, Any] | None:
    payload = _extract_json_object(text)
    if payload is None:
        return None
    decision = str(payload.get("decision") or "").strip().lower()
    if decision not in {"allow", "revise"}:
        return None
    confidence = str(payload.get("confidence") or "low").strip().lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
    issues = payload.get("issues")
    if not isinstance(issues, list):
        issues = []
    clean_issues = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity") or "warning").strip().lower()
        clean_issues.append(
            {
                "severity": severity if severity in {"blocking", "warning"} else "warning",
                "message": str(issue.get("message") or "").strip(),
                "sql_fragment": str(issue.get("sql_fragment") or "").strip(),
                "evidence": str(issue.get("evidence") or "").strip(),
                "suggested_fix": str(issue.get("suggested_fix") or "").strip(),
            }
        )
    return {
        "decision": decision,
        "confidence": confidence,
        "issues": clean_issues,
    }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(clean[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _business_grounding_context(config: DataAnalystMiddlewareConfig, question: str) -> str:
    sections = []
    matches = _business_grounding_matches(config=config, question=question)
    if matches:
        sections.append("<business_grounding_context>")
        sections.append(
            "Activated customer-supplied business grounding. These typed exact-alias matches are binding unless the user overrides them."
        )
        for match in matches:
            parameterized_sql = match.get("parameterized_sql")
            canonical_sql = None
            sql_contract = None
            if isinstance(parameterized_sql, dict):
                canonical_sql = _compact_sql_template(parameterized_sql.get("sql"))
                sql_contract = parameterized_sql.get("sql_contract")
            sections.append(
                " - "
                + "; ".join(
                    part
                    for part in [
                        f"section={match.get('section')}",
                        f"id={match.get('id')}",
                        f"name={match.get('name')}",
                        f"description={match.get('description')}",
                        f"calculation={match.get('calculation')}",
                        f"policy={match.get('policy')}",
                        f"columns={match.get('columns')}",
                        f"field={match.get('field')}",
                        f"required_tables={match.get('required_tables')}",
                        f"join_path={match.get('join_path')}",
                        f"canonical_parameterized_sql={canonical_sql}",
                        f"sql_contract={sql_contract}",
                        f"sql_template={_compact_sql_template(match.get('sql'))}",
                    ]
                    if part and not part.endswith("=None")
                )
            )
        sections.append("</business_grounding_context>")

    value_constraints = value_constraints_from_question(config=config, question=question)
    if value_constraints:
        if not sections:
            sections.append("<business_grounding_context>")
        sections.append("<user_value_constraints>")
        sections.append(
            "The user mentioned values that exist in profiled columns. Treat these as possible value matches, not resolved filters; bind each value to the correct business entity before filtering."
        )
        for constraint in value_constraints[:12]:
            sections.append(
                f" - {constraint['table']}.{constraint['column']} = {constraint['value']!r}"
            )
        sections.append("</user_value_constraints>")
        if sections[0] == "<business_grounding_context>" and sections[-1] != "</business_grounding_context>":
            sections.append("</business_grounding_context>")
    return "\n".join(sections)


def _compiled_context_contract(config: DataAnalystMiddlewareConfig, question: str) -> str:
    if not config.settings.agent_context_contract_enabled:
        return ""
    if not question.strip() or not requires_sql_evidence_for_config(config, question):
        return ""
    payload = _compiled_context_payload(config=config, question=question)
    if not payload.get("matched_patterns") and not payload.get("relevant_invariants"):
        return ""
    return "\n".join(
        [
            "<compiled_context_contract>",
            "Use this compact learned contract before SQL construction. It is mined from query history, business grounding, and learning review artifacts.",
            "Preserve required joins/invariants unless the user explicitly changes the analytical grain or business definition.",
            json.dumps(payload, indent=2, sort_keys=True),
            "</compiled_context_contract>",
        ]
    )


def _compiled_context_payload(
    *,
    config: DataAnalystMiddlewareConfig,
    question: str,
) -> dict[str, Any]:
    patterns = _top_query_library_patterns(config=config, question=question)
    invariants = _top_candidate_invariants(config=config, question=question, patterns=patterns)
    return {
        "matched_patterns": patterns,
        "relevant_invariants": invariants,
    }


def _top_query_library_patterns(
    *,
    config: DataAnalystMiddlewareConfig,
    question: str,
) -> list[dict[str, Any]]:
    try:
        patterns = config.repository.load_query_library_patterns()
    except Exception:
        return []
    scored = []
    for pattern in patterns:
        compact = pattern.get("compact_contract")
        if not isinstance(compact, dict):
            continue
        score = _context_match_score(question, compact)
        if score <= 0:
            continue
        scored.append((score, int(pattern.get("query_count") or 0), pattern))
    limit = max(config.settings.agent_context_contract_pattern_limit, 0)
    rows = []
    for score, _support, pattern in sorted(scored, key=lambda item: (-item[0], -item[1]))[:limit]:
        compact = pattern.get("compact_contract")
        rows.append(
            {
                "pattern_id": pattern.get("id"),
                "query_count": pattern.get("query_count"),
                "match_score": round(score, 3),
                "contract": _compact_context_contract_dict(compact if isinstance(compact, dict) else {}),
            }
        )
    return rows


def _top_candidate_invariants(
    *,
    config: DataAnalystMiddlewareConfig,
    question: str,
    patterns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        invariants = config.repository.load_candidate_invariants()
    except Exception:
        return []
    pattern_ids = {
        str(pattern.get("pattern_id"))
        for pattern in patterns
        if pattern.get("pattern_id") is not None
    }
    scored = []
    for invariant in invariants:
        invariant_id = str(invariant.get("id") or "")
        source_pattern_match = any(pattern_id and pattern_id in invariant_id for pattern_id in pattern_ids)
        score = _context_match_score(question, invariant) + (3.0 if source_pattern_match else 0.0)
        if score <= 0 and not source_pattern_match:
            continue
        scored.append((score, invariant))
    limit = max(config.settings.agent_context_contract_invariant_limit, 0)
    return [
        _compact_invariant(invariant, score=score)
        for score, invariant in sorted(scored, key=lambda item: -item[0])[:limit]
    ]


def _compact_context_contract_dict(compact: dict[str, Any]) -> dict[str, Any]:
    return {
        "fact_table": compact.get("fact_table"),
        "metrics": _limit_list(compact.get("metrics"), 8),
        "tables": _limit_list(compact.get("tables"), 10),
        "dimension_columns": _limit_list(compact.get("dimension_columns"), 12),
        "filter_columns": _limit_list(compact.get("filter_columns"), 12),
        "required_joins": _limit_list(compact.get("required_joins"), 8),
        "avoid_joins": _limit_list(compact.get("avoid_joins"), 5),
    }


def _compact_invariant(invariant: dict[str, Any], *, score: float) -> dict[str, Any]:
    return {
        "id": invariant.get("id"),
        "type": invariant.get("invariant_type"),
        "match_score": round(score, 3),
        "rule": invariant.get("rule"),
        "source": invariant.get("source"),
        "confidence": invariant.get("confidence"),
        "required_joins": _limit_list(invariant.get("required_joins"), 6),
        "avoid_joins": _limit_list(invariant.get("avoid_joins"), 6),
        "columns": _limit_list(invariant.get("columns"), 8),
        "time_columns": _limit_list(invariant.get("time_columns"), 8),
        "metric_id": invariant.get("metric_id"),
    }


def _context_match_score(question: str, payload: object) -> float:
    query_tokens = set(_tokenize_for_context(question))
    if not query_tokens:
        return 0.0
    payload_tokens = _tokenize_for_context(json.dumps(payload, sort_keys=True, default=str))
    if not payload_tokens:
        return 0.0
    counts = Counter(payload_tokens)
    return float(sum(counts.get(token, 0) for token in query_tokens))


def _tokenize_for_context(text: str) -> list[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "by",
        "for",
        "from",
        "give",
        "how",
        "in",
        "is",
        "me",
        "of",
        "on",
        "or",
        "show",
        "the",
        "to",
        "with",
    }
    tokens: list[str] = []
    for raw_token in re.findall(r"[a-z0-9_]+", text.lower()):
        for token in [raw_token, *raw_token.split("_")]:
            if len(token) > 1 and token not in stopwords:
                tokens.append(token)
    return tokens


def _limit_list(value: object, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value[: max(limit, 0)]


def _candidate_binding_context(config: DataAnalystMiddlewareConfig, question: str) -> str:
    service = config.candidate_search_service
    if service is None or not question.strip():
        return ""
    if not requires_sql_evidence_for_config(config, question):
        return ""
    result = _candidate_search_result(config=config, question=question)
    if result.get("status") != "ok":
        return ""
    compact = compact_candidate_binding_context(result)
    return "\n".join(
        [
            "<candidate_binding_context>",
            "Hybrid candidate binding search resolved likely columns and confounders before SQL construction.",
            "Use `predicate_bindings` as the preferred value-to-column contract.",
            "Do not use `rejected_confounders` as row filters unless the user explicitly asks for that business entity.",
            json.dumps(compact, indent=2, sort_keys=True),
            "</candidate_binding_context>",
        ]
    )


def _react_sql_contract(config: DataAnalystMiddlewareConfig) -> str:
    dialect = config.settings.sql_dialect
    return "\n".join(
        [
            "<react_sql_contract>",
            "For factual, numeric, or aggregate data questions, you must use tools and execute SQL.",
            "Do not give a final business number until `run_sql_tool` has returned `status: ok` for the final SQL.",
            "If `run_sql_tool` returns `status: error`, treat it as an observation, repair the SQL, and call `run_sql_tool` again.",
            "Repair the smallest broken part first; preserve the user's business intent and prior verified joins.",
            "When running verification SQL, call `run_sql_tool` with purpose='probe' and a descriptive check_name.",
            "When running the final business query, call `run_sql_tool` with purpose='final' and check_name='final_result'.",
            f"Generate SQL for dialect: {dialect}.",
            "Use one read-only SELECT statement. Prefer CTEs for multi-join or multi-metric questions.",
            "Qualify columns when a query joins tables that share column names.",
            "After a successful SQL result, verify row count, shape, null/zero patterns, and caveats before answering.",
            "</react_sql_contract>",
        ]
    )


def _analyst_protocol_context(
    *,
    config: DataAnalystMiddlewareConfig,
    question: str,
    evidence: dict[str, Any],
) -> str:
    required = required_probe_checks(question, config=config)
    missing = _missing_required_probe_checks(question, evidence, config=config)
    if not required:
        return "\n".join(
            [
                "<analyst_protocol>",
                "For simple data questions, execute focused SQL and answer from returned rows.",
                "For complex joins, ratios, or sliced metrics, upgrade to the staged analyst protocol.",
                "</analyst_protocol>",
            ]
        )
    return "\n".join(
        [
            "<analyst_protocol>",
            "This is a complex analytical question. Behave like a careful analyst, not a one-shot SQL generator.",
            "Required before final answer:",
            "- State the metric-owning base table and base grain in your own visible notes.",
            "- Build SQL using named CTEs when multiple joins, filters, metrics, or ratios are involved.",
            "- Carry the metric grain through the CTE names and avoid mixing grains from different entities unless the user asks for that.",
            "- Prefer joins from the metric-owning base table to one-to-one or many-to-one dimensions.",
            "- Avoid fanout unless the user asks for pairwise/relationship output.",
            "- Use learned business metric/default policies.",
            "- Preserve every user-stated value constraint listed in business_grounding_context.",
            "- For ratio metrics, return numerator and denominator columns alongside the ratio so the result is auditable.",
            "- Run verification probes before finalizing.",
            f"Required probe checks: {', '.join(required)}.",
            f"Completed probe checks: {', '.join(evidence.get('completed_probe_checks', [])) or 'none'}.",
            f"Missing probe checks: {', '.join(missing) or 'none'}.",
            "Probe check guidance:",
            "- base_population: count the metric-owning base table after the time window; return a count-like column.",
            "- filter_selectivity: show step-by-step count reductions after time, product, geography, status, and other user filters; return multiple steps or multiple count columns.",
            "- join_fanout: verify joined row count and distinct base-grain key count before/after joins; return at least two count-like columns.",
            "- freshness: check MAX of the metric-owning timestamp column for requested time-sensitive data.",
            "- dimension_quality: verify final grouped dimension values, row counts, and null/unknown buckets.",
            "Use `run_sql_tool(..., purpose='probe', check_name='<required_check>')` for each probe.",
            "Use `run_sql_tool(..., purpose='final', check_name='final_result')` for the final business query.",
            "</analyst_protocol>",
        ]
    )


def semantic_sql_violations(
    *,
    config: DataAnalystMiddlewareConfig,
    question: str,
    evidence: dict[str, Any],
) -> list[str]:
    """Return final-SQL semantic violations that are obvious from grounding/profile context."""
    latest_final = evidence.get("latest_final_sql")
    if not isinstance(latest_final, dict):
        return []
    sql = str(latest_final.get("sql") or "")
    if not sql.strip():
        return []

    normalized_question = f" {question.lower()} "
    normalized_sql = _normalize_for_semantic_checks(sql)
    violations: list[str] = []

    for constraint in value_constraints_from_question(config=config, question=question):
        value = str(constraint["value"]).lower()
        column = str(constraint["column"]).lower()
        if value not in normalized_sql:
            violations.append(
                f"User-stated value {constraint['value']!r} from {constraint['table']}.{constraint['column']} is missing from final SQL."
            )
        elif column not in normalized_sql:
            violations.append(
                f"Final SQL contains value {constraint['value']!r} but does not qualify it with the profiled column {constraint['table']}.{constraint['column']}."
            )

    for contract in grounding_contract_violations(
        config=config,
        question=question,
        normalized_sql=normalized_sql,
    ):
        violations.append(contract)

    for violation in candidate_binding_violations(
        config=config,
        question=question,
        sql=sql,
    ):
        violations.append(violation)

    return _dedupe_strings(violations)


def candidate_binding_violations(
    *,
    config: DataAnalystMiddlewareConfig,
    question: str,
    sql: str,
) -> list[str]:
    """Validate final SQL against candidate search rejected confounders."""
    result = _candidate_search_result(config=config, question=question)
    if result.get("status") != "ok":
        return []
    violations = []
    for confounder in result.get("rejected_confounders", []):
        if not isinstance(confounder, dict):
            continue
        table_name = str(confounder.get("table_name") or "")
        column_name = str(confounder.get("column_name") or "")
        value = str(confounder.get("value") or "")
        if not table_name or not column_name:
            continue
        if not _sql_filters_column_ref(
            sql=sql,
            table_name=table_name,
            column_name=column_name,
            value=value,
            dialect=config.settings.sql_dialect,
        ):
            continue
        reason = str(confounder.get("reason") or "").strip()
        phrase = str(confounder.get("user_phrase") or "").strip()
        detail = f" {reason}" if reason else ""
        violations.append(
            (
                f"Candidate binding rejected {table_name}.{column_name}"
                f" for user phrase {phrase!r}; final SQL filters it anyway.{detail}"
            )
        )
    return _dedupe_strings(violations)


def grounding_contract_violations(
    *,
    config: DataAnalystMiddlewareConfig,
    question: str,
    normalized_sql: str,
) -> list[str]:
    """Validate final SQL against customer-supplied grounding, without schema-specific rules."""
    violations = []
    for match in _business_grounding_matches(config=config, question=question):
        section = str(match.get("section") or "")
        if section not in {"metrics", "defaults", "definitions", "sql_templates"}:
            continue
        label = _grounding_label(match)
        for table_name in _grounding_required_tables(match):
            if table_name.lower() not in normalized_sql:
                violations.append(f"Grounding contract {label} requires table {table_name!r}.")
        for column_ref in _grounding_required_columns(match):
            column_name = column_ref.split(".", 1)[-1]
            if column_name.lower() not in normalized_sql:
                violations.append(
                    f"Grounding contract {label} requires column {column_ref!r}."
                )
        for literal in _grounding_required_literals(match):
            if literal.lower() not in normalized_sql:
                violations.append(
                    f"Grounding contract {label} requires literal value {literal!r}."
                )
        parameterized_violation = _parameterized_sql_contract_violation(match, normalized_sql)
        if parameterized_violation:
            violations.append(f"Grounding contract {label}: {parameterized_violation}")
        aggregate_violation = _conditional_aggregate_violation(match, normalized_sql)
        if aggregate_violation:
            violations.append(f"Grounding contract {label}: {aggregate_violation}")
    return violations


def probe_quality_violations(
    *,
    config: DataAnalystMiddlewareConfig,
    question: str,
    evidence: dict[str, Any],
) -> list[str]:
    """Detect shallow probe SQL that satisfies check names but not analyst evidence."""
    required = required_probe_checks(question, config=config)
    if not required:
        return []

    latest_by_check = _latest_successful_probe_by_check(evidence)
    constraints = value_constraints_from_question(config=config, question=question)
    violations = []
    for check_name in required:
        observation = latest_by_check.get(check_name)
        if observation is None:
            continue
        violation = _probe_quality_violation(
            check_name=check_name,
            observation=observation,
            constraints=constraints,
        )
        if violation:
            violations.append(violation)
    return violations


def sql_craft_violations(
    *,
    question: str,
    evidence: dict[str, Any],
    config: DataAnalystMiddlewareConfig | None = None,
) -> list[str]:
    """Detect final SQL/result shapes that are too opaque for analyst-grade answers."""
    if not requires_analyst_protocol(question, config=config):
        return []
    latest_final = evidence.get("latest_final_sql")
    if not isinstance(latest_final, dict):
        return []

    sql = str(latest_final.get("sql") or "")
    normalized_question = f" {question.lower()} "
    normalized_sql = _normalize_for_semantic_checks(sql)
    columns = [str(column).lower() for column in latest_final.get("columns", [])]
    column_text = " ".join(columns)
    violations = []

    if "with " not in normalized_sql[:40] and " with " not in normalized_sql:
        violations.append(
            "Complex analytical final SQL must use named CTEs so grain, filters, joins, and aggregation are auditable."
        )

    if _question_requests_ratio(normalized_question):
        if not any(
            term in column_text
            for term in [
                "total_attempt",
                "attempt_count",
                "denominator",
                "total_count",
                "total",
                "base_count",
            ]
        ):
            violations.append(
                "Rate or ratio output must include an auditable denominator column."
            )
        if not any(
            term in column_text
            for term in [
                "numerator",
                "matched_count",
                "qualified_count",
                "converted_count",
                "success_count",
                "successful",
                "positive_count",
            ]
        ):
            violations.append(
                "Rate or ratio output must include an auditable numerator column."
            )

    return _dedupe_strings(violations)


def answer_shape_violations(
    *,
    question: str,
    evidence: dict[str, Any],
    answer: str,
) -> list[str]:
    """Detect final answers that distort compact SQL result evidence."""
    latest_final = evidence.get("latest_final_sql")
    if not isinstance(latest_final, dict):
        return []
    rows = latest_final.get("rows")
    columns = latest_final.get("columns")
    if not isinstance(rows, list) or not rows or not isinstance(columns, list):
        return []

    violations: list[str] = []
    normalized_question = f" {question.lower()} "
    if " by " in normalized_question or "compare" in normalized_question:
        string_columns = [
            str(column)
            for column in columns
            if all(
                isinstance(row, dict) and isinstance(row.get(str(column)), str)
                for row in rows[: min(len(rows), 5)]
            )
        ]
        if len(string_columns) >= 2 and len(rows) <= 50:
            normalized_answer = _normalize_value_text(answer)
            missing_pairs = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                values = [str(row.get(column, "")) for column in string_columns[:2]]
                if not all(values):
                    continue
                if not _values_appear_near_each_other(values, normalized_answer):
                    missing_pairs.append(" x ".join(values))

            if missing_pairs:
                preview = ", ".join(missing_pairs[:6])
                suffix = "" if len(missing_pairs) <= 6 else f", and {len(missing_pairs) - 6} more"
                violations.append(
                    (
                        "Final answer does not preserve the two-dimensional final result rows. "
                        f"Missing visible row pairs: {preview}{suffix}."
                    )
                )

    numeric_violation = _answer_numeric_fidelity_violation(
        question=question,
        latest_final=latest_final,
        answer=answer,
    )
    if numeric_violation:
        violations.append(numeric_violation)
    return violations


def _answer_numeric_fidelity_violation(
    *,
    question: str,
    latest_final: dict[str, Any],
    answer: str,
) -> str | None:
    """Detect unsupported summary numbers in prose around compact SQL results."""
    rows = latest_final.get("rows")
    columns = latest_final.get("columns")
    if not isinstance(rows, list) or not rows or not isinstance(columns, list):
        return None
    if len(rows) > 50:
        return None

    supported_values = _supported_answer_numbers(latest_final)
    question_values = {
        value
        for token in _extract_numeric_tokens(question)
        if (value := _numeric_token_value(token)) is not None
    }
    unsupported_tokens: list[str] = []
    for line in answer.splitlines():
        if not _line_has_summary_numeric_claim(line):
            continue
        for token in _extract_numeric_tokens(line):
            value = _numeric_token_value(token)
            if value is None:
                continue
            if _numeric_value_is_year(value):
                continue
            if _numeric_value_supported(value, question_values):
                continue
            if _numeric_value_supported(value, supported_values):
                continue
            unsupported_tokens.append(token.strip())

    unsupported_tokens = _dedupe_strings(unsupported_tokens)
    if not unsupported_tokens:
        return None
    preview = ", ".join(unsupported_tokens[:8])
    suffix = "" if len(unsupported_tokens) <= 8 else f", and {len(unsupported_tokens) - 8} more"
    return (
        "Final answer includes aggregate or summary numeric claims that are not supported "
        f"by the final SQL result rows or additive result totals: {preview}{suffix}."
    )


def _line_has_summary_numeric_claim(line: str) -> bool:
    stripped = line.strip()
    if not stripped or "|" in stripped or stripped.startswith("```"):
        return False
    if not _extract_numeric_tokens(stripped):
        return False
    normalized = stripped.lower()
    summary_terms = [
        "total",
        "overall",
        "summary",
        "data scope",
        "scope",
        "across",
        "accounting",
        "representing",
        "dominant",
        "highest",
        "lowest",
        "rows",
        "records",
        "count",
        "counts",
    ]
    return any(term in normalized for term in summary_terms)


def _extract_numeric_tokens(text: str) -> list[str]:
    return re.findall(r"(?<![\w.])-?[$₹]?\d[\d,]*(?:\.\d+)?%?", text)


def _numeric_token_value(token: str) -> float | None:
    cleaned = token.strip().replace(",", "").replace("$", "").replace("₹", "")
    cleaned = cleaned.rstrip("%")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _supported_answer_numbers(latest_final: dict[str, Any]) -> list[float]:
    rows = latest_final.get("rows")
    columns = latest_final.get("columns")
    if not isinstance(rows, list) or not isinstance(columns, list):
        return []

    values: list[float] = [float(len(rows))]
    column_sums: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for column in columns:
            column_name = str(column)
            value = row.get(column_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            numeric = float(value)
            values.extend(_numeric_equivalents(numeric))
            if _is_additive_result_column(column_name):
                column_sums[column_name] = column_sums.get(column_name, 0.0) + numeric

    for numeric in column_sums.values():
        values.extend(_numeric_equivalents(numeric))
    return values


def _numeric_equivalents(value: float) -> list[float]:
    values = [value]
    if 0 <= value <= 1:
        values.append(value * 100)
    return values


def _is_additive_result_column(column_name: str) -> bool:
    normalized = column_name.lower()
    if any(
        term in normalized
        for term in [
            "rate",
            "ratio",
            "avg",
            "average",
            "percent",
            "percentage",
        ]
    ):
        return False
    return any(
        term in normalized
        for term in [
            "count",
            "total",
            "sum",
            "amount",
            "volume",
            "value",
            "revenue",
            "sales",
            "attempt",
            "row",
            "record",
            "successful",
            "qualified",
            "matched",
            "converted",
        ]
    )


def _numeric_value_supported(value: float, supported_values: set[float] | list[float]) -> bool:
    for supported in supported_values:
        tolerance = max(0.05, abs(supported) * 0.001)
        if abs(value - supported) <= tolerance:
            return True
    return False


def _numeric_value_is_year(value: float) -> bool:
    return value.is_integer() and 1900 <= value <= 2100


def value_constraints_from_question(
    *,
    config: DataAnalystMiddlewareConfig,
    question: str,
) -> list[dict[str, str]]:
    """Find user-mentioned literal values that are known from profiling artifacts."""
    normalized_question = _normalize_value_text(question)
    if not normalized_question:
        return []
    try:
        profile = config.repository.load_profile()
    except Exception:
        return []

    constraints: list[dict[str, str]] = []
    for table in profile.get("tables", []):
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("table_name") or "")
        for column in table.get("columns", []):
            if not isinstance(column, dict):
                continue
            column_name = str(column.get("column_name") or "")
            values = [
                *[
                    item.get("value")
                    for item in column.get("top_values", [])
                    if isinstance(item, dict)
                ],
                *column.get("distinct_values", []),
            ]
            for raw_value in values:
                if not isinstance(raw_value, str):
                    continue
                value = raw_value.strip()
                if not _profile_value_is_actionable(value):
                    continue
                if _normalized_value_in_text(value, normalized_question):
                    constraints.append(
                        {
                            "table": table_name,
                            "column": column_name,
                            "value": value,
                        }
                    )
    return _dedupe_constraints(constraints)


def _sql_repair_context(observation: dict[str, Any]) -> str:
    return "\n".join(
        [
            "<sql_error_observation>",
            json.dumps(observation, indent=2, sort_keys=True),
            "Action: repair the SQL and call `run_sql_tool` again. Do not answer the user yet.",
            "</sql_error_observation>",
        ]
    )


def _truth_context() -> str:
    return "\n".join(
        [
            "<truth_compiler_mode>",
            "A SQL result is available. Use only returned rows for final business numbers.",
            "Use probe results as evidence for caveats, especially count reduction, join fanout, freshness, and dimension quality.",
            "First reproduce the final_result rows exactly when row_count is compact; preserve all grouping dimensions from the SQL result.",
            "Do not replace a two-dimension result with separate one-dimension summaries.",
            "Do not add totals, dominant segments, highest/lowest rankings, or consistency claims unless those exact values were returned by final SQL or a separate SQL result.",
            "Mention uncertainty only when supported by tool evidence or data shape.",
            "Do not claim highest/lowest/dominant segments unless that claim is directly supported by the final result rows.",
            "Include the SQL used and a compact result table when useful.",
            "</truth_compiler_mode>",
        ]
    )


def _sql_guard_message(config: DataAnalystMiddlewareConfig) -> str:
    return "\n".join(
        [
            "Runtime guard: this appears to be a factual data question, but no successful `run_sql_tool` result exists for this turn.",
            "Continue the ReAct loop instead of finalizing.",
            f"Use SQL dialect `{config.settings.sql_dialect}`.",
            "Call the necessary context tools, then call `run_sql_tool`.",
            "If `run_sql_tool` returns an error, repair the SQL from that observation and call it again.",
        ]
    )


def _analyst_protocol_guard_message(
    *,
    config: DataAnalystMiddlewareConfig,
    missing_checks: list[str],
    evidence: dict[str, Any],
) -> str:
    return "\n".join(
        [
            "Runtime analyst protocol guard: final answer blocked.",
            "A final SQL result exists, but required analyst verification evidence is missing.",
            f"Missing probe checks: {', '.join(missing_checks)}.",
            f"Completed probe checks: {', '.join(evidence.get('completed_probe_checks', [])) or 'none'}.",
            f"Use SQL dialect `{config.settings.sql_dialect}`.",
            "Run the missing probe SQL with `run_sql_tool` using purpose='probe' and the exact check_name.",
            "Then answer only after the required probes and final SQL evidence are both available.",
        ]
    )


def _probe_quality_guard_message(
    *,
    config: DataAnalystMiddlewareConfig,
    violations: list[str],
    evidence: dict[str, Any],
) -> str:
    return "\n".join(
        [
            "Runtime probe-quality guard: final answer blocked.",
            "The required probe check names exist, but one or more probes are too shallow to establish analyst confidence.",
            "Violations:",
            *[f"- {violation}" for violation in violations],
            f"Completed probe checks: {', '.join(evidence.get('completed_probe_checks', [])) or 'none'}.",
            f"Use SQL dialect `{config.settings.sql_dialect}`.",
            "Rerun the weak probe SQL with the same purpose='probe' and check_name.",
            "Then call the final SQL again if the repaired probe changes the validated grain, filters, or joins.",
        ]
    )


def _semantic_sql_guard_message(
    *,
    config: DataAnalystMiddlewareConfig,
    violations: list[str],
    evidence: dict[str, Any],
) -> str:
    final_sql = ""
    latest_final = evidence.get("latest_final_sql")
    if isinstance(latest_final, dict):
        final_sql = str(latest_final.get("sql") or "")
    return "\n".join(
        [
            "Runtime semantic SQL guard: final answer blocked.",
            "A final SQL result exists, but the final SQL appears to violate the user's business intent or retrieved grounding.",
            "Violations:",
            *[f"- {violation}" for violation in violations],
            f"Use SQL dialect `{config.settings.sql_dialect}`.",
            "Repair the final SQL while preserving completed probe evidence where still applicable.",
            "Call `run_sql_tool` again with purpose='final' and check_name='final_result'.",
            "Then answer only from the repaired final SQL result.",
            "<previous_final_sql>",
            final_sql,
            "</previous_final_sql>",
        ]
    )


def _sql_craft_guard_message(
    *,
    config: DataAnalystMiddlewareConfig,
    violations: list[str],
    evidence: dict[str, Any],
) -> str:
    final_sql = ""
    latest_final = evidence.get("latest_final_sql")
    if isinstance(latest_final, dict):
        final_sql = str(latest_final.get("sql") or "")
    return "\n".join(
        [
            "Runtime SQL craft guard: final answer blocked.",
            "The final SQL ran, but it is not sufficiently auditable for this analytical question.",
            "Violations:",
            *[f"- {violation}" for violation in violations],
            f"Use SQL dialect `{config.settings.sql_dialect}`.",
            "Repair the final SQL with explicit CTEs and transparent metric columns.",
            "Call `run_sql_tool` again with purpose='final' and check_name='final_result'.",
            "Then answer only from the repaired final SQL result.",
            "<previous_final_sql>",
            final_sql,
            "</previous_final_sql>",
        ]
    )


def _answer_shape_guard_message(
    *,
    config: DataAnalystMiddlewareConfig,
    violations: list[str],
    evidence: dict[str, Any],
) -> str:
    latest_final = evidence.get("latest_final_sql")
    row_count = latest_final.get("row_count") if isinstance(latest_final, dict) else "unknown"
    columns = latest_final.get("columns") if isinstance(latest_final, dict) else []
    return "\n".join(
        [
            "Runtime answer-shape guard: final answer blocked.",
            "The final SQL result exists, but the natural-language answer did not preserve the returned result shape.",
            "Violations:",
            *[f"- {violation}" for violation in violations],
            f"Final result columns: {columns}.",
            f"Final result row_count: {row_count}.",
            "Answer again from the existing final SQL result. Do not rerun SQL unless needed.",
            "For compact grouped results, reproduce every final_result row with all grouping dimensions and metric columns.",
            "Do not add derived totals or aggregate claims unless those values are present in SQL tool results.",
            "Do not mention this runtime guard, apologize, or phrase the answer as a correction.",
            f"Use SQL dialect `{config.settings.sql_dialect}` if you do need a repair query.",
        ]
    )


def _run_sql_observation(message: object) -> dict[str, Any] | None:
    if _message_role(message) != "tool":
        return None
    if getattr(message, "name", None) not in {None, "run_sql_tool"}:
        return None
    content = _content_text(getattr(message, "content", ""))
    if "run_sql_tool" not in str(getattr(message, "name", "")) and "sql" not in content.lower():
        return None
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    if "status" not in value or "sql" not in value:
        return None
    value = dict(value)
    value["purpose"] = _observation_purpose(value)
    value["check_name"] = _observation_check_name(value)
    return value


def _latest_successful_probe_by_check(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    probes = {}
    for observation in evidence.get("sql_observations", []):
        if not isinstance(observation, dict):
            continue
        if observation.get("status") != "ok" or observation.get("purpose") != "probe":
            continue
        check_name = observation.get("check_name")
        if not isinstance(check_name, str) or not check_name:
            continue
        probes[check_name] = observation
    return probes


def _probe_quality_violation(
    *,
    check_name: str,
    observation: dict[str, Any],
    constraints: list[dict[str, str]],
) -> str | None:
    sql = _normalize_for_semantic_checks(str(observation.get("sql") or ""))
    rows = observation.get("rows")
    columns = observation.get("columns")
    if not isinstance(rows, list):
        rows = []
    if not isinstance(columns, list):
        columns = []
    count_like_columns = _count_like_columns(columns, rows)

    if check_name == "base_population":
        if "count(" not in sql or not count_like_columns:
            return "base_population must return a count from the metric-owning base table."
        return None

    if check_name == "filter_selectivity":
        shows_reduction = (
            len(rows) >= 2
            or len(count_like_columns) >= 2
            or "union all" in sql
            or "case when" in sql
        )
        if "count(" not in sql or not shows_reduction:
            return "filter_selectivity must show count reduction across at least two filter stages."
        if constraints and not any(
            str(constraint["value"]).lower() in sql for constraint in constraints
        ):
            return "filter_selectivity must include at least one profiled user-stated value filter."
        return None

    if check_name == "join_fanout":
        if "join" not in sql or "distinct" not in sql or "count(" not in sql:
            return "join_fanout must join the selected path and compare row count with distinct base-grain count."
        if len(count_like_columns) < 2:
            return "join_fanout must return at least two count-like columns, such as joined_rows and distinct_base_keys."
        return None

    if check_name == "freshness":
        column_text = " ".join(str(column).lower() for column in columns)
        if "max(" not in sql or not any(term in f"{sql} {column_text}" for term in ["time", "date", "timestamp"]):
            return "freshness must return MAX of the metric-owning time column."
        return None

    if check_name == "dimension_quality":
        if "group by" not in sql or "count(" not in sql:
            return "dimension_quality must group by the final dimensions and return row counts."
        if not rows:
            return "dimension_quality returned no rows, so final dimension values were not verified."
        return None

    return None


def _count_like_columns(columns: list[Any], rows: list[Any]) -> list[str]:
    count_columns = []
    for column in columns:
        name = str(column)
        normalized = name.lower()
        if not any(
            token in normalized
            for token in [
                "count",
                "rows",
                "row_count",
                "attempt",
                "total",
                "distinct",
                "population",
            ]
        ):
            continue
        if any(
            isinstance(row, dict) and isinstance(row.get(name), (int, float))
            for row in rows
        ):
            count_columns.append(name)
    return count_columns


def _messages_after_latest_real_user(messages: list[object]) -> list[object]:
    latest_index = -1
    for index, message in enumerate(messages):
        if _message_role(message) in {"human", "user"} and not _is_runtime_guard_message(message):
            latest_index = index
    return messages[latest_index + 1 :]


def _observation_purpose(observation: dict[str, Any]) -> str:
    purpose = str(observation.get("purpose") or "").strip().lower()
    if purpose in {"probe", "final"}:
        return purpose
    check_name = str(observation.get("check_name") or "").strip()
    if check_name:
        return "probe" if check_name != "final_result" else "final"
    return "final"


def _observation_check_name(observation: dict[str, Any]) -> str | None:
    check_name = observation.get("check_name")
    if isinstance(check_name, str) and check_name.strip():
        return check_name.strip().lower()
    if _observation_purpose(observation) != "probe":
        return None
    sql = str(observation.get("sql") or "").lower()
    if "max(" in sql and any(term in sql for term in ["time", "date", "timestamp"]):
        return "freshness"
    if "join" in sql and "distinct" in sql and "count(" in sql:
        return "join_fanout"
    if "join" in sql and "count(" in sql:
        return "join_fanout"
    if "where" in sql and "count(" in sql:
        return "filter_selectivity"
    if "count(" in sql:
        return "base_population"
    return None


def _message_role(message: object) -> str:
    role = getattr(message, "type", None) or getattr(message, "role", None)
    return str(role or "").lower()


def _message_has_tool_calls(message: object) -> bool:
    return bool(getattr(message, "tool_calls", None))


def _is_ai_message(message: object) -> bool:
    return _message_role(message) in {"ai", "assistant"}


def _is_runtime_guard_message(message: object) -> bool:
    content = _content_text(getattr(message, "content", ""))
    return any(
        content.startswith(prefix)
        for prefix in [
            "Runtime guard:",
            "Runtime analyst protocol guard:",
            "Runtime probe-quality guard:",
            "Runtime semantic SQL guard:",
            "Runtime SQL craft guard:",
            "Runtime answer-shape guard:",
        ]
    )


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content)


def _business_grounding_matches(
    *,
    config: DataAnalystMiddlewareConfig,
    question: str,
) -> list[dict[str, Any]]:
    repository = BusinessGroundingRepository(
        settings=config.settings,
        object_store=config.object_store,
    )
    try:
        resolution = repository.resolve_business_intent(
            question,
            limit=max(1, min(config.settings.agent_business_search_limit, 8)),
        )
    except BusinessGroundingError:
        return []

    matches = []
    activated = resolution.get("activated", {})
    if not isinstance(activated, dict):
        return []
    for section in ["metrics", "defaults", "definitions", "sql_templates"]:
        rows = activated.get(section)
        if isinstance(rows, list):
            matches.extend(row for row in rows if isinstance(row, dict))
    return matches


def _candidate_search_result(
    *,
    config: DataAnalystMiddlewareConfig,
    question: str,
) -> dict[str, Any]:
    service = config.candidate_search_service
    if service is None:
        return {"status": "disabled"}
    try:
        result = service.search(question)
    except Exception as exc:  # noqa: BLE001 - prompt context should never crash agent loop
        return {"status": "error", "error": str(exc)}
    return result if isinstance(result, dict) else {"status": "error"}


def _has_metric_grounding_match(
    *,
    config: DataAnalystMiddlewareConfig | None,
    question: str,
) -> bool:
    if config is None:
        return False
    return any(
        str(match.get("section") or "") in {"metrics", "sql_templates"}
        for match in _business_grounding_matches(config=config, question=question)
    )


def _business_grounding_item(
    payload: dict[str, Any],
    *,
    section: object,
    item_id: object,
) -> dict[str, Any] | None:
    if not isinstance(section, str) or not isinstance(item_id, str):
        return None
    rows = payload.get(section)
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("id") == item_id:
            return dict(row)
    return None


def _grounding_label(match: dict[str, Any]) -> str:
    section = str(match.get("section") or "grounding")
    item_id = str(match.get("id") or match.get("name") or "unknown")
    return f"{section}.{item_id}"


def _grounding_required_tables(match: dict[str, Any]) -> list[str]:
    tables = []
    for key in ["required_tables", "tables"]:
        value = match.get(key)
        if isinstance(value, list):
            tables.extend(str(item) for item in value if isinstance(item, str))
        elif isinstance(value, str):
            tables.append(value)
    parameterized_sql = match.get("parameterized_sql")
    if isinstance(parameterized_sql, dict):
        value = parameterized_sql.get("required_tables")
        if isinstance(value, list):
            tables.extend(str(item) for item in value if isinstance(item, str))
        elif isinstance(value, str):
            tables.append(value)
    return _dedupe_strings(tables)


def _grounding_required_columns(match: dict[str, Any]) -> list[str]:
    refs = []
    for key in ["columns", "filters", "field"]:
        value = match.get(key)
        if isinstance(value, list):
            refs.extend(str(item) for item in value if isinstance(item, str) and "." in item)
        elif isinstance(value, str) and "." in value:
            refs.append(value)
    for key in ["calculation", "policy", "sql"]:
        refs.extend(_extract_column_refs(str(match.get(key) or "")))
    parameterized_sql = match.get("parameterized_sql")
    if isinstance(parameterized_sql, dict):
        value = parameterized_sql.get("required_columns")
        if isinstance(value, list):
            refs.extend(str(item) for item in value if isinstance(item, str) and "." in item)
        elif isinstance(value, str) and "." in value:
            refs.append(value)
        refs.extend(_extract_column_refs(str(parameterized_sql.get("sql") or "")))
    for edge in match.get("join_path", []) if isinstance(match.get("join_path"), list) else []:
        if isinstance(edge, list):
            refs.extend(str(item) for item in edge if isinstance(item, str) and "." in item)
    return _dedupe_strings(refs)


def _grounding_required_literals(match: dict[str, Any]) -> list[str]:
    literals = []
    for key in ["calculation", "policy", "sql"]:
        literals.extend(_extract_sql_literals(str(match.get(key) or "")))
    parameterized_sql = match.get("parameterized_sql")
    if isinstance(parameterized_sql, dict):
        literals.extend(_extract_sql_literals(str(parameterized_sql.get("sql") or "")))
    return _dedupe_strings([literal for literal in literals if _literal_is_actionable(literal)])


def _parameterized_sql_contract_violation(
    match: dict[str, Any],
    normalized_sql: str,
) -> str | None:
    parameterized_sql = match.get("parameterized_sql")
    if not isinstance(parameterized_sql, dict):
        return None
    contract = parameterized_sql.get("sql_contract")
    if not isinstance(contract, dict):
        return None

    aggregate = str(contract.get("aggregate") or "").lower()
    measure = str(contract.get("measure") or "")
    condition = contract.get("condition")
    if aggregate == "sum" and measure and isinstance(condition, dict):
        measure_column = measure.split(".", 1)[-1]
        condition_columns = []
        condition_column = condition.get("column")
        if isinstance(condition_column, str):
            condition_columns.append(condition_column.split(".", 1)[-1])
        condition_literals = [
            str(condition.get("value"))
        ] if condition.get("value") is not None else []
        if not _conditional_sum_aggregate_expression_is_satisfied(
            normalized_sql=normalized_sql,
            measure_column=measure_column,
            required_columns=condition_columns,
            required_literals=condition_literals,
        ):
            return (
                f"canonical metric SQL requires SUM over {measure!r} to be conditioned on "
                f"{condition.get('column')!r} {condition.get('operator', '=')} {condition.get('value')!r}."
            )

    denominator = contract.get("denominator")
    if isinstance(denominator, dict):
        denominator_violation = _denominator_contract_violation(
            denominator=denominator,
            normalized_sql=normalized_sql,
        )
        if denominator_violation:
            return denominator_violation
    return None


def _denominator_contract_violation(
    *,
    denominator: dict[str, Any],
    normalized_sql: str,
) -> str | None:
    forbidden_filters = denominator.get("forbidden_base_filters")
    if not isinstance(forbidden_filters, list):
        return None

    for forbidden_filter in forbidden_filters:
        if not isinstance(forbidden_filter, dict):
            continue
        column_ref = forbidden_filter.get("column")
        if not isinstance(column_ref, str) or "." not in column_ref:
            continue
        column_name = column_ref.split(".", 1)[-1].lower()
        if not _where_clauses_filter_column(normalized_sql, column_name):
            continue
        grain = denominator.get("grain")
        reason = forbidden_filter.get("reason")
        reason_text = f" {reason}" if isinstance(reason, str) and reason.strip() else ""
        grain_text = f" at grain {grain!r}" if isinstance(grain, str) and grain.strip() else ""
        return (
            f"canonical metric SQL denominator counts all rows{grain_text}; "
            f"do not filter {column_ref!r} in WHERE or base CTEs.{reason_text}"
        )
    return None


def _conditional_sum_aggregate_expression_is_satisfied(
    *,
    normalized_sql: str,
    measure_column: str,
    required_columns: list[str],
    required_literals: list[str],
) -> bool:
    aggregate_expressions = [
        match.group(0)
        for match in re.finditer(
            rf"sum\s*\((?P<body>[^)]*{re.escape(measure_column)}[^)]*)\)",
            normalized_sql,
            flags=re.IGNORECASE,
        )
    ]
    if not aggregate_expressions:
        return True
    condition_columns = [column.lower() for column in required_columns if column]
    condition_literals = [
        literal.lower()
        for literal in required_literals
        if _literal_is_actionable(literal)
    ]
    return any(
        _condition_terms_present(expression, condition_columns, condition_literals)
        for expression in aggregate_expressions
    )


def _conditional_aggregate_violation(match: dict[str, Any], normalized_sql: str) -> str | None:
    """Validate generic "aggregate where condition" metric contracts when available."""
    for contract_text in [str(match.get("calculation") or ""), str(match.get("sql") or "")]:
        requirement = _conditional_sum_requirement(contract_text)
        if requirement is None:
            continue
        measure_column = requirement["measure_column"]
        required_columns = requirement["condition_columns"]
        required_literals = requirement["condition_literals"]
        if not measure_column or not required_columns and not required_literals:
            continue
        if _conditional_sum_is_satisfied(
            normalized_sql=normalized_sql,
            measure_column=measure_column,
            required_columns=required_columns,
            required_literals=required_literals,
        ):
            continue
        return (
            f"conditional aggregate over {measure_column!r} must apply "
            "its grounding condition inside the aggregate or in the final SQL filter."
        )
    return None


def _conditional_sum_requirement(contract_text: str) -> dict[str, list[str] | str] | None:
    normalized = _normalize_for_semantic_checks(contract_text)
    match = re.search(
        r"sum\s*\(\s*(?:[a-z_][a-z0-9_]*\.)?(?P<measure>[a-z_][a-z0-9_]*)\s*\)"
        r"(?P<condition>[\s\S]{0,400})",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    condition = match.group("condition")
    if " where " not in condition and " filter " not in condition and " when " not in condition:
        return None
    return {
        "measure_column": match.group("measure"),
        "condition_columns": [ref.split(".", 1)[-1] for ref in _extract_column_refs(condition)],
        "condition_literals": _extract_sql_literals(condition),
    }


def _conditional_sum_is_satisfied(
    *,
    normalized_sql: str,
    measure_column: str,
    required_columns: list[str],
    required_literals: list[str],
) -> bool:
    aggregate_expressions = [
        match.group(0)
        for match in re.finditer(
            rf"sum\s*\((?P<body>[^)]*{re.escape(measure_column)}[^)]*)\)",
            normalized_sql,
            flags=re.IGNORECASE,
        )
    ]
    if not aggregate_expressions:
        return True

    condition_columns = [column.lower() for column in required_columns if column]
    condition_literals = [
        literal.lower()
        for literal in required_literals
        if _literal_is_actionable(literal)
    ]
    for expression in aggregate_expressions:
        if _condition_terms_present(expression, condition_columns, condition_literals):
            return True

    where_clause = _final_sql_where_clause(normalized_sql)
    return bool(where_clause) and _condition_terms_present(
        where_clause,
        condition_columns,
        condition_literals,
    )


def _condition_terms_present(
    text: str,
    columns: list[str],
    literals: list[str],
) -> bool:
    normalized = text.lower()
    has_columns = not columns or any(column in normalized for column in columns)
    has_literals = not literals or any(literal in normalized for literal in literals)
    return has_columns and has_literals


def _final_sql_where_clause(normalized_sql: str) -> str:
    match = re.search(r"\bwhere\b(?P<body>[\s\S]*?)(\bgroup\s+by\b|\border\s+by\b|$)", normalized_sql)
    if match is None:
        return ""
    return match.group("body")


def _where_clauses_filter_column(normalized_sql: str, column_name: str) -> bool:
    if not column_name:
        return False
    for where_clause in _sql_where_clauses(normalized_sql):
        if not _where_clause_filters_column(where_clause, column_name):
            continue
        return True
    return False


def _sql_where_clauses(normalized_sql: str) -> list[str]:
    clauses = []
    for match in re.finditer(
        r"\bwhere\b(?P<body>[\s\S]*?)(?=\bgroup\s+by\b|\bhaving\b|\border\s+by\b|\bqualify\b|\blimit\b|\bunion\b|\)\s*,|\)\s*select\b|$)",
        normalized_sql,
        flags=re.IGNORECASE,
    ):
        body = match.group("body").strip()
        if body:
            clauses.append(body)
    return clauses


def _where_clause_filters_column(where_clause: str, column_name: str) -> bool:
    column_pattern = rf"(?:[a-z_][a-z0-9_]*\.)?{re.escape(column_name)}"
    filter_pattern = (
        rf"\b{column_pattern}\b\s*(?:\bnot\s+in\b|\bin\b|>=|<=|!=|<>|=|<|>|\blike\b|\bis\b)"
    )
    if re.search(filter_pattern, where_clause, flags=re.IGNORECASE):
        return True
    reversed_filter_pattern = (
        rf"(?:'[^']*'|\"[^\"]*\"|\d+(?:\.\d+)?)\s*(?:=|!=|<>|<|>|<=|>=)\s*\b{column_pattern}\b"
    )
    return re.search(reversed_filter_pattern, where_clause, flags=re.IGNORECASE) is not None


def _sql_filters_column_ref(
    *,
    sql: str,
    table_name: str,
    column_name: str,
    value: str,
    dialect: str,
) -> bool:
    normalized_sql = _normalize_for_semantic_checks(sql)
    normalized_value = str(value).strip().lower()
    aliases = _table_aliases_in_sql(sql=sql, table_name=table_name, dialect=dialect)
    column_refs = {f"{alias}.{column_name.lower()}" for alias in aliases}
    for column_ref in column_refs:
        if _normalized_sql_filters_ref(normalized_sql, column_ref, normalized_value):
            return True
    return _table_local_unqualified_filter(
        normalized_sql=normalized_sql,
        table_name=table_name,
        column_name=column_name,
        value=normalized_value,
    )


def _table_aliases_in_sql(*, sql: str, table_name: str, dialect: str) -> set[str]:
    aliases = {table_name.lower()}
    try:
        import sqlglot
        from sqlglot import exp

        expressions = sqlglot.parse(sql, read=dialect)
        for expression in expressions:
            for table in expression.find_all(exp.Table):
                if str(table.name).lower() != table_name.lower():
                    continue
                aliases.add(str(table.name).lower())
                alias = table.alias
                if alias:
                    aliases.add(str(alias).lower())
    except Exception:
        pass
    pattern = (
        rf"\b(?:from|join)\s+(?:[a-z_][a-z0-9_]*\.)?{re.escape(table_name.lower())}"
        rf"(?:\s+(?:as\s+)?([a-z_][a-z0-9_]*))?"
    )
    for match in re.finditer(pattern, sql.lower(), flags=re.IGNORECASE):
        alias = match.group(1)
        if alias and alias not in {"on", "where", "join", "left", "right", "inner", "outer", "full"}:
            aliases.add(alias)
    return aliases


def _column_aliases_in_sql(*, sql: str, column_name: str) -> set[str]:
    aliases = set()
    pattern = rf"\b{re.escape(column_name.lower())}\b\s+as\s+([a-z_][a-z0-9_]*)"
    for match in re.finditer(pattern, sql.lower(), flags=re.IGNORECASE):
        aliases.add(match.group(1))
    return aliases


def _normalized_sql_filters_ref(
    normalized_sql: str,
    column_ref: str,
    value: str,
) -> bool:
    ref = re.escape(column_ref.lower())
    value_patterns = _sql_value_patterns(value)
    operators = r"(?:\bnot\s+in\b|\bin\b|>=|<=|!=|<>|=|<|>|\blike\b|\bis\b)"
    for value_pattern in value_patterns:
        forward = rf"\b{ref}\b\s*{operators}\s*{value_pattern}"
        reverse = rf"{value_pattern}\s*(?:=|!=|<>|<|>|<=|>=)\s*\b{ref}\b"
        if re.search(forward, normalized_sql, flags=re.IGNORECASE):
            return True
        if re.search(reverse, normalized_sql, flags=re.IGNORECASE):
            return True
    return False


def _table_local_unqualified_filter(
    *,
    normalized_sql: str,
    table_name: str,
    column_name: str,
    value: str,
) -> bool:
    table_pattern = rf"\b(?:from|join)\s+(?:[a-z_][a-z0-9_]*\.)?{re.escape(table_name.lower())}\b"
    for table_match in re.finditer(table_pattern, normalized_sql, flags=re.IGNORECASE):
        window = normalized_sql[table_match.start() : table_match.start() + 1200]
        if _normalized_sql_filters_unqualified_ref(window, column_name.lower(), value):
            return True
    return False


def _normalized_sql_filters_unqualified_ref(
    normalized_sql: str,
    column_name: str,
    value: str,
) -> bool:
    ref = re.escape(column_name.lower())
    value_patterns = _sql_value_patterns(value)
    operators = r"(?:\bnot\s+in\b|\bin\b|>=|<=|!=|<>|=|<|>|\blike\b|\bis\b)"
    for value_pattern in value_patterns:
        forward = rf"(?<!\.)\b{ref}\b\s*{operators}\s*{value_pattern}"
        reverse = rf"{value_pattern}\s*(?:=|!=|<>|<|>|<=|>=)\s*(?<!\.)\b{ref}\b"
        if re.search(forward, normalized_sql, flags=re.IGNORECASE):
            return True
        if re.search(reverse, normalized_sql, flags=re.IGNORECASE):
            return True
    return False


def _sql_value_patterns(value: str) -> list[str]:
    clean = value.strip().strip("'\"").lower()
    if not clean:
        return [r"(?:'[^']*'|\"[^\"]*\"|\d+(?:\.\d+)?)"]
    escaped = re.escape(clean)
    return [
        rf"'{escaped}'",
        rf'"{escaped}"',
        rf"\b{escaped}\b",
    ]


def _extract_column_refs(value: str) -> list[str]:
    return [
        f"{table}.{column}"
        for table, column in re.findall(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b",
            value,
        )
    ]


def _extract_sql_literals(value: str) -> list[str]:
    return [
        match.group(1) or match.group(2)
        for match in re.finditer(r"'([^']+)'|\"([^\"]+)\"", value)
    ]


def _literal_is_actionable(value: str) -> bool:
    clean = value.strip()
    if not clean:
        return False
    if clean.startswith("{{") and clean.endswith("}}"):
        return False
    return len(clean) >= 2


def _compact_sql_template(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return re.sub(r"\s+", " ", value.strip())[:500]


def _question_requests_ratio(normalized_question: str) -> bool:
    return _question_mentions_any(
        normalized_question,
        {
            " rate ",
            " ratio ",
            " percentage ",
            " percent ",
            " conversion ",
        },
    )


def _question_mentions_time(question: str) -> bool:
    normalized = f" {question.lower()} "
    if _question_mentions_any(
        normalized,
        {
            " date ",
            " day ",
            " daily ",
            " week ",
            " weekly ",
            " month ",
            " monthly ",
            " quarter ",
            " quarterly ",
            " year ",
            " yearly ",
            " today ",
            " yesterday ",
            " tomorrow ",
            " last ",
            " next ",
            " current ",
        },
    ):
        return True
    if re.search(r"\b(19|20)\d{2}\b", question):
        return True
    if re.search(
        r"\b(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|"
        r"aug|august|sep|sept|september|oct|october|nov|november|dec|december)\b",
        question,
        flags=re.IGNORECASE,
    ):
        return True
    return False


def _normalize_for_semantic_checks(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _question_mentions_any(normalized_question: str, phrases: set[str]) -> bool:
    return any(phrase in normalized_question for phrase in phrases)


def _normalize_value_text(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return f" {clean} " if clean else ""


def _profile_value_is_actionable(value: str) -> bool:
    clean = _normalize_value_text(value).strip()
    if len(clean) < 3:
        return False
    stop_values = {"yes", "no", "true", "false", "none", "null", "unknown", "other"}
    return clean not in stop_values


def _normalized_value_in_text(value: str, normalized_text: str) -> bool:
    normalized_value = _normalize_value_text(value)
    if not normalized_value.strip():
        return False
    return normalized_value in normalized_text


def _values_appear_near_each_other(values: list[str], normalized_text: str) -> bool:
    position_groups = []
    for value in values:
        normalized_value = _normalize_value_text(value).strip()
        if not normalized_value:
            return False
        pattern = rf"(?<![a-z0-9]){re.escape(normalized_value)}(?![a-z0-9])"
        positions = [match.start() for match in re.finditer(pattern, normalized_text)]
        if not positions:
            return False
        position_groups.append(positions)
    if len(position_groups) == 1:
        return True
    for first_position in position_groups[0]:
        if _has_near_positions(first_position, position_groups[1:]):
            return True
    return False


def _has_near_positions(anchor: int, remaining_groups: list[list[int]]) -> bool:
    if not remaining_groups:
        return True
    for position in remaining_groups[0]:
        if abs(position - anchor) <= 180 and _has_near_positions(
            min(anchor, position),
            remaining_groups[1:],
        ):
            return True
    return False


def _dedupe_constraints(values: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    deduped = []
    for value in values:
        key = (value["table"], value["column"], value["value"].lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def _dedupe_strings(values: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
