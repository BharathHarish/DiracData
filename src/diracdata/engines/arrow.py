"""Canonicalize an Arrow table at the connector boundary, so every engine's result lands the same way
in parquet + the DuckDB reconciler.

The only transform needed in practice: unwrap Arrow **extension types** (e.g. Postgres `jsonb` arrives
as the `arrow.json` extension) to their **storage type** -- canonical JSON *text* -- which DuckDB reads
as VARCHAR and can re-parse with `json_extract`. Native `list`/`struct`/`timestamp[tz]` pass through
unchanged (parquet + DuckDB handle them directly).
"""

from __future__ import annotations

from typing import Any


def canonicalize(table: Any) -> Any:
    import pyarrow as pa

    if not any(isinstance(f.type, pa.ExtensionType) for f in table.schema):
        return table
    arrays, fields = [], []
    for field, col in zip(table.schema, table.columns):
        if isinstance(field.type, pa.ExtensionType):
            storage = field.type.storage_type
            col = (pa.chunked_array([c.storage for c in col.chunks], type=storage)
                   if col.num_chunks else pa.chunked_array([], type=storage))
            field = pa.field(field.name, storage, nullable=field.nullable)
        arrays.append(col)
        fields.append(field)
    return pa.Table.from_arrays(arrays, schema=pa.schema(fields))
