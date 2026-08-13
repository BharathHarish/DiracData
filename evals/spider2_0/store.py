"""SpiderStore — MinIO abstraction for Spider 2.0-Lite artifacts.

Layout (all under s3://<bucket>/spider2/):
  manifest.jsonl                         — 135 SQLite instance rows (subset of the full 547)
  sqlite/<db_id>.sqlite                  — SQLite database blobs (fetched on-demand)
  docs/<filename>.md                     — external_knowledge markdown docs
  gold/sql/<instance_id>.sql             — gold SQL (one per instance)
  gold/csv/<instance_id>_<variant>.csv   — gold result CSVs (a/b/c variants)
  gold/eval_index.jsonl                  — per-instance grader metadata
                                            (condition_cols, ignore_order, temporal, toks)
  predictions/<run_id>/<instance_id>.csv — our predictions per run
  results/<run_id>.json                  — grader output per run

Nothing lives in the repo. SQLite databases are cached locally on first fetch
into a scratch dir (gitignored) so subsequent runs skip the download.
"""
from __future__ import annotations
import io
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
import boto3
from botocore.config import Config as BotoConfig


# ---------- config (env-driven, no magic numbers in code) ----------

@dataclass(frozen=True)
class SpiderConfig:
    s3_endpoint: str = os.getenv("DIRACDATA_S3_ENDPOINT_URL", "http://localhost:9000")
    s3_key:      str = os.getenv("DIRACDATA_AWS_ACCESS_KEY_ID", "minioadmin")
    s3_secret:   str = os.getenv("DIRACDATA_AWS_SECRET_ACCESS_KEY", "minioadmin")
    bucket:      str = os.getenv("DIRACDATA_LAKE_BUCKET", "lake")
    root_prefix: str = os.getenv("DIRACDATA_SPIDER_PREFIX", "spider2")
    # Local SQLite cache — gitignored under evals/spider2.0/sqlite_cache/
    sqlite_cache_dir: str = os.getenv(
        "DIRACDATA_SPIDER_SQLITE_CACHE",
        str(Path(__file__).resolve().parent / "sqlite_cache"),
    )

    def key(self, *parts: str) -> str:
        return "/".join([self.root_prefix, *parts])


def _load_env(env_file: str = ".env") -> None:
    if os.path.exists(env_file):
        for ln in open(env_file):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln: continue
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def make_s3(cfg: SpiderConfig):
    return boto3.client(
        "s3", endpoint_url=cfg.s3_endpoint,
        aws_access_key_id=cfg.s3_key, aws_secret_access_key=cfg.s3_secret,
        config=BotoConfig(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
    )


# ---------- SpiderStore — the single seam over MinIO ----------

class SpiderStore:
    """Read + write access to Spider 2.0-Lite artifacts on MinIO.

    Callers never construct S3 URIs directly. Everything goes through named
    accessors so we can change the layout without changing consumers.
    """

    def __init__(self, cfg: Optional[SpiderConfig] = None):
        self.cfg = cfg or SpiderConfig()
        self.s3 = make_s3(self.cfg)

    # -------- manifest --------
    def list_instances(self, backend: str = "local") -> List[Dict]:
        """Return every instance for the given backend (default: local = SQLite)."""
        try:
            body = self.s3.get_object(
                Bucket=self.cfg.bucket, Key=self.cfg.key("manifest.jsonl"),
            )["Body"].read().decode()
        except Exception:
            return []
        rows = [json.loads(l) for l in body.splitlines() if l.strip()]
        return [r for r in rows if r.get("instance_id", "").startswith(backend)]

    def get_instance(self, instance_id: str) -> Optional[Dict]:
        for r in self.list_instances(backend=""):
            if r.get("instance_id") == instance_id:
                return r
        return None

    def get_eval_index(self, instance_id: str) -> Dict:
        """Grader metadata: {condition_cols, ignore_order, temporal, toks}."""
        try:
            body = self.s3.get_object(
                Bucket=self.cfg.bucket, Key=self.cfg.key("gold/eval_index.jsonl"),
            )["Body"].read().decode()
        except Exception:
            return {}
        for line in body.splitlines():
            row = json.loads(line)
            if row.get("instance_id") == instance_id:
                return row
        return {}

    # -------- SQLite databases (on-demand cache) --------
    def sqlite_local_path(self, db_id: str) -> Path:
        """Return a local Path to the SQLite file. Downloads from MinIO on first call.

        The file is cached under sqlite_cache_dir (gitignored). Subsequent calls
        return the cached path without re-downloading.
        """
        cache = Path(self.cfg.sqlite_cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        local = cache / f"{db_id}.sqlite"
        if local.exists() and local.stat().st_size > 0:
            return local
        key = self.cfg.key(f"sqlite/{db_id}.sqlite")
        tmp = cache / f".{db_id}.sqlite.part"
        try:
            self.s3.download_file(self.cfg.bucket, key, str(tmp))
            tmp.rename(local)
        except Exception as ex:
            if tmp.exists(): tmp.unlink()
            raise FileNotFoundError(
                f"SQLite for db_id={db_id} not in MinIO at s3://{self.cfg.bucket}/{key}. "
                f"Run bootstrap.py first. Underlying: {ex}"
            )
        return local

    def list_sqlite_dbs(self) -> List[str]:
        out = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.cfg.bucket, Prefix=self.cfg.key("sqlite/"),
        ):
            for o in page.get("Contents", []):
                name = o["Key"].split("/")[-1]
                if name.endswith(".sqlite"):
                    out.append(name[:-len(".sqlite")])
        return sorted(out)

    # -------- external knowledge docs --------
    def get_doc(self, filename: str) -> str:
        """Fetch one external_knowledge markdown file by filename."""
        # Try with and without .md extension
        for k in (f"docs/{filename}", f"docs/{filename}.md"):
            try:
                return self.s3.get_object(
                    Bucket=self.cfg.bucket, Key=self.cfg.key(k),
                )["Body"].read().decode("utf-8", errors="replace")
            except Exception:
                continue
        return ""

    def get_docs_for(self, instance_id: str) -> Dict[str, str]:
        """Return {filename: content} for every doc referenced in the instance's external_knowledge."""
        inst = self.get_instance(instance_id)
        if not inst: return {}
        refs = inst.get("external_knowledge") or []
        if isinstance(refs, str):
            refs = [r.strip() for r in refs.split(",") if r.strip()]
        return {name: self.get_doc(name) for name in refs}

    # -------- gold --------
    def get_gold_sql(self, instance_id: str) -> str:
        try:
            return self.s3.get_object(
                Bucket=self.cfg.bucket, Key=self.cfg.key(f"gold/sql/{instance_id}.sql"),
            )["Body"].read().decode()
        except Exception:
            return ""

    def get_gold_csvs(self, instance_id: str) -> Dict[str, bytes]:
        """Return {variant: csv_bytes} — Spider 2.0 provides variants (a/b/c/...)."""
        prefix = self.cfg.key(f"gold/csv/{instance_id}_")
        out = {}
        for page in self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.cfg.bucket, Prefix=prefix,
        ):
            for o in page.get("Contents", []):
                variant = o["Key"].split("/")[-1].replace(f"{instance_id}_", "").replace(".csv", "")
                obj = self.s3.get_object(Bucket=self.cfg.bucket, Key=o["Key"])
                out[variant] = obj["Body"].read()
        return out

    # -------- predictions --------
    def put_prediction(self, run_id: str, instance_id: str, csv_bytes: bytes) -> str:
        key = self.cfg.key(f"predictions/{run_id}/{instance_id}.csv")
        self.s3.put_object(
            Bucket=self.cfg.bucket, Key=key, Body=csv_bytes, ContentType="text/csv",
        )
        return key

    def list_predictions(self, run_id: str) -> List[str]:
        out = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.cfg.bucket, Prefix=self.cfg.key(f"predictions/{run_id}/"),
        ):
            for o in page.get("Contents", []):
                inst = o["Key"].split("/")[-1].removesuffix(".csv")
                out.append(inst)
        return sorted(out)

    def get_prediction(self, run_id: str, instance_id: str) -> Optional[bytes]:
        try:
            return self.s3.get_object(
                Bucket=self.cfg.bucket,
                Key=self.cfg.key(f"predictions/{run_id}/{instance_id}.csv"),
            )["Body"].read()
        except Exception:
            return None

    # -------- results --------
    def put_result(self, run_id: str, result: Dict) -> str:
        key = self.cfg.key(f"results/{run_id}.json")
        self.s3.put_object(
            Bucket=self.cfg.bucket, Key=key,
            Body=json.dumps(result, indent=2, default=str).encode(),
            ContentType="application/json",
        )
        return key

    # -------- footprint --------
    def footprint(self) -> Dict[str, Any]:
        per = {}
        for page in self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.cfg.bucket, Prefix=self.cfg.key(""),
        ):
            for o in page.get("Contents", []):
                parts = o["Key"].split("/")
                sub = "/".join(parts[1:3]) if len(parts) >= 3 else parts[1] if len(parts) >= 2 else parts[0]
                per.setdefault(sub, {"bytes": 0, "count": 0})
                per[sub]["bytes"] += o["Size"]; per[sub]["count"] += 1
        return per


__all__ = ["SpiderConfig", "SpiderStore", "make_s3", "_load_env"]
