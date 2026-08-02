"""`SourceRegistry` — the set of data sources the agent can reach. Each source is an `EngineSpec`
that lazily builds a `QueryEngine`. One place to declare sources; the agent depends on the registry,
not on any concrete engine.

Loaders arrive as they are needed: `from_config` (the back-compat single default source) now;
`from_env` / `from_yaml` (multi-source) in Phase 2. Secrets (DSNs) live in ENV, never in code.
"""

from __future__ import annotations

import os
import re
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

    @classmethod
    def from_env(cls, env: Any = None) -> "SourceRegistry | None":
        """Build a multi-source registry from `DIRACDATA_SOURCES=a,b` + per-source `DIRACDATA_SOURCE_<A>_*`
        keys. Returns None when `DIRACDATA_SOURCES` is unset (caller falls back to `from_config`).
        Secrets (DSNs) come from ENV, never a literal here."""
        env = os.environ if env is None else env
        names = [n.strip() for n in env.get("DIRACDATA_SOURCES", "").split(",") if n.strip()]
        if not names:
            return None
        specs = [_spec_from_env(n, env) for n in names]
        return cls(specs, default=env.get("DIRACDATA_DEFAULT_SOURCE") or specs[0].name)

    @classmethod
    def from_yaml(cls, path: str | Path, env: Any = None) -> "SourceRegistry":
        """Build a registry from a YAML manifest. String fields interpolate `${VAR}` from ENV, so a
        DSN is `dsn: ${ORDERS_DSN}` -- the secret lives in ENV, not the file."""
        import yaml
        env = os.environ if env is None else env
        raw = yaml.safe_load(Path(path).read_text()) or {}
        specs = [_spec_from_dict(_interpolate(d, env)) for d in (raw.get("sources") or [])]
        return cls(specs, default=raw.get("default"))

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
    if spec.kind == "postgres":
        from diracdata.engines.postgres import PostgresEngine
        if not spec.dsn:
            raise ValueError(f"postgres source {spec.name!r} needs a dsn")
        return PostgresEngine(dsn=spec.dsn, name=spec.name, read_only=spec.read_only,
                              timeout_s=spec.timeout_s, schema=spec.params.get("schema", "public"))
    raise NotImplementedError(f"engine kind {spec.kind!r} not available yet (source {spec.name!r})")


def _spec_from_env(name: str, env: Any) -> EngineSpec:
    p = f"DIRACDATA_SOURCE_{name.upper()}_"
    kind = env.get(p + "KIND", "duckdb")
    common = dict(name=name, kind=kind, dsn=env.get(p + "DSN"),
                  read_only=_as_bool(env.get(p + "READ_ONLY"), True),
                  timeout_s=float(env[p + "TIMEOUT_S"]) if env.get(p + "TIMEOUT_S") else None)
    if kind == "postgres":
        return EngineSpec(**common, params={"schema": env.get(p + "SCHEMA", "public")})
    return EngineSpec(**common,
                      data_root=Path(env[p + "DATA_ROOT"]) if env.get(p + "DATA_ROOT") else None,
                      schema=env.get(p + "SCHEMA"))


def _spec_from_dict(d: dict) -> EngineSpec:
    return EngineSpec(
        name=d["name"], kind=d.get("kind", "duckdb"), dsn=d.get("dsn"),
        data_root=Path(d["data_root"]) if d.get("data_root") else None, schema=d.get("schema"),
        read_only=bool(d.get("read_only", True)),
        timeout_s=float(d["timeout_s"]) if d.get("timeout_s") is not None else None,
        params=d.get("params") or {})


def _interpolate(d: dict, env: Any) -> dict:
    def sub(v: Any) -> Any:
        return re.sub(r"\$\{([^}]+)\}", lambda m: env.get(m.group(1), ""), v) if isinstance(v, str) else v
    return {k: sub(v) for k, v in d.items()}


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
