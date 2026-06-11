"""Smoke test the configured DiracData object store."""

from __future__ import annotations

from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import settings_from_env
from diracdata.storage import artifact_key, object_store_from_settings


def main() -> None:
    settings = settings_from_env(".env")
    store = object_store_from_settings(settings, create_bucket_if_missing=True)
    run_id = f"smoke-{uuid4().hex[:8]}"
    key = artifact_key(
        pod_id="tpcds_commerce",
        run_id=run_id,
        artifact_type="smoke",
        name="object_store.json",
    )
    payload = {
        "ok": True,
        "mode": settings.mode,
        "object_store": settings.object_store,
        "artifact_bucket": settings.artifact_bucket,
    }

    store.write_json(key, payload)
    loaded = store.read_json(key)
    keys = store.list_keys(f"pods/tpcds_commerce/runs/{run_id}")

    print(f"Wrote artifact: {key}")
    print(f"Read artifact: {loaded}")
    print(f"Listed keys: {keys}")


if __name__ == "__main__":
    main()

