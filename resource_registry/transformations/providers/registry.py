from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from resource_registry.transformations.binding.immutable_json import freeze_json_value, thaw_json_value
from resource_registry.transformations.providers.base import TransformationOperationProvider
from resource_registry.transformations.providers.contracts import (
    TransformationOperationCapabilityDescriptor,
    TransformationProviderCapabilityDescriptor,
    TransformationProviderIdentity,
    validate_operation_key,
    validate_operation_version,
    validate_provider_key,
    validate_provider_version,
)
from resource_registry.transformations.providers.exceptions import (
    DuplicateOperationDescriptorError,
    DuplicateProviderRegistrationError,
    ProviderContractInconsistencyError,
    TransformationOperationNotFoundError,
    TransformationOperationVersionAmbiguousError,
    TransformationProviderNotFoundError,
    TransformationProviderRegistryLifecycleError,
    TransformationProviderVersionAmbiguousError,
)
from resource_registry.utils.json_checksum import canonicalize_transformation_specification


class TransformationProviderRegistryState(StrEnum):
    """Identify the irreversible provider-registry lifecycle state."""

    BUILDING = "BUILDING"
    VALIDATED = "VALIDATED"
    FROZEN = "FROZEN"


@dataclass(frozen=True)
class ResolvedTransformationProviderOperation:
    """Return one trusted provider and exact immutable operation descriptor."""

    provider: TransformationOperationProvider
    operation: TransformationOperationCapabilityDescriptor


class TransformationProviderRegistry:
    """Register trusted providers and resolve exact or uniquely unambiguous versions."""

    def __init__(self) -> None:
        self._providers: dict[TransformationProviderIdentity, TransformationOperationProvider] = {}
        self._state = TransformationProviderRegistryState.BUILDING
        self._manifest: tuple[Mapping[str, object], ...] = ()
        self._manifest_checksum: str | None = None

    @property
    def state(self) -> TransformationProviderRegistryState:
        """Return the current irreversible registry lifecycle state."""
        return self._state

    @property
    def manifest(self) -> tuple[Mapping[str, object], ...]:
        """Return the immutable validated execution-affecting manifest."""
        if self._state == TransformationProviderRegistryState.BUILDING:
            raise TransformationProviderRegistryLifecycleError(
                "Provider registry manifest is unavailable before validation.",
            )
        return self._manifest

    @property
    def manifest_checksum(self) -> str:
        """Return the deterministic validated provider manifest checksum."""
        if self._manifest_checksum is None:
            raise TransformationProviderRegistryLifecycleError(
                "Provider registry checksum is unavailable before validation.",
            )
        return self._manifest_checksum

    def register_provider(self, *, provider: TransformationOperationProvider) -> None:
        """Register one explicitly supplied trusted provider without dynamic loading.

        Args:
            provider: Provider instance supplied by the application composition root.

        Raises:
            ProviderContractInconsistencyError: If identity or operation ownership is inconsistent.
            DuplicateOperationDescriptorError: If an operation identity is declared twice.
            DuplicateProviderRegistrationError: If the provider identity is already registered.
        """
        self._require_state(TransformationProviderRegistryState.BUILDING)
        identity = provider.provider_identity
        descriptor = provider.capability_descriptor
        if descriptor.identity != identity:
            raise ProviderContractInconsistencyError(
                "Provider identity must match its capability descriptor identity.",
            )
        operation_identities = [operation.identity for operation in descriptor.operations]
        if len(set(operation_identities)) != len(operation_identities):
            raise DuplicateOperationDescriptorError(
                "Provider declares a duplicate exact operation identity.",
            )
        if identity in self._providers:
            raise DuplicateProviderRegistrationError(
                "Exact provider key and version are already registered.",
            )
        self._providers[identity] = provider

    def validate(self) -> None:
        """Validate every descriptor and calculate the canonical registry manifest."""
        self._require_state(TransformationProviderRegistryState.BUILDING)
        for identity, provider in sorted(self._providers.items()):
            descriptor = provider.capability_descriptor
            if descriptor.identity != identity or provider.provider_identity != identity:
                raise ProviderContractInconsistencyError(
                    "Registered provider identity does not match its descriptor.",
                )
            operation_identities = tuple(operation.identity for operation in descriptor.operations)
            if len(set(operation_identities)) != len(operation_identities):
                raise DuplicateOperationDescriptorError(
                    "Provider declares a duplicate exact operation identity.",
                )
        self._state = TransformationProviderRegistryState.VALIDATED
        manifest = tuple(self._build_manifest_entries())
        frozen_manifest: list[Mapping[str, object]] = []
        for entry in manifest:
            frozen = freeze_json_value(entry)
            if not isinstance(frozen, Mapping):
                raise ProviderContractInconsistencyError(
                    "Provider registry manifest entry must be an object.",
                )
            frozen_manifest.append(frozen)
        self._manifest = tuple(frozen_manifest)
        manifest_payload = cast(
            Mapping[str, object],
            thaw_json_value({"operations": self._manifest}),
        )
        canonical = canonicalize_transformation_specification(manifest_payload)
        self._manifest_checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def freeze(self) -> None:
        """Permanently freeze one validated provider registry."""
        self._require_state(TransformationProviderRegistryState.VALIDATED)
        if self._manifest_checksum is None:
            raise TransformationProviderRegistryLifecycleError(
                "Provider registry cannot freeze without a manifest checksum.",
            )
        self._state = TransformationProviderRegistryState.FROZEN

    def resolve_operation(
        self,
        *,
        provider_key: str,
        operation_key: str,
        provider_version: str | None = None,
        operation_version: str | None = None,
    ) -> ResolvedTransformationProviderOperation:
        """Resolve an exact operation, allowing only uniquely unambiguous omitted versions.

        Args:
            provider_key: Exact provider key.
            operation_key: Exact operation key.
            provider_version: Exact provider version, or None only for unique resolution.
            operation_version: Exact operation version, or None only for unique resolution.

        Returns:
            Trusted provider instance and exact operation descriptor.

        Raises:
            TransformationProviderNotFoundError: If no provider candidate exists.
            TransformationProviderVersionAmbiguousError: If provider version is omitted and
                multiple candidates exist.
            TransformationOperationNotFoundError: If no operation candidate exists.
            TransformationOperationVersionAmbiguousError: If operation version is omitted and
                multiple candidates exist.
        """
        self._require_state(TransformationProviderRegistryState.FROZEN)
        validate_provider_key(provider_key)
        validate_operation_key(operation_key)
        if provider_version is not None:
            validate_provider_version(provider_version)
        if operation_version is not None:
            validate_operation_version(operation_version)

        provider = self._resolve_provider(
            provider_key=provider_key,
            provider_version=provider_version,
        )
        operations = tuple(
            operation
            for operation in provider.capability_descriptor.operations
            if operation.identity.operation_key == operation_key
            and (
                operation_version is None
                or operation.identity.operation_version == operation_version
            )
        )
        if not operations:
            raise TransformationOperationNotFoundError(
                "No operation matches the requested exact identity.",
            )
        if operation_version is None and len(operations) > 1:
            raise TransformationOperationVersionAmbiguousError(
                "Operation version is required because multiple exact candidates exist.",
            )
        return ResolvedTransformationProviderOperation(provider=provider, operation=operations[0])

    def list_providers(self) -> tuple[TransformationProviderCapabilityDescriptor, ...]:
        """Return immutable provider descriptors sorted by exact identity."""
        return tuple(
            self._providers[identity].capability_descriptor for identity in sorted(self._providers)
        )

    def list_provider_identities(self) -> tuple[TransformationProviderIdentity, ...]:
        """Return immutable registered provider identities in exact sorted order."""
        return tuple(sorted(self._providers))

    def list_capability_descriptors(
        self,
    ) -> tuple[TransformationProviderCapabilityDescriptor, ...]:
        """Return immutable capability descriptors in exact provider identity order."""
        return self.list_providers()

    def list_operations(self) -> tuple[TransformationOperationCapabilityDescriptor, ...]:
        """Return immutable operation descriptors sorted by all exact identity fields."""
        return tuple(
            sorted(
                (
                    operation
                    for descriptor in self.list_providers()
                    for operation in descriptor.operations
                ),
                key=lambda operation: operation.identity,
            ),
        )

    def _resolve_provider(
        self,
        *,
        provider_key: str,
        provider_version: str | None,
    ) -> TransformationOperationProvider:
        """Resolve an exact or uniquely unambiguous provider version."""
        candidates = tuple(
            provider
            for identity, provider in sorted(self._providers.items())
            if identity.provider_key == provider_key
            and (provider_version is None or identity.provider_version == provider_version)
        )
        if not candidates:
            raise TransformationProviderNotFoundError(
                "No trusted provider matches the requested exact identity.",
            )
        if provider_version is None and len(candidates) > 1:
            raise TransformationProviderVersionAmbiguousError(
                "Provider version is required because multiple exact candidates exist.",
            )
        return candidates[0]

    def _build_manifest_entries(self) -> tuple[dict[str, object], ...]:
        entries: list[dict[str, object]] = []
        for descriptor in self.list_providers():
            for operation in sorted(descriptor.operations, key=lambda item: item.identity):
                identity = operation.identity
                entries.append(
                    {
                        "provider_key": identity.provider_key,
                        "provider_version": identity.provider_version,
                        "operation_key": identity.operation_key,
                        "operation_version": identity.operation_version,
                        "supported_input_formats": list(operation.supported_input_formats),
                        "supported_output_formats": list(operation.supported_output_formats),
                        "is_idempotent": operation.is_idempotent,
                        "is_checkpoint_reuse_safe": operation.is_checkpoint_reuse_safe,
                        "is_checkpoint_reconciliation_safe": (
                            operation.is_checkpoint_reconciliation_safe
                        ),
                        "supports_cooperative_cancellation": (
                            operation.supports_cooperative_cancellation
                        ),
                    },
                )
        return tuple(entries)

    def _require_state(self, expected: TransformationProviderRegistryState) -> None:
        if self._state != expected:
            raise TransformationProviderRegistryLifecycleError(
                f"Provider registry must be {expected.value} for this operation.",
            )
