"""Minimal v2 settings backed by the same root `.env` keys."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class V2Settings:
    catalog: str = "fintech_pod"
    database: str = "analytics"
    schema: str = "fintech_schema"
    sql_dialect: str = "duckdb"
    agent_model_profile: str = "anthropic_haiku_45"
    agent_llm_provider: str = "anthropic"
    agent_llm_model: str = "claude-haiku-4-5-20251001"
    agent_llm_max_tokens: int = 8192
    agent_llm_temperature: float = 0.0
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    openai_api_key: str | None = None
    google_api_key: str | None = None
    bedrock_api_key: str | None = None
    bedrock_region: str | None = None
    aws_region: str = "us-east-1"
    agent_sql_max_rows: int = 100
    agent_column_values_max_values: int = 100
    agent_todo_planning_enabled: bool = True
    agent_recursion_limit: int = 20
    primitive_max_iterations: int = 8
    primitive_subagent_max_iterations: int = 6
    primitive_max_tool_result_chars: int = 12000
    primitive_workflow_mode: str = "gated"
    context_compiler_mode: str = "agentic"
    context_compiler_model_profile: str = "anthropic_haiku_45"
    context_compiler_max_cards: int = 24
    context_compiler_max_patterns: int = 6
    data_root: Path = Path("v2/data")
    metadata_descriptions_path: Path = Path("v2/context/metadata_descriptions.json")
    schema_ast_path: Path = Path("v2/learning/artifacts/fintech_schema_ast_v2_20260609/schema_ast.json")
    sql_library_path: Path = Path("v2/learning/artifacts/fintech_sql_library_v2_20260609/sql_library.json")
    semantic_catalog_path: Path | None = None
    retrieval_documents_path: Path | None = None
    column_embeddings_path: Path | None = None
    nl_sql_pair_paths: tuple[Path, ...] = ()
    nl_sql_pair_limit: int | None = None
    nl_sql_pair_review_status: str = "approved"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_local_files_only: bool = True


def settings_from_env(env_file: str | Path | None = ".env") -> V2Settings:
    if env_file is not None:
        load_dotenv(env_file)
    return V2Settings(
        catalog=os.environ.get("DIRACDATA_CATALOG", "fintech_pod"),
        database=os.environ.get("DIRACDATA_DATABASE", "analytics"),
        schema=os.environ.get("DIRACDATA_SCHEMA", "fintech_schema"),
        sql_dialect=os.environ.get("DIRACDATA_SQL_DIALECT", "duckdb"),
        agent_model_profile=os.environ.get("DIRACDATA_AGENT_MODEL_PROFILE", "anthropic_haiku_45"),
        agent_llm_provider=os.environ.get("DIRACDATA_AGENT_LLM_PROVIDER", "anthropic"),
        agent_llm_model=os.environ.get("DIRACDATA_AGENT_LLM_MODEL", "claude-haiku-4-5-20251001"),
        agent_llm_max_tokens=_int_env("DIRACDATA_AGENT_LLM_MAX_TOKENS", 8192),
        agent_llm_temperature=_float_env("DIRACDATA_AGENT_LLM_TEMPERATURE", 0.0),
        anthropic_api_key=os.environ.get("DIRACDATA_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"),
        anthropic_base_url=os.environ.get("DIRACDATA_ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        openai_api_key=os.environ.get("DIRACDATA_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        google_api_key=(
            os.environ.get("DIRACDATA_GOOGLE_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        ),
        bedrock_api_key=(
            os.environ.get("DIRACDATA_BEDROCK_API_KEY")
            or os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        ),
        bedrock_region=os.environ.get("DIRACDATA_BEDROCK_REGION") or os.environ.get("AWS_REGION"),
        aws_region=os.environ.get("DIRACDATA_AWS_REGION", os.environ.get("AWS_REGION", "us-east-1")),
        agent_sql_max_rows=_int_env("DIRACDATA_AGENT_SQL_MAX_ROWS", 100),
        agent_column_values_max_values=_int_env("DIRACDATA_AGENT_COLUMN_VALUES_MAX_VALUES", 100),
        agent_todo_planning_enabled=_bool_env("DIRACDATA_AGENT_TODO_PLANNING_ENABLED", True),
        agent_recursion_limit=_int_env("DIRACDATA_AGENT_RECURSION_LIMIT", 20),
        primitive_max_iterations=_int_env("DIRACDATA_PRIMITIVE_MAX_ITERATIONS", 8),
        primitive_subagent_max_iterations=_int_env("DIRACDATA_PRIMITIVE_SUBAGENT_MAX_ITERATIONS", 6),
        primitive_max_tool_result_chars=_int_env("DIRACDATA_PRIMITIVE_MAX_TOOL_RESULT_CHARS", 12000),
        primitive_workflow_mode=os.environ.get("DIRACDATA_PRIMITIVE_WORKFLOW_MODE", "gated"),
        context_compiler_mode=os.environ.get("DIRACDATA_CONTEXT_COMPILER_MODE", "agentic"),
        context_compiler_model_profile=os.environ.get(
            "DIRACDATA_CONTEXT_COMPILER_MODEL_PROFILE",
            "anthropic_haiku_45",
        ),
        context_compiler_max_cards=_int_env("DIRACDATA_CONTEXT_COMPILER_MAX_CARDS", 24),
        context_compiler_max_patterns=_int_env("DIRACDATA_CONTEXT_COMPILER_MAX_PATTERNS", 6),
        data_root=Path(os.environ.get("DIRACDATA_V2_DATA_ROOT", "v2/data")),
        metadata_descriptions_path=Path(
            os.environ.get(
                "DIRACDATA_V2_METADATA_DESCRIPTIONS_PATH",
                "v2/context/metadata_descriptions.json",
            )
        ),
        schema_ast_path=Path(
            os.environ.get(
                "DIRACDATA_V2_SCHEMA_AST_PATH",
                "v2/learning/artifacts/fintech_schema_ast_v2_20260609/schema_ast.json",
            )
        ),
        sql_library_path=Path(
            os.environ.get(
                "DIRACDATA_V2_SQL_LIBRARY_PATH",
                "v2/learning/artifacts/fintech_sql_library_v2_20260609/sql_library.json",
            )
        ),
        semantic_catalog_path=_optional_path_env("DIRACDATA_V2_SEMANTIC_CATALOG_PATH"),
        retrieval_documents_path=_optional_path_env("DIRACDATA_V2_RETRIEVAL_DOCUMENTS_PATH"),
        column_embeddings_path=_optional_path_env("DIRACDATA_V2_COLUMN_EMBEDDINGS_PATH"),
        nl_sql_pair_paths=_path_tuple_env("DIRACDATA_V2_NL_SQL_PAIR_PATHS"),
        nl_sql_pair_limit=_optional_int_env("DIRACDATA_V2_NL_SQL_PAIR_LIMIT"),
        nl_sql_pair_review_status=os.environ.get("DIRACDATA_V2_NL_SQL_PAIR_REVIEW_STATUS", "approved"),
        embedding_model=os.environ.get("DIRACDATA_V2_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
        embedding_local_files_only=_bool_env("DIRACDATA_V2_EMBEDDING_LOCAL_FILES_ONLY", True),
    )


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


def _int_env(key: str, default: int) -> int:
    value = os.environ.get(key)
    return default if value is None or value == "" else int(value)


def _optional_int_env(key: str) -> int | None:
    value = os.environ.get(key)
    return int(value) if value else None


def _float_env(key: str, default: float) -> float:
    value = os.environ.get(key)
    return default if value is None or value == "" else float(value)


def _bool_env(key: str, default: bool) -> bool:
    value = os.environ.get(key)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _optional_path_env(key: str) -> Path | None:
    value = os.environ.get(key)
    return Path(value) if value else None


def _path_tuple_env(key: str) -> tuple[Path, ...]:
    value = os.environ.get(key)
    if not value:
        return ()
    return tuple(Path(item.strip()) for item in value.split(",") if item.strip())
