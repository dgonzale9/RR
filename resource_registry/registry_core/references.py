"""Define opaque immutable reference contracts for Registry Core."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .errors import RegistryCoreValidationError
from .ownership import OwnerDescriptor

__all__ = ("OpaqueReference",)


@dataclass(frozen=True, slots=True)
class OpaqueReference:
    """Carry an owner-issued opaque reference without interpreting its value.

    Args:
        owner: Declarative owner of the reference's meaning.
        value: Owner-issued opaque non-blank token.
        parent: Optional opaque association to a parent reference.

    Raises:
        RegistryCoreValidationError: If a field does not satisfy the reference contract.
    """

    owner: OwnerDescriptor
    value: str
    parent: OpaqueReference | None = None

    def __post_init__(self) -> None:
        """Validate the reference fields without parsing or resolving them.

        Raises:
            RegistryCoreValidationError: If an owner, value, or parent is invalid.
        """
        if not isinstance(self.owner, OwnerDescriptor):
            raise RegistryCoreValidationError("invalid opaque reference owner")
        if not _is_non_blank_string(self.value):
            raise RegistryCoreValidationError("invalid opaque reference value")
        if self.parent is not None and not isinstance(self.parent, OpaqueReference):
            raise RegistryCoreValidationError("invalid opaque reference parent")


def _is_non_blank_string(candidate_value: object) -> bool:
    """Determine whether a candidate is a non-blank string.

    Args:
        candidate_value: Value to validate.

    Returns:
        Whether the value is a string containing at least one non-whitespace character.
    """
    return (
        isinstance(candidate_value, str)
        and not isinstance(candidate_value, Enum)
        and bool(candidate_value.strip())
    )
