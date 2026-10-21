"""Define immutable identity value contracts for Registry Core."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import overload
from uuid import UUID

from .errors import RegistryCoreValidationError

__all__ = ("NamespaceKey", "ResourceTypeKey", "ResourceKey")  # noqa: RUF022

_RESOURCE_TYPE_KEY_PATTERN = r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*){2,}"
_MAXIMUM_RESOURCE_TYPE_KEY_LENGTH = 128


@dataclass(frozen=True, slots=True, init=False)
class NamespaceKey:
    """Identify one Registry namespace with a canonical UUID.

    Args:
        value: UUID object or canonical lowercase RFC-4122 UUID text.

    Raises:
        RegistryCoreValidationError: If the value is not an accepted namespace UUID.
    """

    value: UUID

    @overload
    def __init__(self, value: UUID) -> None: ...

    @overload
    def __init__(self, value: str) -> None: ...

    def __init__(self, value: UUID | str) -> None:
        """Validate and store the namespace as a UUID value.

        Args:
            value: UUID object or canonical lowercase RFC-4122 UUID text.

        Raises:
            RegistryCoreValidationError: If the value is not an accepted namespace UUID.
        """
        if isinstance(value, UUID):
            object.__setattr__(self, "value", value)
            return
        if type(value) is not str:
            raise RegistryCoreValidationError("invalid namespace key")
        try:
            parsed_value = UUID(value)
        except ValueError:
            raise RegistryCoreValidationError("invalid namespace key") from None
        if str(parsed_value) != value:
            raise RegistryCoreValidationError("invalid namespace key")
        object.__setattr__(self, "value", parsed_value)


@dataclass(frozen=True, slots=True)
class ResourceTypeKey:
    """Identify stable resource semantics with a lowercase three-segment key.

    Args:
        value: Lowercase ASCII resource type key with at least three dot-separated segments.

    Raises:
        RegistryCoreValidationError: If the value does not satisfy the resource type grammar.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the stable resource type key without resolving or registering it.

        Raises:
            RegistryCoreValidationError: If the value does not satisfy the resource type grammar.
        """
        if not _is_valid_resource_type_key(self.value):
            raise RegistryCoreValidationError("invalid resource type key")


@dataclass(frozen=True, slots=True)
class ResourceKey:
    """Identify one issuer-owned resource within its namespace and resource type.

    Args:
        namespace: Exact immutable Registry namespace identity.
        resource_type: Exact immutable resource semantics identity.
        resource_id: Non-blank issuer-owned identifier preserved without normalization.

    Raises:
        RegistryCoreValidationError: If a component does not satisfy the resource key contract.
    """

    namespace: NamespaceKey
    resource_type: ResourceTypeKey
    resource_id: str

    def __post_init__(self) -> None:
        """Validate components in their public field order without resolving them.

        Raises:
            RegistryCoreValidationError: If a component is not an exact accepted value.
        """
        if not isinstance(self.namespace, NamespaceKey):
            raise RegistryCoreValidationError("invalid resource key namespace")
        if not isinstance(self.resource_type, ResourceTypeKey):
            raise RegistryCoreValidationError("invalid resource key type")
        if not _is_non_blank_string(self.resource_id):
            raise RegistryCoreValidationError("invalid resource key id")


def _is_valid_resource_type_key(candidate_value: object) -> bool:
    """Determine whether a value has the approved resource type key grammar.

    Args:
        candidate_value: Candidate resource type key.

    Returns:
        Whether the candidate is a lowercase ASCII three-segment resource type key.
    """
    return (
        type(candidate_value) is str
        and len(candidate_value) <= _MAXIMUM_RESOURCE_TYPE_KEY_LENGTH
        and re.fullmatch(_RESOURCE_TYPE_KEY_PATTERN, candidate_value) is not None
    )


def _is_non_blank_string(candidate_value: object) -> bool:
    """Determine whether a value is a non-blank plain string.

    Args:
        candidate_value: Candidate issuer-owned resource identifier.

    Returns:
        Whether the candidate is a plain string containing a non-whitespace character.
    """
    return type(candidate_value) is str and bool(candidate_value.strip())
