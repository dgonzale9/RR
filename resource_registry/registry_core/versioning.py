"""Define immutable version and artifact identity value contracts for Registry Core."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import RegistryCoreValidationError
from .identity import ResourceKey

__all__ = ("ResourceVersionKey", "ArtifactKey")  # noqa: RUF022


@dataclass(frozen=True, slots=True)
class ResourceVersionKey:
    """Identify one issuer-owned version of an exact resource.

    Args:
        resource: Exact immutable resource identity that owns the version.
        version_id: Non-blank issuer-owned version identifier preserved without normalization.

    Raises:
        RegistryCoreValidationError: If a component does not satisfy the version key contract.
    """

    resource: ResourceKey
    version_id: str

    def __post_init__(self) -> None:
        """Validate components in their public field order without resolving them.

        Raises:
            RegistryCoreValidationError: If a component is not an exact accepted value.
        """
        if not isinstance(self.resource, ResourceKey):
            raise RegistryCoreValidationError("invalid resource version key resource")
        if not _is_non_blank_string(self.version_id):
            raise RegistryCoreValidationError("invalid resource version key id")


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactKey:
    """Identify one issuer-owned artifact under an exact resource and optional version.

    Args:
        resource: Exact immutable resource identity that owns the artifact.
        version: Exact optional version identity that must belong to ``resource``.
        artifact_id: Non-blank issuer-owned artifact identifier preserved without normalization.

    Raises:
        RegistryCoreValidationError: If a component does not satisfy the artifact key contract.
    """

    resource: ResourceKey
    version: ResourceVersionKey | None = None
    artifact_id: str

    def __post_init__(self) -> None:
        """Validate components in their public field order without resolving them.

        Raises:
            RegistryCoreValidationError: If a component is not an exact accepted value.
        """
        if not isinstance(self.resource, ResourceKey):
            raise RegistryCoreValidationError("invalid artifact key resource")
        if self.version is not None and not isinstance(self.version, ResourceVersionKey):
            raise RegistryCoreValidationError("invalid artifact key version")
        if self.version is not None and self.version.resource != self.resource:
            raise RegistryCoreValidationError("artifact key version resource mismatch")
        if not _is_non_blank_string(self.artifact_id):
            raise RegistryCoreValidationError("invalid artifact key id")


def _is_non_blank_string(candidate_value: object) -> bool:
    """Determine whether a value is a non-blank plain string.

    Args:
        candidate_value: Candidate issuer-owned identifier.

    Returns:
        Whether the candidate is a plain string containing a non-whitespace character.
    """
    return type(candidate_value) is str and bool(candidate_value.strip())
