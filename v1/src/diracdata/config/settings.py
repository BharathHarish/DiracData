"""Environment-backed settings for DiracData."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiracDataSettings:
    """Runtime settings for local dev, MinIO, and AWS S3 deployments."""

    mode: str = "dev"
    query_engine: str = "duckdb"
    sql_dialect: str = "duckdb"
    catalog: str = "default"
    database: str = "main"
    schema: str = "main"
    catalog_config: Path | None = None
    duckdb_database: str = ":memory:"
    learning_sample_limit: int = 1000
    learning_distinct_limit: int = 1000
    learning_top_values_limit: int = 20
    learning_context_distinct_values_limit: int = 50
    learning_description_column_batch_size: int = 50
    learning_artifact_strategy: str = "deterministic"
    learning_context_mode: str = "linear"
    learning_agentic_query_history_limit: int = 80
    learning_agentic_max_columns: int = 500
    learning_agentic_repair_attempts: int = 2
    learning_embedding_provider: str = "none"
    learning_embedding_model: str = "BAAI/bge-small-en-v1.5"
    learning_embedding_local_files_only: bool = False
    learning_vector_index_backend: str = "faiss"
    learning_vector_index_algorithm: str = "hnsw_flat"
    learning_vector_index_metric: str = "inner_product"
    learning_faiss_hnsw_m: int = 32
    learning_faiss_ef_construction: int = 200
    learning_bm25_k1: float = 1.2
    learning_bm25_b: float = 0.75
    learning_bm25_delta: float = 1.0
    learning_rrf_k: int = 60
    join_history_llm_batch_size: int = 50
    join_name_similarity_min: float = 0.55
    join_value_overlap_min: float = 0.05
    join_min_score: float = 0.45
    join_sample_match_min: int = 1
    join_key_unique_tolerance: float = 0.02
    learning_run_id: str = "dev_learning_run"
    llm_model_profile: str | None = None
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    llm_max_tokens: int = 8192
    llm_temperature: float = 0.0
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    openai_api_key: str | None = None
    agent_llm_provider: str = "anthropic"
    agent_llm_model: str = "claude-sonnet-4-6"
    agent_model_profile: str | None = None
    agent_llm_max_tokens: int = 8192
    agent_llm_temperature: float = 0.0
    agent_streaming: str = "off"
    agent_stream_modes: str = "updates,messages"
    agent_stream_version: str = "v2"
    agent_checkpointer: str = "memory"
    agent_store: str = "memory"
    agent_schema_search_limit: int = 10
    agent_inline_schema_context: bool = False
    agent_business_search_limit: int = 10
    agent_context_contract_enabled: bool = True
    agent_context_contract_pattern_limit: int = 2
    agent_context_contract_invariant_limit: int = 6
    agent_candidate_search_enabled: bool = True
    agent_candidate_search_llm_enabled: bool = False
    agent_candidate_search_limit: int = 20
    agent_candidate_search_per_query_limit: int = 30
    agent_candidate_search_max_queries: int = 12
    agent_candidate_search_model_profile: str | None = None
    agent_candidate_search_llm_provider: str | None = None
    agent_candidate_search_llm_model: str | None = None
    agent_candidate_search_llm_max_tokens: int = 2048
    agent_candidate_search_llm_temperature: float = 0.0
    agent_profile_values_limit: int = 25
    agent_join_recovery_enabled: bool = True
    agent_join_recovery_candidate_limit: int = 50
    agent_sql_max_rows: int = 100
    agent_sql_timeout_seconds: int = 30
    agent_compiler_max_probes: int = 6
    agent_compiler_probe_max_rows: int = 20
    agent_compiler_max_repairs: int = 1
    agent_reflection_enabled: bool = False
    agent_reflection_model_profile: str | None = None
    agent_reflection_llm_provider: str | None = None
    agent_reflection_llm_model: str | None = None
    agent_reflection_llm_max_tokens: int = 2048
    agent_reflection_llm_temperature: float = 0.0
    agent_reflection_max_retries: int = 1
    object_store: str = "local"
    artifact_bucket: str = "diracdata"
    lake_bucket: str = "lake"
    s3_endpoint_url: str | None = None
    aws_region: str = "us-east-1"
    bedrock_region: str | None = None
    bedrock_api_key: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    local_artifact_root: Path = Path(".diracdata/artifacts")

    @property
    def is_dev(self) -> bool:
        return self.mode.lower() == "dev"


def settings_from_env(env_file: str | Path | None = ".env") -> DiracDataSettings:
    """Build settings from process env, optionally loading a simple dotenv file first."""
    if env_file is not None:
        load_dotenv(env_file)

    llm_provider = os.environ.get("DIRACDATA_LLM_PROVIDER", "anthropic")
    llm_model = os.environ.get("DIRACDATA_LLM_MODEL", "claude-sonnet-4-6")
    llm_max_tokens = _int_env("DIRACDATA_LLM_MAX_TOKENS", 8192)
    llm_temperature = _float_env("DIRACDATA_LLM_TEMPERATURE", 0.0)

    return DiracDataSettings(
        mode=os.environ.get("DIRACDATA_MODE", "dev"),
        query_engine=os.environ.get("DIRACDATA_QUERY_ENGINE", "duckdb"),
        sql_dialect=os.environ.get("DIRACDATA_SQL_DIALECT", "duckdb"),
        catalog=os.environ.get("DIRACDATA_CATALOG", "default"),
        database=os.environ.get("DIRACDATA_DATABASE", "main"),
        schema=os.environ.get("DIRACDATA_SCHEMA", "main"),
        catalog_config=_optional_path(os.environ.get("DIRACDATA_CATALOG_CONFIG")),
        duckdb_database=os.environ.get("DIRACDATA_DUCKDB_DATABASE", ":memory:"),
        learning_sample_limit=_int_env("DIRACDATA_LEARNING_SAMPLE_LIMIT", 1000),
        learning_distinct_limit=_int_env("DIRACDATA_LEARNING_DISTINCT_LIMIT", 1000),
        learning_top_values_limit=_int_env("DIRACDATA_LEARNING_TOP_VALUES_LIMIT", 20),
        learning_context_distinct_values_limit=_int_env(
            "DIRACDATA_LEARNING_CONTEXT_DISTINCT_VALUES_LIMIT",
            50,
        ),
        learning_description_column_batch_size=_int_env(
            "DIRACDATA_LEARNING_DESCRIPTION_COLUMN_BATCH_SIZE",
            50,
        ),
        learning_artifact_strategy=os.environ.get(
            "DIRACDATA_LEARNING_ARTIFACT_STRATEGY",
            "deterministic",
        ),
        learning_context_mode=os.environ.get("DIRACDATA_LEARNING_CONTEXT_MODE", "linear"),
        learning_agentic_query_history_limit=_int_env(
            "DIRACDATA_LEARNING_AGENTIC_QUERY_HISTORY_LIMIT",
            80,
        ),
        learning_agentic_max_columns=_int_env("DIRACDATA_LEARNING_AGENTIC_MAX_COLUMNS", 500),
        learning_agentic_repair_attempts=_int_env(
            "DIRACDATA_LEARNING_AGENTIC_REPAIR_ATTEMPTS",
            2,
        ),
        learning_embedding_provider=os.environ.get("DIRACDATA_LEARNING_EMBEDDING_PROVIDER", "none"),
        learning_embedding_model=os.environ.get(
            "DIRACDATA_LEARNING_EMBEDDING_MODEL",
            "BAAI/bge-small-en-v1.5",
        ),
        learning_embedding_local_files_only=_bool_env(
            "DIRACDATA_LEARNING_EMBEDDING_LOCAL_FILES_ONLY",
            False,
        ),
        learning_vector_index_backend=os.environ.get(
            "DIRACDATA_LEARNING_VECTOR_INDEX_BACKEND",
            "faiss",
        ),
        learning_vector_index_algorithm=os.environ.get(
            "DIRACDATA_LEARNING_VECTOR_INDEX_ALGORITHM",
            "hnsw_flat",
        ),
        learning_vector_index_metric=os.environ.get(
            "DIRACDATA_LEARNING_VECTOR_INDEX_METRIC",
            "inner_product",
        ),
        learning_faiss_hnsw_m=_int_env("DIRACDATA_LEARNING_FAISS_HNSW_M", 32),
        learning_faiss_ef_construction=_int_env(
            "DIRACDATA_LEARNING_FAISS_EF_CONSTRUCTION",
            200,
        ),
        learning_bm25_k1=_float_env("DIRACDATA_LEARNING_BM25_K1", 1.2),
        learning_bm25_b=_float_env("DIRACDATA_LEARNING_BM25_B", 0.75),
        learning_bm25_delta=_float_env("DIRACDATA_LEARNING_BM25_DELTA", 1.0),
        learning_rrf_k=_int_env("DIRACDATA_LEARNING_RRF_K", 60),
        join_history_llm_batch_size=_int_env("DIRACDATA_JOIN_HISTORY_LLM_BATCH_SIZE", 50),
        join_name_similarity_min=_float_env("DIRACDATA_JOIN_NAME_SIMILARITY_MIN", 0.55),
        join_value_overlap_min=_float_env("DIRACDATA_JOIN_VALUE_OVERLAP_MIN", 0.05),
        join_min_score=_float_env("DIRACDATA_JOIN_MIN_SCORE", 0.45),
        join_sample_match_min=_int_env("DIRACDATA_JOIN_SAMPLE_MATCH_MIN", 1),
        join_key_unique_tolerance=_float_env("DIRACDATA_JOIN_KEY_UNIQUE_TOLERANCE", 0.02),
        learning_run_id=os.environ.get("DIRACDATA_LEARNING_RUN_ID", "dev_learning_run"),
        llm_model_profile=os.environ.get("DIRACDATA_LLM_MODEL_PROFILE") or None,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_max_tokens=llm_max_tokens,
        llm_temperature=llm_temperature,
        anthropic_base_url=os.environ.get("DIRACDATA_ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        anthropic_api_key=os.environ.get("DIRACDATA_ANTHROPIC_API_KEY") or None,
        google_api_key=os.environ.get("DIRACDATA_GOOGLE_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or None,
        openai_api_key=os.environ.get("DIRACDATA_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or None,
        agent_llm_provider=os.environ.get("DIRACDATA_AGENT_LLM_PROVIDER", llm_provider),
        agent_llm_model=os.environ.get("DIRACDATA_AGENT_LLM_MODEL", llm_model),
        agent_model_profile=os.environ.get("DIRACDATA_AGENT_MODEL_PROFILE") or None,
        agent_llm_max_tokens=_int_env("DIRACDATA_AGENT_LLM_MAX_TOKENS", llm_max_tokens),
        agent_llm_temperature=_float_env(
            "DIRACDATA_AGENT_LLM_TEMPERATURE",
            llm_temperature,
        ),
        agent_streaming=os.environ.get("DIRACDATA_AGENT_STREAMING", "off"),
        agent_stream_modes=os.environ.get("DIRACDATA_AGENT_STREAM_MODES", "updates,messages"),
        agent_stream_version=os.environ.get("DIRACDATA_AGENT_STREAM_VERSION", "v2"),
        agent_checkpointer=os.environ.get("DIRACDATA_AGENT_CHECKPOINTER", "memory"),
        agent_store=os.environ.get("DIRACDATA_AGENT_STORE", "memory"),
        agent_schema_search_limit=_int_env("DIRACDATA_AGENT_SCHEMA_SEARCH_LIMIT", 10),
        agent_inline_schema_context=_bool_env(
            "DIRACDATA_AGENT_INLINE_SCHEMA_CONTEXT",
            False,
        ),
        agent_business_search_limit=_int_env("DIRACDATA_AGENT_BUSINESS_SEARCH_LIMIT", 10),
        agent_context_contract_enabled=_bool_env(
            "DIRACDATA_AGENT_CONTEXT_CONTRACT_ENABLED",
            True,
        ),
        agent_context_contract_pattern_limit=_int_env(
            "DIRACDATA_AGENT_CONTEXT_CONTRACT_PATTERN_LIMIT",
            2,
        ),
        agent_context_contract_invariant_limit=_int_env(
            "DIRACDATA_AGENT_CONTEXT_CONTRACT_INVARIANT_LIMIT",
            6,
        ),
        agent_candidate_search_enabled=_bool_env(
            "DIRACDATA_AGENT_CANDIDATE_SEARCH_ENABLED",
            True,
        ),
        agent_candidate_search_llm_enabled=_bool_env(
            "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_ENABLED",
            False,
        ),
        agent_candidate_search_limit=_int_env("DIRACDATA_AGENT_CANDIDATE_SEARCH_LIMIT", 20),
        agent_candidate_search_per_query_limit=_int_env(
            "DIRACDATA_AGENT_CANDIDATE_SEARCH_PER_QUERY_LIMIT",
            30,
        ),
        agent_candidate_search_max_queries=_int_env(
            "DIRACDATA_AGENT_CANDIDATE_SEARCH_MAX_QUERIES",
            12,
        ),
        agent_candidate_search_model_profile=os.environ.get(
            "DIRACDATA_AGENT_CANDIDATE_SEARCH_MODEL_PROFILE"
        )
        or None,
        agent_candidate_search_llm_provider=os.environ.get(
            "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_PROVIDER"
        )
        or None,
        agent_candidate_search_llm_model=os.environ.get(
            "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_MODEL"
        )
        or None,
        agent_candidate_search_llm_max_tokens=_int_env(
            "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_MAX_TOKENS",
            2048,
        ),
        agent_candidate_search_llm_temperature=_float_env(
            "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_TEMPERATURE",
            0.0,
        ),
        agent_profile_values_limit=_int_env("DIRACDATA_AGENT_PROFILE_VALUES_LIMIT", 25),
        agent_join_recovery_enabled=_bool_env("DIRACDATA_AGENT_JOIN_RECOVERY_ENABLED", True),
        agent_join_recovery_candidate_limit=_int_env(
            "DIRACDATA_AGENT_JOIN_RECOVERY_CANDIDATE_LIMIT",
            50,
        ),
        agent_sql_max_rows=_int_env("DIRACDATA_AGENT_SQL_MAX_ROWS", 100),
        agent_sql_timeout_seconds=_int_env("DIRACDATA_AGENT_SQL_TIMEOUT_SECONDS", 30),
        agent_compiler_max_probes=_int_env("DIRACDATA_AGENT_COMPILER_MAX_PROBES", 6),
        agent_compiler_probe_max_rows=_int_env(
            "DIRACDATA_AGENT_COMPILER_PROBE_MAX_ROWS",
            20,
        ),
        agent_compiler_max_repairs=_int_env("DIRACDATA_AGENT_COMPILER_MAX_REPAIRS", 1),
        agent_reflection_enabled=_bool_env("DIRACDATA_AGENT_REFLECTION_ENABLED", False),
        agent_reflection_model_profile=os.environ.get("DIRACDATA_AGENT_REFLECTION_MODEL_PROFILE")
        or None,
        agent_reflection_llm_provider=os.environ.get("DIRACDATA_AGENT_REFLECTION_LLM_PROVIDER")
        or None,
        agent_reflection_llm_model=os.environ.get("DIRACDATA_AGENT_REFLECTION_LLM_MODEL") or None,
        agent_reflection_llm_max_tokens=_int_env(
            "DIRACDATA_AGENT_REFLECTION_LLM_MAX_TOKENS",
            2048,
        ),
        agent_reflection_llm_temperature=_float_env(
            "DIRACDATA_AGENT_REFLECTION_LLM_TEMPERATURE",
            0.0,
        ),
        agent_reflection_max_retries=_int_env("DIRACDATA_AGENT_REFLECTION_MAX_RETRIES", 1),
        object_store=os.environ.get("DIRACDATA_OBJECT_STORE", "local"),
        artifact_bucket=os.environ.get("DIRACDATA_ARTIFACT_BUCKET", "diracdata"),
        lake_bucket=os.environ.get("DIRACDATA_LAKE_BUCKET", "lake"),
        s3_endpoint_url=os.environ.get("DIRACDATA_S3_ENDPOINT_URL"),
        aws_region=os.environ.get("DIRACDATA_AWS_REGION", "us-east-1"),
        bedrock_region=os.environ.get("DIRACDATA_BEDROCK_REGION") or None,
        bedrock_api_key=os.environ.get("DIRACDATA_BEDROCK_API_KEY")
        or os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        or None,
        aws_access_key_id=os.environ.get("DIRACDATA_AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("DIRACDATA_AWS_SECRET_ACCESS_KEY"),
        local_artifact_root=Path(
            os.environ.get("DIRACDATA_LOCAL_ARTIFACT_ROOT", ".diracdata/artifacts")
        ),
    )


def load_dotenv(path: str | Path) -> None:
    """Load KEY=VALUE lines from a dotenv file without adding a dependency."""
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_quotes(value.strip())
        os.environ.setdefault(key, value)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _optional_path(value: str | None) -> Path | None:
    if value is None or value.strip() == "":
        return None
    return Path(value)


def _int_env(key: str, default: int) -> int:
    value = os.environ.get(key)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _float_env(key: str, default: float) -> float:
    value = os.environ.get(key)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _bool_env(key: str, default: bool) -> bool:
    value = os.environ.get(key)
    if value is None or value.strip() == "":
        return default
    clean = value.strip().lower()
    if clean in {"1", "true", "yes", "on"}:
        return True
    if clean in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key} must be a boolean value")
