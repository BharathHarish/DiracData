"""Thin boto3 wrapper — one client, one auth, no per-call ceremony."""
from __future__ import annotations
import io
import json
import boto3
from botocore.config import Config as BotoConfig
from data_harness.common.config import Config


def make_s3(cfg: Config):
    return boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint,
        aws_access_key_id=cfg.s3_key,
        aws_secret_access_key=cfg.s3_secret,
        config=BotoConfig(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
    )


def upload_bytes(s3, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)


def upload_json(s3, bucket: str, key: str, obj) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(obj, indent=2, default=str).encode("utf-8"),
                  ContentType="application/json")


def download_json(s3, bucket: str, key: str, default=None):
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
    except s3.exceptions.NoSuchKey:
        return default
    except Exception as e:
        # NoSuchKey via generic error on some MinIO versions
        if "NoSuchKey" in str(e) or "404" in str(e):
            return default
        raise
    return json.loads(resp["Body"].read().decode("utf-8"))


def total_bytes(s3, bucket: str, prefix: str) -> int:
    tot = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            tot += o["Size"]
    return tot


def count_objects(s3, bucket: str, prefix: str) -> int:
    n = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        n += len(page.get("Contents", []))
    return n
