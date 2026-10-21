"""Local deterministic DuckDB Parquet transformation provider."""

from resource_registry.transformations.providers.builtin.local_duckdb_tabular.capabilities import (
    PROJECT_COLUMNS_IDENTITY,
    PROVIDER_CAPABILITY_DESCRIPTOR,
    PROVIDER_IDENTITY,
)
from resource_registry.transformations.providers.builtin.local_duckdb_tabular.provider import (
    LocalDuckDbTabularProvider,
)

__all__ = [
    "PROJECT_COLUMNS_IDENTITY",
    "PROVIDER_CAPABILITY_DESCRIPTOR",
    "PROVIDER_IDENTITY",
    "LocalDuckDbTabularProvider",
]
