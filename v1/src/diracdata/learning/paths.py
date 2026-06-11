"""Learning artifact key helpers."""

from __future__ import annotations

from diracdata.config.settings import DiracDataSettings


def learning_artifact_prefix(
    settings: DiracDataSettings,
    *,
    run_id: str,
) -> str:
    return f"{learning_scope_prefix(settings)}/{_clean(run_id)}"


def learning_scope_prefix(settings: DiracDataSettings) -> str:
    return "/".join(
        [
            "artifacts",
            "learning",
            _clean(settings.catalog),
            _clean(settings.database),
            _clean(settings.schema),
        ]
    )


def learning_artifact_key(
    settings: DiracDataSettings,
    *,
    run_id: str,
    relative_path: str,
) -> str:
    return f"{learning_artifact_prefix(settings, run_id=run_id)}/{_clean_relative(relative_path)}"


def active_learning_artifact_key(
    settings: DiracDataSettings,
    *,
    relative_path: str,
) -> str:
    return f"{learning_scope_prefix(settings)}/active/{_clean_relative(relative_path)}"


def _clean(value: str) -> str:
    clean = value.strip().strip("/")
    if not clean or clean in {".", ".."} or "/" in clean:
        raise ValueError(f"Invalid learning artifact path component: {value!r}")
    return clean


def _clean_relative(value: str) -> str:
    clean = value.strip().strip("/")
    if not clean or clean in {".", ".."} or ".." in clean.split("/"):
        raise ValueError(f"Invalid learning artifact relative path: {value!r}")
    return clean
