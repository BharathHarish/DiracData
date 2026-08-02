"""`SourceRegistry` — the set of data sources the agent can reach. Each source is an `EngineSpec`
that lazily builds a `QueryEngine`. One place to declare sources; the agent depends on the registry,
not on any concrete engine.

Loaders arrive as they are needed: `from_config` (the back-compat single default source) now;
`from_env` / `from_yaml` (multi-source) in Phase 2. Secrets (DSNs) live in ENV, never in code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from diracdata.engines.base import QueryEngine


@dataclass(frozen=True)
class EngineSpec:
    name: str
    kind: str = "duckdb"            # duckdb | postgres | mysql | trino | ...
    data_root: Path | None = None   # duckdb: where the schema's parquet lives
    schema: str | None = None       # duckdb: schema/subdir name
    dsn: str | None = None          # external connectors (secret; sourced from ENV)
    read_only: bool = True
    timeout_s: float | None = None
    params: dict[str, Any] = field(default_factory=dict)


class SourceRegistry:
    def __init__(self, specs: list[EngineSpec], *, default: str | None = None) -> None:
        if not specs:
            raise ValueError("SourceRegistry needs at least one source")
        self._specs = {s.name: s for s in specs}
        self._default = default or specs[0].name
        self._built: dict[str, QueryEngine] = {}

    @classmethod
    def from_config(cls, config: Any) -> "SourceRegistry":
        """Back-compat: one source built from `Config` (today's single-engine behaviour)."""
        spec = EngineSpec(name=config.schema, kind=config.sql_engine,
                          data_root=Path(config.data_root), schema=config.schema)
        return cls([spec])

    @classmethod
    def of(cls, engine: QueryEngine) -> "SourceRegistry":
        """Wrap an already-built engine as a one-source registry (skeleton back-compat)."""
        reg = cls([EngineSpec(name=engine.name, kind=getattr(engine, "dialect", "duckdb"))])
        reg._built[engine.name] = engine
        return reg

    def names(self) -> list[str]:
        return list(self._specs)

    def spec(self, name: str) -> EngineSpec:
        return self._specs[name]

    def get(self, name: str | None = None) -> QueryEngine:
        key = name or self._default
        if key in self._built:
            return self._built[key]
        if key not in self._specs:
            raise KeyError(f"no such source: {key!r}; known: {self.names()}")
        self._built[key] = _build(self._specs[key])
        return self._built[key]

    def get_default(self) -> QueryEngine:
        return self.get(self._default)


def _build(spec: EngineSpec) -> QueryEngine:
    if spec.kind == "duckdb":
        from diracdata.engines.duckdb import DuckDBEngine
        if spec.data_root is None:
            raise ValueError(f"duckdb source {spec.name!r} needs data_root")
        return DuckDBEngine(data_root=Path(spec.data_root), schema_name=spec.schema or spec.name,
                            name=spec.name, read_only=spec.read_only)
    raise NotImplementedError(f"engine kind {spec.kind!r} not available yet (source {spec.name!r})")
