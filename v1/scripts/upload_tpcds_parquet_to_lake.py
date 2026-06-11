"""Upload local TPC-DS parquet files to the configured S3/MinIO lake bucket."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diracdata.config import settings_from_env
from diracdata.storage.s3 import S3ObjectStore


DEFAULT_DATA_DIR = Path("data/tpcds/parquet/sf1")
DEFAULT_PREFIX = "tpcds/sf1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = settings_from_env(".env")
    if settings.object_store.lower() not in {"s3", "minio"}:
        raise RuntimeError("TPC-DS lake upload requires DIRACDATA_OBJECT_STORE=s3 or minio")

    store = S3ObjectStore(
        settings.lake_bucket,
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        create_bucket_if_missing=True,
    )

    parquet_files = sorted(args.data_dir.glob("*.parquet"))
    if not parquet_files:
        raise FileNotFoundError(f"No parquet files found in {args.data_dir}")

    for parquet_file in parquet_files:
        key = f"{args.prefix.strip('/')}/{parquet_file.name}"
        store.write_bytes(key, parquet_file.read_bytes(), content_type="application/octet-stream")
        print(f"Uploaded s3://{settings.lake_bucket}/{key}")

    print(f"Uploaded {len(parquet_files)} parquet files to bucket {settings.lake_bucket}")


if __name__ == "__main__":
    main()

