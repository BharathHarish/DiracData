"""Guardrails — disk budget check + query kill switch.

Phase 1: just the disk budget check. Query kill switch added in Phase 5 workload.
"""
from __future__ import annotations
from data_harness.common.config import Config
from data_harness.common.minio_client import total_bytes
from data_harness.common.logging import log


def check_disk_budget(cfg: Config, s3) -> bool:
    """Returns True if under budget, False if over (caller pauses)."""
    prefix = f"{cfg.root_prefix}/"
    tot = total_bytes(s3, cfg.bucket, prefix)
    limit_gb = float(cfg.get("guardrails.max_lab_disk_gb", 8))
    limit_bytes = int(limit_gb * 1024**3)
    if tot > limit_bytes:
        log.warn("guardrail.disk.exceeded", bytes=tot, limit_bytes=limit_bytes)
        return False
    return True
