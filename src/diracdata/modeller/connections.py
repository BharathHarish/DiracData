"""One-line DuckDB + MinIO connection factories for the modeller."""
from __future__ import annotations
import boto3
from botocore.config import Config as BotoConfig
import duckdb
from .config import ModellerConfig


def make_s3(cfg: ModellerConfig):
    return boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint,
        aws_access_key_id=cfg.s3_key,
        aws_secret_access_key=cfg.s3_secret,
        config=BotoConfig(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
    )


def make_duckdb(cfg: ModellerConfig) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    endpoint = cfg.s3_endpoint.replace("http://", "").replace("https://", "")
    use_ssl = "true" if cfg.s3_endpoint.startswith("https") else "false"
    con.execute(f"""
        CREATE OR REPLACE SECRET modeller_s3 (
            TYPE S3,
            KEY_ID '{cfg.s3_key}',
            SECRET '{cfg.s3_secret}',
            ENDPOINT '{endpoint}',
            URL_STYLE 'path',
            USE_SSL {use_ssl},
            REGION 'us-east-1'
        )
    """)
    con.execute(f"SET memory_limit = '{max(1, int(cfg.max_query_scan_gb))}GB'")
    con.execute("SET enable_object_cache = true")
    return con
