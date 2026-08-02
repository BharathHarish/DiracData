"""Config -- the ONE home for every constant and connection setting. Nothing numeric is hardcoded
elsewhere: leaf functions default their knobs to the fields here (`_DEFAULTS = Config()`), and the
Agent threads a `Config.from_env()` so any `DIRACDATA_*` env var overrides a default at runtime.

Two groups:
- infra: how to reach the model and the object store (read by utils/model_factory + utils/object_store).
- runtime: the agent loop's budgets, row caps, display widths, and paths (threaded through the Agent).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path


class Stage(StrEnum):
    """The five distinct model-call stages. Each can run on its own model + sampling budget."""
    FRAMING = "framing"
    AUTHORING = "authoring"     # the analyst loop (subagents inherit this)
    VERIFY = "verify"
    SUMMARIZE = "summarize"
    LEARN = "learn"


@dataclass(frozen=True)
class StageConfig:
    """Per-stage overrides from ENV. Every field None -> the stage uses the global default (below)."""
    model_profile: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ResolvedStage:
    """A stage's effective model + sampling after applying overrides, global fallback, and the
    determinism clamp. What the ModelRegistry builds from."""
    profile_id: str
    max_tokens: int
    temperature: float
    reasoning_effort: str | None


@dataclass(frozen=True)
class Config:
    # --- engine / schema ---
    schema: str = "default_schema"
    sql_dialect: str = "duckdb"
    sql_engine: str = "duckdb"
    data_root: Path = Path("data")

    # --- model (utils/model_factory) ---
    agent_model_profile: str = "anthropic_haiku_45"
    agent_llm_provider: str = "anthropic"
    agent_llm_model: str = "claude-haiku-4-5-20251001"
    agent_llm_max_tokens: int = 8192
    agent_llm_temperature: float = 0.0
    deterministic_sampling: bool = True
    agent_llm_seed: int | None = None
    stream_tokens: bool = False
    stream_envelope_enabled: bool = False   # route model streams through diracdata.streaming (envelope)
    stream_mode: str = "messages"           # off | messages | updates | all (display filter)
    llm_timeout_seconds: int | None = 120
    llm_max_retries: int = 1
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    openai_api_key: str | None = None
    google_api_key: str | None = None
    bedrock_api_key: str | None = None
    bedrock_region: str | None = None
    aws_region: str = "us-east-1"

    # --- object store (utils/object_store) ---
    object_store: str = "local"
    artifact_bucket: str = "diracdata"
    s3_endpoint_url: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    local_artifact_root: Path = Path(".diracdata/artifacts")
    cache_max_entries: int = 512

    # --- agent loop budgets ---
    max_steps: int = 24
    framing_max_steps: int = 8
    subagent_max_depth: int = 1
    subagent_facts_merge: int = 8    # sub's facts folded back into the parent
    learn_max_steps: int = 20

    # --- row caps ---
    nav_max_rows: int = 100          # navigation run_sql preview
    query_max_rows: int = 200        # run_sql / query_result
    result_query_max_rows: int = 200  # ResultStore.query slice
    learn_max_rows: int = 50         # learning run_sql preview
    preview_rows: int = 100          # ResultStore stored-result preview
    preview_all_max: int = 200       # ResultStore full-return threshold
    find_examples_limit: int = 5

    # --- multi-engine: reconciler (combines result parquets, spills instead of OOM) + execution ---
    reconciler_memory_limit: str = "2GB"    # DuckDB reconciler memory cap; spills to temp beyond it
    reconciler_temp_dir: str | None = None   # spill dir; None -> a subdir of the result store's temp
    reconciler_threads: int | None = None    # None -> DuckDB default
    executor: str = "inline"                 # inline | process (isolated worker pool -> Phase 1.5)

    # --- display / truncation widths ---
    obs_cap: int = 12000             # tool observation fed back to the loop
    tool_call_display: int = 200     # streamed tool-call echo
    tool_result_display: int = 400   # streamed tool-result echo
    sql_display_cap: int = 200       # SQL shown in memory/result renders
    subagent_task_display: int = 90  # sub-task echoed in the sink
    profile_distinct_cap: int = 1000  # distinct values profile_column scans/caches
    profile_values_display: int = 80  # distinct values shown by profile_column
    example_values_shown: int = 5    # value samples in a column description
    value_str_width: int = 24        # per-value truncation in that sample
    learn_obs_cap: int = 6000
    learn_call_display: int = 160
    learn_result_display: int = 300

    # --- profiling / footprint (learning + stewardship) ---
    profiler_complete_max: int = 40
    profiler_sample: int = 12
    footprint_max_cols: int = 16
    footprint_max_joins: int = 3
    hint_values_shown: int = 8         # example values shown in a term-domain hint
    slot_match_max_diffs: int = 3      # reject a slot match with more than this many literal diffs

    # --- stewardship data-quality thresholds (the data_check tool) ---
    steward_null_pct_flag: float = 5.0   # flag a column whose null rate exceeds this %
    steward_max_z: float = 6.0           # flag a value beyond this |z-score|
    steward_orphan_pct_max: float = 1.0  # a join is "clean" only below this orphan %
    steward_fanout_max: float = 1.5      # ...and at/below this fan-out ratio
    steward_flags_shown: int = 3         # flags / row-counts shown in the trust line

    # --- faithfulness gate (numbers in the answer must trace to a query result) ---
    faithfulness_min_magnitude: float = 100.0  # below this a bare int is a count, not an aggregate
    faithfulness_rel_tol: float = 0.005        # relative rounding tolerance when matching a figure
    faithfulness_abs_tol: float = 1.0          # absolute tolerance floor

    # --- durable conversation memory ---
    conversations_dir: Path = Path(".diracdata/conversations")
    transcript_result_cap: int = 4000  # per tool-call args/result chars kept in transcript.md

    # --- agentic memory (schema-scoped experiences.md, async curator). Off by default. ---
    agentic_memory_enabled: bool = False
    curator_max_steps: int = 6              # the curator's tool-loop budget

    # --- per-stage model + sampling overrides (empty by default -> every stage uses the global model) ---
    stages: dict[Stage, StageConfig] = field(default_factory=dict)

    # --- agentic model router (off by default). ON: the main model chooses the analyst's model +
    # budget per turn by reasoning over the model catalog. No routing policy lives here -- only the
    # rollout gate and a safety bound on how many times a failed pick may be re-routed upward. ---
    router_enabled: bool = False
    router_max_escalations: int = 1

    def resolve_stage(self, stage: Stage) -> ResolvedStage:
        """The effective model + sampling for a stage: the stage override if set, else the global
        default; temperature is clamped to 0 when deterministic_sampling is on (the reproducibility
        floor wins regardless of any per-stage temperature)."""
        sc = self.stages.get(stage) or StageConfig()
        profile_id = sc.model_profile or self.agent_model_profile
        max_tokens = sc.max_tokens if sc.max_tokens is not None else self.agent_llm_max_tokens
        if self.deterministic_sampling:
            temperature = 0.0
        else:
            temperature = sc.temperature if sc.temperature is not None else self.agent_llm_temperature
        return ResolvedStage(profile_id=profile_id, max_tokens=max_tokens,
                             temperature=temperature, reasoning_effort=sc.reasoning_effort)

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> "Config":
        """Build a Config from `DIRACDATA_*` env vars, FALLING BACK to the field defaults above -- so
        every default value is written exactly once (as the field default) and an env var only
        overrides it. `replace` on a defaults instance also means a newly-added field keeps its
        default even before it is wired here."""
        if env_file is not None:
            load_dotenv(env_file)
        d = cls()          # the single source of every default value
        g = os.environ.get
        return replace(
            d,
            schema=_str("DIRACDATA_SCHEMA", d.schema),
            sql_dialect=_str("DIRACDATA_SQL_DIALECT", d.sql_dialect),
            sql_engine=_str("DIRACDATA_SQL_ENGINE", d.sql_engine),
            data_root=_path("DIRACDATA_DATA_ROOT", d.data_root),
            agent_model_profile=_str("DIRACDATA_AGENT_MODEL_PROFILE", d.agent_model_profile),
            agent_llm_provider=_str("DIRACDATA_AGENT_LLM_PROVIDER", d.agent_llm_provider),
            agent_llm_model=_str("DIRACDATA_AGENT_LLM_MODEL", d.agent_llm_model),
            agent_llm_max_tokens=_int("DIRACDATA_AGENT_LLM_MAX_TOKENS", d.agent_llm_max_tokens),
            agent_llm_temperature=_float("DIRACDATA_AGENT_LLM_TEMPERATURE", d.agent_llm_temperature),
            deterministic_sampling=_bool("DIRACDATA_DETERMINISTIC_SAMPLING", d.deterministic_sampling),
            agent_llm_seed=_opt_int("DIRACDATA_AGENT_LLM_SEED", default=d.agent_llm_seed),
            stream_tokens=_bool("DIRACDATA_STREAM_TOKENS", d.stream_tokens),
            stream_envelope_enabled=_bool("DIRACDATA_STREAM_ENVELOPE_ENABLED", d.stream_envelope_enabled),
            stream_mode=_str("DIRACDATA_STREAM_MODE", d.stream_mode),
            llm_timeout_seconds=_opt_int("DIRACDATA_LLM_TIMEOUT_SECONDS", default=d.llm_timeout_seconds),
            llm_max_retries=_int("DIRACDATA_LLM_MAX_RETRIES", d.llm_max_retries),
            anthropic_api_key=g("DIRACDATA_ANTHROPIC_API_KEY") or g("ANTHROPIC_API_KEY") or d.anthropic_api_key,
            anthropic_base_url=_str("DIRACDATA_ANTHROPIC_BASE_URL", d.anthropic_base_url),
            openai_api_key=g("DIRACDATA_OPENAI_API_KEY") or g("OPENAI_API_KEY") or d.openai_api_key,
            google_api_key=(g("DIRACDATA_GOOGLE_API_KEY") or g("GOOGLE_API_KEY")
                            or g("GEMINI_API_KEY") or d.google_api_key),
            bedrock_api_key=g("DIRACDATA_BEDROCK_API_KEY") or g("AWS_BEARER_TOKEN_BEDROCK") or d.bedrock_api_key,
            bedrock_region=g("DIRACDATA_BEDROCK_REGION") or g("AWS_REGION") or d.bedrock_region,
            aws_region=g("DIRACDATA_AWS_REGION") or g("AWS_REGION") or d.aws_region,
            object_store=_str("DIRACDATA_OBJECT_STORE", d.object_store),
            artifact_bucket=_str("DIRACDATA_ARTIFACT_BUCKET", d.artifact_bucket),
            s3_endpoint_url=g("DIRACDATA_S3_ENDPOINT_URL") or d.s3_endpoint_url,
            aws_access_key_id=g("DIRACDATA_AWS_ACCESS_KEY_ID") or d.aws_access_key_id,
            aws_secret_access_key=g("DIRACDATA_AWS_SECRET_ACCESS_KEY") or d.aws_secret_access_key,
            local_artifact_root=_path("DIRACDATA_LOCAL_ARTIFACT_ROOT", d.local_artifact_root),
            cache_max_entries=_int("DIRACDATA_CACHE_MAX_ENTRIES", d.cache_max_entries),
            max_steps=_int("DIRACDATA_MAX_STEPS", d.max_steps),
            framing_max_steps=_int("DIRACDATA_FRAMING_MAX_STEPS", d.framing_max_steps),
            subagent_max_depth=_int("DIRACDATA_SUBAGENT_MAX_DEPTH", d.subagent_max_depth),
            subagent_facts_merge=_int("DIRACDATA_SUBAGENT_FACTS_MERGE", d.subagent_facts_merge),
            learn_max_steps=_int("DIRACDATA_LEARN_MAX_STEPS", d.learn_max_steps),
            nav_max_rows=_int("DIRACDATA_NAV_MAX_ROWS", d.nav_max_rows),
            query_max_rows=_int("DIRACDATA_QUERY_MAX_ROWS", d.query_max_rows),
            result_query_max_rows=_int("DIRACDATA_RESULT_QUERY_MAX_ROWS", d.result_query_max_rows),
            learn_max_rows=_int("DIRACDATA_LEARN_MAX_ROWS", d.learn_max_rows),
            preview_rows=_int("DIRACDATA_PREVIEW_ROWS", d.preview_rows),
            preview_all_max=_int("DIRACDATA_PREVIEW_ALL_MAX", d.preview_all_max),
            find_examples_limit=_int("DIRACDATA_FIND_EXAMPLES_LIMIT", d.find_examples_limit),
            reconciler_memory_limit=_str("DIRACDATA_RECONCILER_MEMORY_LIMIT", d.reconciler_memory_limit),
            reconciler_temp_dir=_str_or_none("DIRACDATA_RECONCILER_TEMP_DIR") or d.reconciler_temp_dir,
            reconciler_threads=_opt_int("DIRACDATA_RECONCILER_THREADS", default=d.reconciler_threads),
            executor=_str("DIRACDATA_EXECUTOR", d.executor),
            obs_cap=_int("DIRACDATA_OBS_CAP", d.obs_cap),
            tool_call_display=_int("DIRACDATA_TOOL_CALL_DISPLAY", d.tool_call_display),
            tool_result_display=_int("DIRACDATA_TOOL_RESULT_DISPLAY", d.tool_result_display),
            sql_display_cap=_int("DIRACDATA_SQL_DISPLAY_CAP", d.sql_display_cap),
            subagent_task_display=_int("DIRACDATA_SUBAGENT_TASK_DISPLAY", d.subagent_task_display),
            profile_distinct_cap=_int("DIRACDATA_PROFILE_DISTINCT_CAP", d.profile_distinct_cap),
            profile_values_display=_int("DIRACDATA_PROFILE_VALUES_DISPLAY", d.profile_values_display),
            example_values_shown=_int("DIRACDATA_EXAMPLE_VALUES_SHOWN", d.example_values_shown),
            value_str_width=_int("DIRACDATA_VALUE_STR_WIDTH", d.value_str_width),
            learn_obs_cap=_int("DIRACDATA_LEARN_OBS_CAP", d.learn_obs_cap),
            learn_call_display=_int("DIRACDATA_LEARN_CALL_DISPLAY", d.learn_call_display),
            learn_result_display=_int("DIRACDATA_LEARN_RESULT_DISPLAY", d.learn_result_display),
            profiler_complete_max=_int("DIRACDATA_PROFILER_COMPLETE_MAX", d.profiler_complete_max),
            profiler_sample=_int("DIRACDATA_PROFILER_SAMPLE", d.profiler_sample),
            footprint_max_cols=_int("DIRACDATA_FOOTPRINT_MAX_COLS", d.footprint_max_cols),
            footprint_max_joins=_int("DIRACDATA_FOOTPRINT_MAX_JOINS", d.footprint_max_joins),
            hint_values_shown=_int("DIRACDATA_HINT_VALUES_SHOWN", d.hint_values_shown),
            slot_match_max_diffs=_int("DIRACDATA_SLOT_MATCH_MAX_DIFFS", d.slot_match_max_diffs),
            steward_null_pct_flag=_float("DIRACDATA_STEWARD_NULL_PCT_FLAG", d.steward_null_pct_flag),
            steward_max_z=_float("DIRACDATA_STEWARD_MAX_Z", d.steward_max_z),
            steward_orphan_pct_max=_float("DIRACDATA_STEWARD_ORPHAN_PCT_MAX", d.steward_orphan_pct_max),
            steward_fanout_max=_float("DIRACDATA_STEWARD_FANOUT_MAX", d.steward_fanout_max),
            steward_flags_shown=_int("DIRACDATA_STEWARD_FLAGS_SHOWN", d.steward_flags_shown),
            faithfulness_min_magnitude=_float("DIRACDATA_FAITHFULNESS_MIN_MAGNITUDE", d.faithfulness_min_magnitude),
            faithfulness_rel_tol=_float("DIRACDATA_FAITHFULNESS_REL_TOL", d.faithfulness_rel_tol),
            faithfulness_abs_tol=_float("DIRACDATA_FAITHFULNESS_ABS_TOL", d.faithfulness_abs_tol),
            conversations_dir=_path("DIRACDATA_CONVERSATIONS_DIR", d.conversations_dir),
            transcript_result_cap=_int("DIRACDATA_TRANSCRIPT_RESULT_CAP", d.transcript_result_cap),
            agentic_memory_enabled=_bool("DIRACDATA_AGENTIC_MEMORY_ENABLED", d.agentic_memory_enabled),
            curator_max_steps=_int("DIRACDATA_CURATOR_MAX_STEPS", d.curator_max_steps),
            router_enabled=_bool("DIRACDATA_ROUTER_ENABLED", d.router_enabled),
            router_max_escalations=_int("DIRACDATA_ROUTER_MAX_ESCALATIONS", d.router_max_escalations),
            stages=_stages_from_env(),
        )


def settings_from_env(env_file: str | Path | None = ".env") -> Config:
    """Back-compat entry (scripts/tests): identical to `Config.from_env`."""
    return Config.from_env(env_file)


def load_dotenv(path: str | Path) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#") or "=" not in clean:
            continue
        key, value = clean.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip("\"'")


def _stages_from_env() -> dict[Stage, StageConfig]:
    """Read DIRACDATA_STAGE_<STAGE>_{MODEL_PROFILE,MAX_TOKENS,TEMPERATURE,REASONING_EFFORT}. A stage
    with no vars set gets an empty StageConfig (all None) -> it falls back to the global model."""
    out: dict[Stage, StageConfig] = {}
    for stage in Stage:
        p = f"DIRACDATA_STAGE_{stage.name}_"
        out[stage] = StageConfig(
            model_profile=_str_or_none(p + "MODEL_PROFILE"),
            max_tokens=_opt_int(p + "MAX_TOKENS"),
            temperature=_opt_float(p + "TEMPERATURE"),
            reasoning_effort=_str_or_none(p + "REASONING_EFFORT"),
        )
    return out


def _str_or_none(key: str) -> str | None:
    value = os.environ.get(key)
    return value if value not in (None, "") else None


def _opt_float(key: str, *, default: float | None = None) -> float | None:
    value = os.environ.get(key)
    return float(value) if value not in (None, "") else default


def _str(key: str, default: str) -> str:
    value = os.environ.get(key)
    return value if value not in (None, "") else default


def _path(key: str, default: Path) -> Path:
    value = os.environ.get(key)
    return Path(value) if value else default


def _int(key: str, default: int) -> int:
    value = os.environ.get(key)
    return default if value is None or value == "" else int(value)


def _opt_int(key: str, *, default: int | None = None) -> int | None:
    value = os.environ.get(key)
    return int(value) if value else default


def _float(key: str, default: float) -> float:
    value = os.environ.get(key)
    return default if value is None or value == "" else float(value)


def _bool(key: str, default: bool) -> bool:
    value = os.environ.get(key)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
