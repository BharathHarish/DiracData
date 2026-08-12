"""One-line DuckDB connection factory with httpfs + S3 secret to MinIO."""
from __future__ import annotations
import duckdb
from data_harness.common.config import Config


def make_duckdb(cfg: Config) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    # DuckDB secret — points at MinIO
    endpoint = cfg.s3_endpoint.replace("http://", "").replace("https://", "")
    use_ssl = "true" if cfg.s3_endpoint.startswith("https") else "false"
    con.execute(f"""
        CREATE OR REPLACE SECRET labs_s3 (
            TYPE S3,
            KEY_ID '{cfg.s3_key}',
            SECRET '{cfg.s3_secret}',
            ENDPOINT '{endpoint}',
            URL_STYLE 'path',
            USE_SSL {use_ssl},
            REGION 'us-east-1'
        )
    """)
    con.execute("SET enable_object_cache = true")
    return con
