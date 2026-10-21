"""Define pure immutable native version, artifact, and representation contracts."""

from __future__ import annotations

from dataclasses import dataclass

from resource_registry.registry_core import (
    ArtifactKey,
    MetadataEnvelope,
    MetadataScope,
    RegistryCoreValidationError,
    ResourceKey,
    ResourceVersionKey,
)
from resource_registry.registry_native_resource_module import RegistryNativeResourceRegistry

__all__ = (  # noqa: RUF022
    "RegistryNativeVersion",
    "RegistryNativeArtifact",
    "RegistryNativeRepresentation",
    "RegistryNativeVersionRegistry",
)


@dataclass(frozen=True, slots=True)
class RegistryNativeVersion:
    """Represent one immutable version declared by a Registry-native resource.

    Args:
        resource_key: Exact Registry-native resource that declares the version.
        version_key: Exact immutable identity for the declared version.
        ordinal: Positive resource-local ordering value without identity semantics.
        metadata: Exact version-scoped metadata envelopes for the version.

    Raises:
        RegistryCoreValidationError: If a field or metadata association is invalid.
    """

    resource_key: ResourceKey
    version_key: ResourceVersionKey
    ordinal: int
    metadata: tuple[MetadataEnvelope, ...]

    def __post_init__(self) -> None:
        """Validate fields and exact version metadata association in declaration order.

        Raises:
            RegistryCoreValidationError: If a field or metadata association is invalid.
        """
        if not isinstance(self.resource_key, ResourceKey):
            raise RegistryCoreValidationError("invalid native version resource key")
        if not isinstance(self.version_key, ResourceVersionKey):
            raise RegistryCoreValidationError("invalid native version key")
        if self.version_key.resource != self.resource_key:
            raise RegistryCoreValidationError("invalid native version key")
        if type(self.ordinal) is not int or self.ordinal <= 0:
            raise RegistryCoreValidationError("invalid native version ordinal")
        if type(self.metadata) is not tuple:
            raise RegistryCoreValidationError("invalid native version metadata")
        for metadata_envelope in self.metadata:
            if (
                not isinstance(metadata_envelope, MetadataEnvelope)
                or metadata_envelope.scope is not MetadataScope.VERSION
                or metadata_envelope.subject != self.version_key
            ):
                raise RegistryCoreValidationError("invalid native version metadata")


@dataclass(frozen=True, slots=True)
class RegistryNativeArtifact:
    """Represent one immutable logical artifact declared by a native version.

    Args:
        version_key: Exact native version that owns the artifact.
        artifact_key: Exact immutable identity for the logical artifact.
        metadata: Exact artifact-scoped metadata envelopes for the artifact.

    Raises:
        RegistryCoreValidationError: If a field or metadata association is invalid.
    """

    version_key: ResourceVersionKey
    artifact_key: ArtifactKey
    metadata: tuple[MetadataEnvelope, ...]

    def __post_init__(self) -> None:
        """Validate fields and exact artifact metadata association in declaration order.

        Raises:
            RegistryCoreValidationError: If a field or metadata association is invalid.
        """
        if not isinstance(self.version_key, ResourceVersionKey):
            raise RegistryCoreValidationError("invalid native artifact version key")
        if not isinstance(self.artifact_key, ArtifactKey):
            raise RegistryCoreValidationError("invalid native artifact key")
        if self.artifact_key.version != self.version_key:
            raise RegistryCoreValidationError("invalid native artifact key")
        if type(self.metadata) is not tuple:
            raise RegistryCoreValidationError("invalid native artifact metadata")
        for metadata_envelope in self.metadata:
            if (
                not isinstance(metadata_envelope, MetadataEnvelope)
                or metadata_envelope.scope is not MetadataScope.ARTIFACT
                or metadata_envelope.subject != self.artifact_key
            ):
                raise RegistryCoreValidationError("invalid native artifact metadata")


@dataclass(frozen=True, slots=True)
class RegistryNativeRepresentation:
    """Represent one supported logical representation of a native artifact.

    Args:
        artifact_key: Exact logical artifact supporting the representation.
        representation_id: Non-blank opaque representation identifier.
        media_type: Non-blank opaque media-type label.

    Raises:
        RegistryCoreValidationError: If a representation field is invalid.
    """

    artifact_key: ArtifactKey
    representation_id: str
    media_type: str

    def __post_init__(self) -> None:
        """Validate representation fields in declaration order without normalization.

        Raises:
            RegistryCoreValidationError: If a representation field is invalid.
        """
        if not isinstance(self.artifact_key, ArtifactKey):
            raise RegistryCoreValidationError("invalid native representation artifact key")
        if not _is_non_blank_plain_string(self.representation_id):
            raise RegistryCoreValidationError("invalid native representation id")
        if not _is_non_blank_plain_string(self.media_type):
            raise RegistryCoreValidationError("invalid native representation media type")


@dataclass(frozen=True, slots=True)
class RegistryNativeVersionRegistry:
    """Resolve immutable declarations under exact admitted Registry-native parents.

    Args:
        native_resource_registry: Completed Registry-native resource registry containing parents.
        versions: Declared native versions in preserved declaration order.
        artifacts: Declared native artifacts in preserved declaration order.
        representations: Declared native representations in preserved declaration order.

    Raises:
        RegistryCoreValidationError: If dependencies, associations, or uniqueness rules are invalid.
    """

    native_resource_registry: RegistryNativeResourceRegistry
    versions: tuple[RegistryNativeVersion, ...]
    artifacts: tuple[RegistryNativeArtifact, ...]
    representations: tuple[RegistryNativeRepresentation, ...]

    def __post_init__(self) -> None:
        """Validate declarations before parent admission and duplicate checks.

        Raises:
            RegistryCoreValidationError: If registry state violates the native declaration contract.
        """
        if not isinstance(self.native_resource_registry, RegistryNativeResourceRegistry):
            raise RegistryCoreValidationError("invalid native version registry")
        if (
            type(self.versions) is not tuple
            or type(self.artifacts) is not tuple
            or type(self.representations) is not tuple
        ):
            raise RegistryCoreValidationError("invalid native version registry")

        _validate_native_versions(self.versions)
        _validate_native_artifacts(self.artifacts)
        _validate_native_representations(self.representations)
        _validate_native_parent_associations(
            self.native_resource_registry,
            self.versions,
            self.artifacts,
            self.representations,
        )
        _validate_native_declaration_uniqueness(
            self.versions,
            self.artifacts,
            self.representations,
        )

    def resolve_version(self, version_key: ResourceVersionKey) -> RegistryNativeVersion:
        """Resolve one declared native version by exact typed identity.

        Args:
            version_key: Exact version identity to resolve.

        Returns:
            The declared immutable native version.

        Raises:
            RegistryCoreValidationError: If the key is invalid or has no declared native version.
        """
        if not isinstance(version_key, ResourceVersionKey):
            raise RegistryCoreValidationError("invalid native version key")
        for version in self.versions:
            if version.version_key == version_key:
                return version
        raise RegistryCoreValidationError("unknown native version parent resource")

    def resolve_versions_for_resource(
        self,
        resource_key: ResourceKey,
    ) -> tuple[RegistryNativeVersion, ...]:
        """Resolve declared versions for one exact parent in ascending ordinal order.

        Args:
            resource_key: Exact Registry-native resource identity containing the versions.

        Returns:
            Immutable native versions ordered by ascending positive ordinal.

        Raises:
            RegistryCoreValidationError: If the key is invalid or not an admitted native parent.
        """
        if not isinstance(resource_key, ResourceKey):
            raise RegistryCoreValidationError("invalid native version resource key")
        if not any(
            resource.resource_key == resource_key
            for resource in self.native_resource_registry.resources
        ):
            raise RegistryCoreValidationError("unknown native version parent resource")
        return tuple(
            sorted(
                (version for version in self.versions if version.resource_key == resource_key),
                key=lambda version: version.ordinal,
            )
        )

    def resolve_artifact(self, artifact_key: ArtifactKey) -> RegistryNativeArtifact:
        """Resolve one declared native artifact by exact typed identity.

        Args:
            artifact_key: Exact artifact identity to resolve.

        Returns:
            The declared immutable native artifact.

        Raises:
            RegistryCoreValidationError: If the key is invalid or has no declared native artifact.
        """
        if not isinstance(artifact_key, ArtifactKey):
            raise RegistryCoreValidationError("invalid native artifact key")
        for artifact in self.artifacts:
            if artifact.artifact_key == artifact_key:
                return artifact
        raise RegistryCoreValidationError("unknown native artifact parent version")

    def resolve_representation(
        self,
        artifact_key: ArtifactKey,
        representation_id: str,
    ) -> RegistryNativeRepresentation:
        """Resolve one declared representation by exact artifact and opaque identifier.

        Args:
            artifact_key: Exact artifact identity containing the representation.
            representation_id: Exact non-blank opaque representation identifier.

        Returns:
            The declared immutable native representation.

        Raises:
            RegistryCoreValidationError: If lookup input is invalid or no declaration matches.
        """
        if not isinstance(artifact_key, ArtifactKey):
            raise RegistryCoreValidationError("invalid native representation artifact key")
        if not _is_non_blank_plain_string(representation_id):
            raise RegistryCoreValidationError("invalid native representation id")
        for representation in self.representations:
            if (
                representation.artifact_key == artifact_key
                and representation.representation_id == representation_id
            ):
                return representation
        raise RegistryCoreValidationError("unknown native representation parent artifact")


def _validate_native_versions(versions: tuple[RegistryNativeVersion, ...]) -> None:
    """Validate the declared version collection before association checks.

    Args:
        versions: Candidate native version declarations.

    Raises:
        RegistryCoreValidationError: If an item is not a native version declaration.
    """
    if not all(isinstance(version, RegistryNativeVersion) for version in versions):
        raise RegistryCoreValidationError("invalid native version registry")


def _validate_native_artifacts(artifacts: tuple[RegistryNativeArtifact, ...]) -> None:
    """Validate the declared artifact collection before association checks.

    Args:
        artifacts: Candidate native artifact declarations.

    Raises:
        RegistryCoreValidationError: If an item is not a native artifact declaration.
    """
    if not all(isinstance(artifact, RegistryNativeArtifact) for artifact in artifacts):
        raise RegistryCoreValidationError("invalid native version registry")


def _validate_native_representations(
    representations: tuple[RegistryNativeRepresentation, ...],
) -> None:
    """Validate the declared representation collection before association checks.

    Args:
        representations: Candidate native representation declarations.

    Raises:
        RegistryCoreValidationError: If an item is not a native representation declaration.
    """
    if not all(
        isinstance(representation, RegistryNativeRepresentation)
        for representation in representations
    ):
        raise RegistryCoreValidationError("invalid native version registry")


def _validate_native_parent_associations(
    native_resource_registry: RegistryNativeResourceRegistry,
    versions: tuple[RegistryNativeVersion, ...],
    artifacts: tuple[RegistryNativeArtifact, ...],
    representations: tuple[RegistryNativeRepresentation, ...],
) -> None:
    """Validate every declaration's exact parent in declaration-category order.

    Args:
        native_resource_registry: Registry containing admitted native resource parents.
        versions: Valid native version declarations.
        artifacts: Valid native artifact declarations.
        representations: Valid native representation declarations.

    Raises:
        RegistryCoreValidationError: If an exact parent declaration is unknown.
    """
    native_resource_keys = {
        resource.resource_key for resource in native_resource_registry.resources
    }
    for version in versions:
        if version.resource_key not in native_resource_keys:
            raise RegistryCoreValidationError("unknown native version parent resource")

    version_keys = {version.version_key for version in versions}
    for artifact in artifacts:
        if artifact.version_key not in version_keys:
            raise RegistryCoreValidationError("unknown native artifact parent version")

    artifact_keys = {artifact.artifact_key for artifact in artifacts}
    for representation in representations:
        if representation.artifact_key not in artifact_keys:
            raise RegistryCoreValidationError("unknown native representation parent artifact")


def _validate_native_declaration_uniqueness(
    versions: tuple[RegistryNativeVersion, ...],
    artifacts: tuple[RegistryNativeArtifact, ...],
    representations: tuple[RegistryNativeRepresentation, ...],
) -> None:
    """Validate exact native declaration uniqueness after parent association succeeds.

    Args:
        versions: Admitted native version declarations.
        artifacts: Admitted native artifact declarations.
        representations: Admitted native representation declarations.

    Raises:
        RegistryCoreValidationError: If an exact identity or ordinal collision exists.
    """
    version_keys: set[ResourceVersionKey] = set()
    version_ordinals: set[tuple[ResourceKey, int]] = set()
    for version in versions:
        if version.version_key in version_keys:
            raise RegistryCoreValidationError("duplicate native version key")
        if (version.resource_key, version.ordinal) in version_ordinals:
            raise RegistryCoreValidationError("duplicate native version ordinal")
        version_keys.add(version.version_key)
        version_ordinals.add((version.resource_key, version.ordinal))

    artifact_keys: set[ArtifactKey] = set()
    for artifact in artifacts:
        if artifact.artifact_key in artifact_keys:
            raise RegistryCoreValidationError("duplicate native artifact key")
        artifact_keys.add(artifact.artifact_key)

    representation_keys: set[tuple[ArtifactKey, str]] = set()
    for representation in representations:
        representation_key = (representation.artifact_key, representation.representation_id)
        if representation_key in representation_keys:
            raise RegistryCoreValidationError("duplicate native representation")
        representation_keys.add(representation_key)


def _is_non_blank_plain_string(candidate_value: object) -> bool:
    """Determine whether an opaque representation value is a non-blank plain string.

    Args:
        candidate_value: Candidate opaque representation value.

    Returns:
        Whether the candidate is a plain string containing a non-whitespace character.
    """
    return type(candidate_value) is str and bool(candidate_value.strip())
