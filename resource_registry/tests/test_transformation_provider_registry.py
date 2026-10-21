from __future__ import annotations

from dataclasses import dataclass

import pytest

from resource_registry.tests.transformation_runtime_test_helpers import (
    freeze_test_provider_registry,
)
from resource_registry.transformations.providers import (
    ResolvedTransformationOperationIdentity,
    TransformationOperationCapabilityDescriptor,
    TransformationOperationProvider,
    TransformationProviderCapabilityDescriptor,
    TransformationProviderIdentity,
    TransformationProviderRegistry,
)
from resource_registry.transformations.providers.exceptions import (
    DuplicateOperationDescriptorError,
    DuplicateProviderRegistrationError,
    TransformationOperationNotFoundError,
    TransformationOperationVersionAmbiguousError,
    TransformationProviderNotFoundError,
    TransformationProviderVersionAmbiguousError,
)


@dataclass(frozen=True)
class _TestProvider:
    provider_identity: TransformationProviderIdentity
    capability_descriptor: TransformationProviderCapabilityDescriptor

    def validate_operation(self, *, request):  # pragma: no cover - registry never invokes it
        raise AssertionError("Registry must not invoke provider validation.")

    def execute(self, *, request, runtime_context):  # pragma: no cover - registry never invokes it
        raise AssertionError("Registry must not execute providers.")


def _provider(
    provider_key: str,
    provider_version: str,
    *operation_versions: str,
) -> _TestProvider:
    identity = TransformationProviderIdentity(provider_key, provider_version)
    operations = tuple(
        TransformationOperationCapabilityDescriptor(
            identity=ResolvedTransformationOperationIdentity(
                provider_key=provider_key,
                provider_version=provider_version,
                operation_key="transform",
                operation_version=operation_version,
            ),
            supported_input_formats=("PARQUET",),
            supported_output_formats=("PARQUET",),
            is_idempotent=True,
            is_checkpoint_reuse_safe=True,
            is_checkpoint_reconciliation_safe=False,
            supports_cooperative_cancellation=True,
        )
        for operation_version in operation_versions
    )
    return _TestProvider(
        provider_identity=identity,
        capability_descriptor=TransformationProviderCapabilityDescriptor(
            identity=identity,
            operations=operations,
        ),
    )


def test_exact_registration_and_four_part_resolution() -> None:
    registry = TransformationProviderRegistry()
    provider = _provider("Provider", "v1", "op1")

    assert isinstance(provider, TransformationOperationProvider)
    registry.register_provider(provider=provider)
    freeze_test_provider_registry(registry)
    resolved = registry.resolve_operation(
        provider_key="Provider",
        provider_version="v1",
        operation_key="transform",
        operation_version="op1",
    )

    assert resolved.provider is provider
    assert resolved.operation.identity.operation_version == "op1"
    with pytest.raises(TransformationProviderNotFoundError):
        registry.resolve_operation(
            provider_key="provider",
            provider_version="v1",
            operation_key="transform",
            operation_version="op1",
        )


def test_duplicate_provider_and_operation_descriptors_are_rejected() -> None:
    registry = TransformationProviderRegistry()
    provider = _provider("provider", "v1", "op1")
    registry.register_provider(provider=provider)

    with pytest.raises(DuplicateProviderRegistrationError):
        registry.register_provider(provider=provider)

    duplicate_operations = _provider("other", "v1", "op1", "op1")
    with pytest.raises(DuplicateOperationDescriptorError):
        registry.register_provider(provider=duplicate_operations)


def test_omitted_provider_version_requires_one_candidate() -> None:
    registry = TransformationProviderRegistry()
    registry.register_provider(provider=_provider("provider", "v1", "op1"))
    freeze_test_provider_registry(registry)

    resolved = registry.resolve_operation(
        provider_key="provider",
        operation_key="transform",
        operation_version="op1",
    )
    assert resolved.operation.identity.provider_version == "v1"

    registry = TransformationProviderRegistry()
    registry.register_provider(provider=_provider("provider", "v1", "op1"))
    registry.register_provider(provider=_provider("provider", "v2", "op1"))
    freeze_test_provider_registry(registry)
    with pytest.raises(TransformationProviderVersionAmbiguousError):
        registry.resolve_operation(
            provider_key="provider",
            operation_key="transform",
            operation_version="op1",
        )


def test_omitted_operation_version_requires_one_candidate() -> None:
    registry = TransformationProviderRegistry()
    registry.register_provider(provider=_provider("provider", "v1", "1", "2"))
    freeze_test_provider_registry(registry)

    with pytest.raises(TransformationOperationVersionAmbiguousError):
        registry.resolve_operation(
            provider_key="provider",
            provider_version="v1",
            operation_key="transform",
        )
    resolved = registry.resolve_operation(
        provider_key="provider",
        provider_version="v1",
        operation_key="transform",
        operation_version="1",
    )
    assert resolved.operation.identity.operation_version == "1"


def test_missing_operation_fails_narrowly() -> None:
    registry = TransformationProviderRegistry()
    registry.register_provider(provider=_provider("provider", "v1", "1"))
    freeze_test_provider_registry(registry)

    with pytest.raises(TransformationOperationNotFoundError):
        registry.resolve_operation(
            provider_key="provider",
            provider_version="v1",
            operation_key="missing",
            operation_version="1",
        )


def test_listings_are_sorted_and_do_not_expose_registry_collections() -> None:
    registry = TransformationProviderRegistry()
    registry.register_provider(provider=_provider("z-provider", "v2", "2", "1"))
    registry.register_provider(provider=_provider("a-provider", "v1", "1"))

    providers = registry.list_providers()
    identities = registry.list_provider_identities()
    capabilities = registry.list_capability_descriptors()
    operations = registry.list_operations()

    assert isinstance(providers, tuple)
    assert capabilities == providers
    assert identities == tuple(sorted(identities))
    assert isinstance(operations, tuple)
    assert [item.identity.provider_key for item in providers] == ["a-provider", "z-provider"]
    assert [item.identity for item in operations] == sorted(item.identity for item in operations)
