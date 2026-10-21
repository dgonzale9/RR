from __future__ import annotations

from resource_registry.providers.base import ExternalDatasetProvider
from resource_registry.providers.exceptions import ProviderNotRegisteredError


class ProviderRegistry:
    """Dependency-injection-friendly provider lookup registry."""

    def __init__(self) -> None:
        self._providers: dict[str, ExternalDatasetProvider] = {}

    def register_provider(self, *, key: str, provider: ExternalDatasetProvider) -> None:
        normalized_key = key.strip().lower()
        self._providers[normalized_key] = provider

    def get_provider(self, *, key: str) -> ExternalDatasetProvider:
        normalized_key = key.strip().lower()
        try:
            return self._providers[normalized_key]
        except KeyError as error:
            raise ProviderNotRegisteredError(f"Provider '{key}' is not registered.") from error

    def list_providers(self) -> list[str]:
        return sorted(self._providers.keys())
