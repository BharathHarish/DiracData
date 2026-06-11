"""Generate TPC-DS data with DuckDB and export each table as parquet."""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb


DEFAULT_SCALE_FACTOR = 1
DEFAULT_OUTPUT_DIR = Path("data/tpcds/parquet/sf1")
DEFAULT_DATABASE_PATH = Path("data/tpcds/tpcds_sf1.duckdb")


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def sql_string(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale-factor", type=float, default=DEFAULT_SCALE_FACTOR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--database-path", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate tables and overwrite existing parquet files.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.database_path.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(args.database_path))
    con.execute("LOAD tpcds")

    existing_tables = [row[0] for row in con.execute("SHOW TABLES").fetchall()]
    if args.force or not existing_tables:
        if existing_tables:
            for table in existing_tables:
                con.execute(f"DROP TABLE IF EXISTS {quote_identifier(table)}")

        print(f"Generating TPC-DS scale factor {args.scale_factor} into {args.database_path}")
        con.execute(f"CALL dsdgen(sf = {args.scale_factor})")

    tables = [row[0] for row in con.execute("SHOW TABLES").fetchall()]
    if not tables:
        raise RuntimeError("TPC-DS generation produced no tables")

    print(f"Exporting {len(tables)} tables to {args.output_dir}")
    for table in tables:
        output_path = args.output_dir / f"{table}.parquet"
        if output_path.exists() and not args.force:
            print(f"Skipping existing {output_path}")
            continue

        print(f"Writing {output_path}")
        con.execute(
            f"COPY {quote_identifier(table)} TO {sql_string(output_path)} "
            "(FORMAT parquet, COMPRESSION zstd)"
        )

    con.close()
    print("Done")


if __name__ == "__main__":
    main()

