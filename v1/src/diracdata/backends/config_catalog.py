"""Config-file-backed catalog resolver."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from diracdata.backends.catalog import TableLocation
from diracdata.config.settings import DiracDataSettings


@dataclass(frozen=True)
class ConfigCatalogResolver:
    """Resolve tables from a JSON catalog config file."""

    catalog: str
    database: str
    schema: str
    tables: dict[str, TableLocation]

    @classmethod
    def from_file(cls, path: str | Path) -> "ConfigCatalogResolver":
        config_path = Path(path)
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        tables = {
            table["name"]: TableLocation(
                name=table["name"],
                path=table["path"],
                format=table.get("format", "parquet"),
                description=table.get("description"),
            )
            for table in raw.get("tables", [])
        }
        return cls(
            catalog=raw["catalog"],
            database=raw["database"],
            schema=raw["schema"],
            tables=tables,
        )

    def validate_settings(self, settings: DiracDataSettings) -> None:
        expected = {
            "catalog": settings.catalog,
            "database": settings.database,
            "schema": settings.schema,
        }
        actual = {
            "catalog": self.catalog,
            "database": self.database,
            "schema": self.schema,
        }
        if actual != expected:
            raise ValueError(f"Catalog config does not match settings: expected {expected}, got {actual}")

    def list_tables(self) -> list[str]:
        return sorted(self.tables)

    def get_table(self, table_name: str) -> TableLocation:
        try:
            return self.tables[table_name]
        except KeyError as exc:
            raise KeyError(f"Table not found in catalog {self.catalog}: {table_name}") from exc

    def table_locations(self) -> list[TableLocation]:
        return [self.tables[name] for name in self.list_tables()]


def catalog_resolver_from_settings(settings: DiracDataSettings) -> ConfigCatalogResolver:
    if settings.catalog_config is None:
        raise ValueError("DIRACDATA_CATALOG_CONFIG is required for config-backed catalogs")

    resolver = ConfigCatalogResolver.from_file(settings.catalog_config)
    resolver.validate_settings(settings)
    return resolver


def catalog_config_dict(resolver: ConfigCatalogResolver) -> dict[str, Any]:
    """Return a serializable view of a config resolver for debugging/tests."""
    return {
        "catalog": resolver.catalog,
        "database": resolver.database,
        "schema": resolver.schema,
        "tables": [
            {
                "name": table.name,
                "path": table.path,
                "format": table.format,
                "description": table.description,
            }
            for table in resolver.table_locations()
        ],
    }

