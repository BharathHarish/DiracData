"""Query engine construction from environment settings."""

from __future__ import annotations

from diracdata.backends import catalog_resolver_from_settings
from diracdata.config.settings import DiracDataSettings
from diracdata.query_engines.base import QueryEngine
from diracdata.query_engines.duckdb import DuckDBQueryEngine


def query_engine_from_settings(settings: DiracDataSettings) -> QueryEngine:
    engine = settings.query_engine.lower()
    if engine == "duckdb":
        catalog = catalog_resolver_from_settings(settings)
        return DuckDBQueryEngine.from_catalog(settings, catalog)
    raise ValueError(f"Unsupported DIRACDATA_QUERY_ENGINE: {settings.query_engine}")

