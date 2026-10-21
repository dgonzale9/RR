"""Verify the ERR-1.3-S1 Registry-native Resource Module contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from uuid import UUID

import pytest

from resource_registry.identity_type_module import ResourceTypeDeclaration, ResourceTypeRegistry
from resource_registry.namespace_module import NamespaceDeclaration, NamespaceRegistry
from resource_registry.registry_core import (
    MetadataClassification,
    MetadataEnvelope,
    MetadataNamespaceKey,
    MetadataProvenance,
    MetadataSchemaKey,
    MetadataScope,
    NamespaceKey,
    OwnerDescriptor,
    OwnershipClassification,
    RegistryCoreValidationError,
    ResourceKey,
    ResourceTypeKey,
)
from resource_registry.registry_native_resource_module import (
    RegistryNativeResource,
    RegistryNativeResourceLifecycle,
    RegistryNativeResourceRegistry,
)


def _build_namespace() -> NamespaceKey:
    """Build a valid namespace fixture.

    Returns:
        A valid namespace key.
    """
    return NamespaceKey(UUID("8dbf68a1-9567-4d74-931f-c36c4247f80b"))


def _build_resource_key(resource_id: str = "native-1") -> ResourceKey:
    """Build a valid Registry-native resource key fixture.

    Args:
        resource_id: Exact owner-issued resource identifier.

    Returns:
        A valid Registry-native resource key.
    """
    return ResourceKey(_build_namespace(), ResourceTypeKey("atlas.registry.reference"), resource_id)


def _build_type_registry(
    classification: OwnershipClassification = OwnershipClassification.REGISTRY_NATIVE,
) -> ResourceTypeRegistry:
    """Build a type registry with a configurable ownership classification.

    Args:
        classification: Ownership classification for the fixture type.

    Returns:
        A valid resource type registry.
    """
    return ResourceTypeRegistry(
        (
            ResourceTypeDeclaration(
                ResourceTypeKey("atlas.registry.reference"),
                OwnerDescriptor("registry-core", classification),
            ),
        )
    )


def _build_namespace_registry() -> NamespaceRegistry:
    """Build a Namespace registry admitting the fixture key.

    Returns:
        A valid namespace registry.
    """
    return NamespaceRegistry((NamespaceDeclaration(_build_namespace(), 42),))


def _build_resource(resource_id: str = "native-1") -> RegistryNativeResource:
    """Build a valid Registry-native resource fixture.

    Args:
        resource_id: Exact owner-issued resource identifier.

    Returns:
        A valid immutable native resource.
    """
    return RegistryNativeResource(
        _build_resource_key(resource_id), RegistryNativeResourceLifecycle.DRAFT, ()
    )


def _build_metadata(resource_key: ResourceKey) -> MetadataEnvelope:
    """Build metadata associated with an exact resource key.

    Args:
        resource_key: Exact resource key for the metadata subject.

    Returns:
        A valid immutable resource-scoped metadata envelope.
    """
    return MetadataEnvelope(
        scope=MetadataScope.RESOURCE,
        subject=resource_key,
        schema=MetadataSchemaKey(
            namespace=MetadataNamespaceKey(value="atlas.registry.reference"),
            name="details",
        ),
        schema_version="1",
        revision=1,
        classification=MetadataClassification.INTERNAL,
        provenance=MetadataProvenance(source_kind="registry", producer="fixture"),
        observed_at=datetime(2026, 7, 22, tzinfo=UTC),
        payload={"label": "native"},
    )


def test_registry_native_module_exports_and_preserves_immutable_identity() -> None:
    """Expose only the authorized contracts and preserve exact ResourceKey identity."""
    from resource_registry import registry_native_resource_module

    resource = _build_resource()
    assert registry_native_resource_module.__all__ == (
        "RegistryNativeResource",
        "RegistryNativeResourceLifecycle",
        "RegistryNativeResourceRegistry",
    )
    assert tuple(field.name for field in fields(RegistryNativeResource)) == (
        "resource_key",
        "lifecycle",
        "metadata",
    )
    assert resource == _build_resource()
    assert hash(resource) == hash(_build_resource())
    with pytest.raises(FrozenInstanceError):
        resource.lifecycle = RegistryNativeResourceLifecycle.ACTIVE  # type: ignore[misc]


def test_registry_native_resource_registry_admits_and_resolves_native_resources() -> None:
    """Resolve an admitted Registry-native resource by its exact composite key."""
    resource = _build_resource()
    registry = RegistryNativeResourceRegistry(
        _build_type_registry(), _build_namespace_registry(), (resource,)
    )

    assert registry.resolve_resource(resource.resource_key) == resource
    assert registry.resources == (resource,)


def test_registry_native_resource_preserves_only_exact_resource_metadata() -> None:
    """Associate immutable resource-scoped metadata with its exact aggregate identity."""
    resource_key = _build_resource_key()
    metadata = _build_metadata(resource_key)
    resource = RegistryNativeResource(
        resource_key, RegistryNativeResourceLifecycle.ACTIVE, (metadata,)
    )

    assert resource.metadata == (metadata,)
    with pytest.raises(
        RegistryCoreValidationError,
        match="^registry-native resource metadata subject mismatch$",
    ):
        RegistryNativeResource(
            resource_key,
            RegistryNativeResourceLifecycle.ACTIVE,
            (_build_metadata(_build_resource_key("other")),),
        )


def test_registry_native_resource_registry_rejects_domain_backed_types_and_collisions() -> None:
    """Reject Domain-backed types and duplicate exact native resource identities."""
    resource = _build_resource()
    with pytest.raises(RegistryCoreValidationError, match="^resource type is not registry-native$"):
        RegistryNativeResourceRegistry(
            _build_type_registry(OwnershipClassification.DOMAIN_BACKED),
            _build_namespace_registry(),
            (resource,),
        )
    with pytest.raises(
        RegistryCoreValidationError, match="^duplicate registry-native resource key$"
    ):
        RegistryNativeResourceRegistry(
            _build_type_registry(), _build_namespace_registry(), (resource, resource)
        )


def test_registry_native_resource_registry_fails_closed_for_unknown_namespace_and_resource() -> (
    None
):
    """Reject unknown namespaces and exact resource lookup misses without fallback behavior."""
    unknown_namespace_key = ResourceKey(
        NamespaceKey("1dbf68a1-9567-4d74-931f-c36c4247f80b"),
        ResourceTypeKey("atlas.registry.reference"),
        "native-1",
    )
    registry = RegistryNativeResourceRegistry(
        _build_type_registry(), _build_namespace_registry(), (_build_resource(),)
    )

    with pytest.raises(RegistryCoreValidationError, match="^unknown namespace$"):
        registry.resolve_resource(unknown_namespace_key)
    with pytest.raises(RegistryCoreValidationError, match="^unknown registry-native resource$"):
        registry.resolve_resource(_build_resource_key("missing"))
