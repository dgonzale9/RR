"""Define pure trusted Provider descriptor manifest and exact lookup contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from .contracts import ProviderDescriptor, ProviderIdentity
from .errors import ProviderContractValidationError


class ProviderRegistryBuildStatus(StrEnum):
    """Classify a pure Provider Registry manifest build result."""

    BUILT = "built"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ProviderRegistryManifest:
    """Carry one exact canonical Provider descriptor manifest before runtime readiness."""

    descriptors: tuple[ProviderDescriptor, ...]
    checksum: str

    def __post_init__(self) -> None:
        """Validate a canonical, exact, duplicate-free manifest without runtime construction."""
        if type(self.descriptors) is not tuple or not self.descriptors:
            raise ProviderContractValidationError("invalid provider registry descriptors")
        if any(not isinstance(descriptor, ProviderDescriptor) for descriptor in self.descriptors):
            raise ProviderContractValidationError("invalid provider registry descriptor")
        if tuple(sorted(self.descriptors, key=_identity_sort_key)) != self.descriptors:
            raise ProviderContractValidationError("noncanonical provider registry manifest")
        if len({descriptor.identity for descriptor in self.descriptors}) != len(self.descriptors):
            raise ProviderContractValidationError("duplicate provider registry identity")
        expected_checksum = _manifest_checksum(self.descriptors)
        if self.checksum != expected_checksum:
            raise ProviderContractValidationError("invalid provider registry checksum")


@dataclass(frozen=True, slots=True)
class ProviderRegistryBuildResult:
    """Carry a deterministic built or rejected pure manifest result."""

    status: ProviderRegistryBuildStatus
    manifest: ProviderRegistryManifest | None
    rejection_reason: str | None = None


@dataclass(frozen=True, slots=True)
class FrozenProviderRegistry:
    """Resolve only exact descriptors from one frozen pure Provider manifest."""

    manifest: ProviderRegistryManifest

    def __post_init__(self) -> None:
        """Validate the immutable frozen manifest boundary."""
        if not isinstance(self.manifest, ProviderRegistryManifest):
            raise ProviderContractValidationError("invalid frozen provider registry manifest")


def build_provider_registry_manifest(
    descriptors: tuple[ProviderDescriptor, ...],
) -> ProviderRegistryBuildResult:
    """Build a deterministic descriptor manifest without discovery or runtime registration."""
    if type(descriptors) is not tuple or any(
        not isinstance(descriptor, ProviderDescriptor) for descriptor in descriptors
    ):
        raise ProviderContractValidationError("invalid provider registry build descriptors")
    if not descriptors:
        return ProviderRegistryBuildResult(
            ProviderRegistryBuildStatus.REJECTED, None, "provider registry manifest is empty"
        )
    ordered = tuple(sorted(descriptors, key=_identity_sort_key))
    if len({descriptor.identity for descriptor in ordered}) != len(ordered):
        return ProviderRegistryBuildResult(
            ProviderRegistryBuildStatus.REJECTED, None, "duplicate provider registry identity"
        )
    return ProviderRegistryBuildResult(
        ProviderRegistryBuildStatus.BUILT,
        ProviderRegistryManifest(ordered, _manifest_checksum(ordered)),
    )


def freeze_provider_registry_manifest(manifest: ProviderRegistryManifest) -> FrozenProviderRegistry:
    """Freeze a validated manifest for exact resolve-only access."""
    if not isinstance(manifest, ProviderRegistryManifest):
        raise ProviderContractValidationError("invalid provider registry manifest")
    return FrozenProviderRegistry(manifest)


def resolve_registered_provider(
    registry: FrozenProviderRegistry, identity: ProviderIdentity
) -> ProviderDescriptor:
    """Resolve one exact registered descriptor or fail closed without candidate selection."""
    if not isinstance(registry, FrozenProviderRegistry):
        raise ProviderContractValidationError("invalid frozen provider registry")
    if not isinstance(identity, ProviderIdentity):
        raise ProviderContractValidationError("invalid provider registry identity")
    for descriptor in registry.manifest.descriptors:
        if descriptor.identity == identity:
            return descriptor
    raise ProviderContractValidationError("unknown provider registry identity")


def _identity_sort_key(descriptor: ProviderDescriptor) -> tuple[str, str, str]:
    """Build a deterministic exact Provider identity sort key."""
    identity = descriptor.identity
    return identity.key.value, identity.version.scheme, identity.version.value


def _manifest_checksum(descriptors: tuple[ProviderDescriptor, ...]) -> str:
    """Calculate a stable checksum from canonical safe descriptor values."""
    material = "\n".join(descriptor.to_canonical_json() for descriptor in descriptors)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
