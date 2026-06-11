import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import settings_from_env


class SettingsTest(unittest.TestCase):
    def test_settings_from_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "DIRACDATA_MODE=dev",
                        "DIRACDATA_QUERY_ENGINE=duckdb",
                        "DIRACDATA_SQL_DIALECT=duckdb",
                        "DIRACDATA_CATALOG=commerce_pod",
                        "DIRACDATA_DATABASE=analytics",
                        "DIRACDATA_SCHEMA=main",
                        "DIRACDATA_CATALOG_CONFIG=/tmp/catalog.json",
                        "DIRACDATA_DUCKDB_DATABASE=:memory:",
                        "DIRACDATA_LEARNING_SAMPLE_LIMIT=10",
                        "DIRACDATA_LEARNING_DISTINCT_LIMIT=11",
                        "DIRACDATA_LEARNING_TOP_VALUES_LIMIT=12",
                        "DIRACDATA_LEARNING_CONTEXT_DISTINCT_VALUES_LIMIT=13",
                        "DIRACDATA_LEARNING_DESCRIPTION_COLUMN_BATCH_SIZE=14",
                        "DIRACDATA_LEARNING_ARTIFACT_STRATEGY=agentic",
                        "DIRACDATA_LEARNING_CONTEXT_MODE=schema_ast",
                        "DIRACDATA_LEARNING_AGENTIC_QUERY_HISTORY_LIMIT=77",
                        "DIRACDATA_LEARNING_AGENTIC_MAX_COLUMNS=425",
                        "DIRACDATA_LEARNING_AGENTIC_REPAIR_ATTEMPTS=2",
                        "DIRACDATA_LEARNING_VECTOR_INDEX_BACKEND=faiss",
                        "DIRACDATA_LEARNING_VECTOR_INDEX_ALGORITHM=hnsw_flat",
                        "DIRACDATA_LEARNING_VECTOR_INDEX_METRIC=inner_product",
                        "DIRACDATA_LEARNING_FAISS_HNSW_M=31",
                        "DIRACDATA_LEARNING_FAISS_EF_CONSTRUCTION=199",
                        "DIRACDATA_LEARNING_RUN_ID=test_run",
                        "DIRACDATA_JOIN_HISTORY_LLM_BATCH_SIZE=15",
                        "DIRACDATA_JOIN_NAME_SIMILARITY_MIN=0.61",
                        "DIRACDATA_JOIN_VALUE_OVERLAP_MIN=0.07",
                        "DIRACDATA_JOIN_MIN_SCORE=0.52",
                        "DIRACDATA_JOIN_SAMPLE_MATCH_MIN=3",
                        "DIRACDATA_JOIN_KEY_UNIQUE_TOLERANCE=0.03",
                        "DIRACDATA_LLM_MODEL_PROFILE=bedrock_anthropic_sonnet_46_ap_south_1",
                        "DIRACDATA_LLM_PROVIDER=anthropic",
                        "DIRACDATA_LLM_MODEL=claude-sonnet-4-6",
                        "DIRACDATA_LLM_MAX_TOKENS=512",
                        "DIRACDATA_LLM_TEMPERATURE=0.25",
                        "DIRACDATA_ANTHROPIC_BASE_URL=https://api.anthropic.com",
                        "DIRACDATA_ANTHROPIC_API_KEY=test-key",
                        "DIRACDATA_GOOGLE_API_KEY=google-test-key",
                        "DIRACDATA_OPENAI_API_KEY=openai-test-key",
                        "DIRACDATA_AGENT_LLM_PROVIDER=anthropic",
                        "DIRACDATA_AGENT_LLM_MODEL=claude-sonnet-4-6",
                        "DIRACDATA_AGENT_MODEL_PROFILE=anthropic_sonnet_46",
                        "DIRACDATA_AGENT_LLM_MAX_TOKENS=513",
                        "DIRACDATA_AGENT_LLM_TEMPERATURE=0.15",
                        "DIRACDATA_AGENT_STREAMING=on",
                        "DIRACDATA_AGENT_STREAM_MODES=updates,messages,custom",
                        "DIRACDATA_AGENT_STREAM_VERSION=v2",
                        "DIRACDATA_AGENT_CHECKPOINTER=memory",
                        "DIRACDATA_AGENT_STORE=memory",
                        "DIRACDATA_AGENT_SCHEMA_SEARCH_LIMIT=16",
                        "DIRACDATA_AGENT_INLINE_SCHEMA_CONTEXT=true",
                        "DIRACDATA_AGENT_BUSINESS_SEARCH_LIMIT=21",
                        "DIRACDATA_AGENT_CONTEXT_CONTRACT_ENABLED=false",
                        "DIRACDATA_AGENT_CONTEXT_CONTRACT_PATTERN_LIMIT=3",
                        "DIRACDATA_AGENT_CONTEXT_CONTRACT_INVARIANT_LIMIT=7",
                        "DIRACDATA_AGENT_CANDIDATE_SEARCH_ENABLED=false",
                        "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_ENABLED=true",
                        "DIRACDATA_AGENT_CANDIDATE_SEARCH_LIMIT=22",
                        "DIRACDATA_AGENT_CANDIDATE_SEARCH_PER_QUERY_LIMIT=23",
                        "DIRACDATA_AGENT_CANDIDATE_SEARCH_MAX_QUERIES=24",
                        "DIRACDATA_AGENT_CANDIDATE_SEARCH_MODEL_PROFILE=anthropic_haiku_45",
                        "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_PROVIDER=anthropic",
                        "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_MODEL=claude-haiku-4-5-20251001",
                        "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_MAX_TOKENS=515",
                        "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_TEMPERATURE=0.04",
                        "DIRACDATA_AGENT_PROFILE_VALUES_LIMIT=17",
                        "DIRACDATA_AGENT_JOIN_RECOVERY_ENABLED=false",
                        "DIRACDATA_AGENT_JOIN_RECOVERY_CANDIDATE_LIMIT=20",
                        "DIRACDATA_AGENT_SQL_MAX_ROWS=18",
                        "DIRACDATA_AGENT_SQL_TIMEOUT_SECONDS=19",
                        "DIRACDATA_AGENT_REFLECTION_ENABLED=true",
                        "DIRACDATA_AGENT_REFLECTION_MODEL_PROFILE=anthropic_haiku_45",
                        "DIRACDATA_AGENT_REFLECTION_LLM_PROVIDER=anthropic",
                        "DIRACDATA_AGENT_REFLECTION_LLM_MODEL=claude-haiku-4-5-20251001",
                        "DIRACDATA_AGENT_REFLECTION_LLM_MAX_TOKENS=514",
                        "DIRACDATA_AGENT_REFLECTION_LLM_TEMPERATURE=0.05",
                        "DIRACDATA_AGENT_REFLECTION_MAX_RETRIES=2",
                        "DIRACDATA_OBJECT_STORE=local",
                        "DIRACDATA_ARTIFACT_BUCKET=test-artifacts",
                        "DIRACDATA_LAKE_BUCKET=test-lake",
                        "DIRACDATA_BEDROCK_REGION=ap-south-1",
                        "DIRACDATA_BEDROCK_API_KEY=bedrock-test-key",
                        "DIRACDATA_LOCAL_ARTIFACT_ROOT=/tmp/diracdata-test-artifacts",
                    ]
                ),
                encoding="utf-8",
            )

            old_values = {
                key: os.environ.pop(key, None)
                for key in [
                    "DIRACDATA_MODE",
                    "DIRACDATA_QUERY_ENGINE",
                    "DIRACDATA_SQL_DIALECT",
                    "DIRACDATA_CATALOG",
                    "DIRACDATA_DATABASE",
                    "DIRACDATA_SCHEMA",
                    "DIRACDATA_CATALOG_CONFIG",
                    "DIRACDATA_DUCKDB_DATABASE",
                    "DIRACDATA_LEARNING_SAMPLE_LIMIT",
                    "DIRACDATA_LEARNING_DISTINCT_LIMIT",
                    "DIRACDATA_LEARNING_TOP_VALUES_LIMIT",
                    "DIRACDATA_LEARNING_CONTEXT_DISTINCT_VALUES_LIMIT",
                    "DIRACDATA_LEARNING_DESCRIPTION_COLUMN_BATCH_SIZE",
                    "DIRACDATA_LEARNING_ARTIFACT_STRATEGY",
                    "DIRACDATA_LEARNING_CONTEXT_MODE",
                    "DIRACDATA_LEARNING_AGENTIC_QUERY_HISTORY_LIMIT",
                    "DIRACDATA_LEARNING_AGENTIC_MAX_COLUMNS",
                    "DIRACDATA_LEARNING_AGENTIC_REPAIR_ATTEMPTS",
                    "DIRACDATA_LEARNING_VECTOR_INDEX_BACKEND",
                    "DIRACDATA_LEARNING_VECTOR_INDEX_ALGORITHM",
                    "DIRACDATA_LEARNING_VECTOR_INDEX_METRIC",
                    "DIRACDATA_LEARNING_FAISS_HNSW_M",
                    "DIRACDATA_LEARNING_FAISS_EF_CONSTRUCTION",
                    "DIRACDATA_LEARNING_RUN_ID",
                    "DIRACDATA_JOIN_HISTORY_LLM_BATCH_SIZE",
                    "DIRACDATA_JOIN_NAME_SIMILARITY_MIN",
                    "DIRACDATA_JOIN_VALUE_OVERLAP_MIN",
                    "DIRACDATA_JOIN_MIN_SCORE",
                    "DIRACDATA_JOIN_SAMPLE_MATCH_MIN",
                    "DIRACDATA_JOIN_KEY_UNIQUE_TOLERANCE",
                    "DIRACDATA_LLM_MODEL_PROFILE",
                    "DIRACDATA_LLM_PROVIDER",
                    "DIRACDATA_LLM_MODEL",
                    "DIRACDATA_LLM_MAX_TOKENS",
                    "DIRACDATA_LLM_TEMPERATURE",
                    "DIRACDATA_ANTHROPIC_BASE_URL",
                    "DIRACDATA_ANTHROPIC_API_KEY",
                    "DIRACDATA_GOOGLE_API_KEY",
                    "GOOGLE_API_KEY",
                    "GEMINI_API_KEY",
                    "DIRACDATA_OPENAI_API_KEY",
                    "OPENAI_API_KEY",
                    "DIRACDATA_AGENT_LLM_PROVIDER",
                    "DIRACDATA_AGENT_LLM_MODEL",
                    "DIRACDATA_AGENT_MODEL_PROFILE",
                    "DIRACDATA_AGENT_LLM_MAX_TOKENS",
                    "DIRACDATA_AGENT_LLM_TEMPERATURE",
                    "DIRACDATA_AGENT_STREAMING",
                    "DIRACDATA_AGENT_STREAM_MODES",
                    "DIRACDATA_AGENT_STREAM_VERSION",
                    "DIRACDATA_AGENT_CHECKPOINTER",
                    "DIRACDATA_AGENT_STORE",
                    "DIRACDATA_AGENT_SCHEMA_SEARCH_LIMIT",
                    "DIRACDATA_AGENT_INLINE_SCHEMA_CONTEXT",
                    "DIRACDATA_AGENT_BUSINESS_SEARCH_LIMIT",
                    "DIRACDATA_AGENT_CONTEXT_CONTRACT_ENABLED",
                    "DIRACDATA_AGENT_CONTEXT_CONTRACT_PATTERN_LIMIT",
                    "DIRACDATA_AGENT_CONTEXT_CONTRACT_INVARIANT_LIMIT",
                    "DIRACDATA_AGENT_CANDIDATE_SEARCH_ENABLED",
                    "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_ENABLED",
                    "DIRACDATA_AGENT_CANDIDATE_SEARCH_LIMIT",
                    "DIRACDATA_AGENT_CANDIDATE_SEARCH_PER_QUERY_LIMIT",
                    "DIRACDATA_AGENT_CANDIDATE_SEARCH_MAX_QUERIES",
                    "DIRACDATA_AGENT_CANDIDATE_SEARCH_MODEL_PROFILE",
                    "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_PROVIDER",
                    "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_MODEL",
                    "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_MAX_TOKENS",
                    "DIRACDATA_AGENT_CANDIDATE_SEARCH_LLM_TEMPERATURE",
                    "DIRACDATA_AGENT_PROFILE_VALUES_LIMIT",
                    "DIRACDATA_AGENT_JOIN_RECOVERY_ENABLED",
                    "DIRACDATA_AGENT_JOIN_RECOVERY_CANDIDATE_LIMIT",
                    "DIRACDATA_AGENT_SQL_MAX_ROWS",
                    "DIRACDATA_AGENT_SQL_TIMEOUT_SECONDS",
                    "DIRACDATA_AGENT_REFLECTION_ENABLED",
                    "DIRACDATA_AGENT_REFLECTION_MODEL_PROFILE",
                    "DIRACDATA_AGENT_REFLECTION_LLM_PROVIDER",
                    "DIRACDATA_AGENT_REFLECTION_LLM_MODEL",
                    "DIRACDATA_AGENT_REFLECTION_LLM_MAX_TOKENS",
                    "DIRACDATA_AGENT_REFLECTION_LLM_TEMPERATURE",
                    "DIRACDATA_AGENT_REFLECTION_MAX_RETRIES",
                    "DIRACDATA_OBJECT_STORE",
                    "DIRACDATA_ARTIFACT_BUCKET",
                    "DIRACDATA_LAKE_BUCKET",
                    "DIRACDATA_BEDROCK_REGION",
                    "DIRACDATA_BEDROCK_API_KEY",
                    "DIRACDATA_LOCAL_ARTIFACT_ROOT",
                ]
            }
            try:
                settings = settings_from_env(env_path)
            finally:
                for key, value in old_values.items():
                    if value is not None:
                        os.environ[key] = value
                    else:
                        os.environ.pop(key, None)

        self.assertEqual(settings.mode, "dev")
        self.assertEqual(settings.query_engine, "duckdb")
        self.assertEqual(settings.sql_dialect, "duckdb")
        self.assertEqual(settings.catalog, "commerce_pod")
        self.assertEqual(settings.database, "analytics")
        self.assertEqual(settings.schema, "main")
        self.assertEqual(settings.catalog_config, Path("/tmp/catalog.json"))
        self.assertEqual(settings.duckdb_database, ":memory:")
        self.assertEqual(settings.learning_sample_limit, 10)
        self.assertEqual(settings.learning_distinct_limit, 11)
        self.assertEqual(settings.learning_top_values_limit, 12)
        self.assertEqual(settings.learning_context_distinct_values_limit, 13)
        self.assertEqual(settings.learning_description_column_batch_size, 14)
        self.assertEqual(settings.learning_artifact_strategy, "agentic")
        self.assertEqual(settings.learning_context_mode, "schema_ast")
        self.assertEqual(settings.learning_agentic_query_history_limit, 77)
        self.assertEqual(settings.learning_agentic_max_columns, 425)
        self.assertEqual(settings.learning_agentic_repair_attempts, 2)
        self.assertEqual(settings.learning_vector_index_backend, "faiss")
        self.assertEqual(settings.learning_vector_index_algorithm, "hnsw_flat")
        self.assertEqual(settings.learning_vector_index_metric, "inner_product")
        self.assertEqual(settings.learning_faiss_hnsw_m, 31)
        self.assertEqual(settings.learning_faiss_ef_construction, 199)
        self.assertEqual(settings.learning_run_id, "test_run")
        self.assertEqual(settings.join_history_llm_batch_size, 15)
        self.assertEqual(settings.join_name_similarity_min, 0.61)
        self.assertEqual(settings.join_value_overlap_min, 0.07)
        self.assertEqual(settings.join_min_score, 0.52)
        self.assertEqual(settings.join_sample_match_min, 3)
        self.assertEqual(settings.join_key_unique_tolerance, 0.03)
        self.assertEqual(settings.llm_model_profile, "bedrock_anthropic_sonnet_46_ap_south_1")
        self.assertEqual(settings.llm_provider, "anthropic")
        self.assertEqual(settings.llm_model, "claude-sonnet-4-6")
        self.assertEqual(settings.llm_max_tokens, 512)
        self.assertEqual(settings.llm_temperature, 0.25)
        self.assertEqual(settings.anthropic_base_url, "https://api.anthropic.com")
        self.assertEqual(settings.anthropic_api_key, "test-key")
        self.assertEqual(settings.google_api_key, "google-test-key")
        self.assertEqual(settings.openai_api_key, "openai-test-key")
        self.assertEqual(settings.agent_llm_provider, "anthropic")
        self.assertEqual(settings.agent_llm_model, "claude-sonnet-4-6")
        self.assertEqual(settings.agent_model_profile, "anthropic_sonnet_46")
        self.assertEqual(settings.agent_llm_max_tokens, 513)
        self.assertEqual(settings.agent_llm_temperature, 0.15)
        self.assertEqual(settings.agent_streaming, "on")
        self.assertEqual(settings.agent_stream_modes, "updates,messages,custom")
        self.assertEqual(settings.agent_stream_version, "v2")
        self.assertEqual(settings.agent_checkpointer, "memory")
        self.assertEqual(settings.agent_store, "memory")
        self.assertEqual(settings.agent_schema_search_limit, 16)
        self.assertTrue(settings.agent_inline_schema_context)
        self.assertEqual(settings.agent_business_search_limit, 21)
        self.assertFalse(settings.agent_context_contract_enabled)
        self.assertEqual(settings.agent_context_contract_pattern_limit, 3)
        self.assertEqual(settings.agent_context_contract_invariant_limit, 7)
        self.assertFalse(settings.agent_candidate_search_enabled)
        self.assertTrue(settings.agent_candidate_search_llm_enabled)
        self.assertEqual(settings.agent_candidate_search_limit, 22)
        self.assertEqual(settings.agent_candidate_search_per_query_limit, 23)
        self.assertEqual(settings.agent_candidate_search_max_queries, 24)
        self.assertEqual(settings.agent_candidate_search_model_profile, "anthropic_haiku_45")
        self.assertEqual(settings.agent_candidate_search_llm_provider, "anthropic")
        self.assertEqual(settings.agent_candidate_search_llm_model, "claude-haiku-4-5-20251001")
        self.assertEqual(settings.agent_candidate_search_llm_max_tokens, 515)
        self.assertEqual(settings.agent_candidate_search_llm_temperature, 0.04)
        self.assertEqual(settings.agent_profile_values_limit, 17)
        self.assertFalse(settings.agent_join_recovery_enabled)
        self.assertEqual(settings.agent_join_recovery_candidate_limit, 20)
        self.assertEqual(settings.agent_sql_max_rows, 18)
        self.assertEqual(settings.agent_sql_timeout_seconds, 19)
        self.assertTrue(settings.agent_reflection_enabled)
        self.assertEqual(settings.agent_reflection_model_profile, "anthropic_haiku_45")
        self.assertEqual(settings.agent_reflection_llm_provider, "anthropic")
        self.assertEqual(settings.agent_reflection_llm_model, "claude-haiku-4-5-20251001")
        self.assertEqual(settings.agent_reflection_llm_max_tokens, 514)
        self.assertEqual(settings.agent_reflection_llm_temperature, 0.05)
        self.assertEqual(settings.agent_reflection_max_retries, 2)
        self.assertEqual(settings.object_store, "local")
        self.assertEqual(settings.artifact_bucket, "test-artifacts")
        self.assertEqual(settings.lake_bucket, "test-lake")
        self.assertEqual(settings.bedrock_region, "ap-south-1")
        self.assertEqual(settings.bedrock_api_key, "bedrock-test-key")
        self.assertEqual(settings.local_artifact_root, Path("/tmp/diracdata-test-artifacts"))
