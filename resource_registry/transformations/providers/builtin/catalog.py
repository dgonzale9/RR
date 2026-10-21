from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from resource_registry.transformations.providers import (
    ResolvedTransformationOperationIdentity,
    TransformationOperationProvider,
    TransformationProviderIdentity,
)
from resource_registry.transformations.providers.builtin.local_duckdb_tabular import (
    PROJECT_COLUMNS_IDENTITY,
    PROVIDER_IDENTITY,
    LocalDuckDbTabularProvider,
)
from resource_registry.transformations.providers.dependencies import (
    ProviderConstructor,
    ProviderDependencies,
    ProviderFactory,
)


@dataclass(frozen=True)
class BuiltinProviderCatalogEntry:
    """Declare and verify one exact source-controlled production provider."""

    identity: TransformationProviderIdentity
    operation_identities: tuple[ResolvedTransformationOperationIdentity, ...]
    provider_class: ProviderConstructor

    def create_provider(
        self,
        dependencies: ProviderDependencies,
    ) -> TransformationOperationProvider:
        """Construct and validate one fully injected provider instance."""
        provider = self.provider_class(dependencies=dependencies)
        actual_operations = tuple(
            sorted(operation.identity for operation in provider.capability_descriptor.operations),
        )
        if provider.provider_identity != self.identity or actual_operations != tuple(
            sorted(self.operation_identities),
        ):
            raise ValueError("Built-in provider catalog entry does not match its descriptor.")
        return provider


PRODUCTION_TRANSFORMATION_PROVIDER_ENTRIES = (
    BuiltinProviderCatalogEntry(
        identity=PROVIDER_IDENTITY,
        operation_identities=(PROJECT_COLUMNS_IDENTITY,),
        provider_class=LocalDuckDbTabularProvider,
    ),
)

PRODUCTION_TRANSFORMATION_PROVIDER_CATALOG: Mapping[
    TransformationProviderIdentity,
    ProviderFactory,
] = MappingProxyType(
    {entry.identity: entry.create_provider for entry in PRODUCTION_TRANSFORMATION_PROVIDER_ENTRIES},
)
