"""Artifact key helpers."""

from __future__ import annotations


def artifact_key(
    *,
    pod_id: str,
    run_id: str,
    artifact_type: str,
    name: str,
) -> str:
    """Build a stable object key for pod learning/answering artifacts."""
    parts = [
        "pods",
        _clean_part(pod_id),
        "runs",
        _clean_part(run_id),
        _clean_part(artifact_type),
        _clean_part(name),
    ]
    return "/".join(parts)


def _clean_part(value: str) -> str:
    clean = value.strip().strip("/")
    if not clean or clean in {".", ".."} or "/" in clean:
        raise ValueError(f"Invalid artifact path component: {value!r}")
    return clean

