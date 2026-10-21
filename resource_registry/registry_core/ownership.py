"""Define immutable ownership contracts for Registry Core."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .errors import RegistryCoreValidationError

_BOUNDED_CONTEXT_IDENTIFIER_PATTERN = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"


class OwnershipClassification(str, Enum):
    """Classify whether a resource has Domain-backed or Registry-native ownership."""

    DOMAIN_BACKED = "domain_backed"
    REGISTRY_NATIVE = "registry_native"

    @classmethod
    def _missing_(cls, value: object) -> OwnershipClassification | None:
        """Reject values outside the two authoritative ownership classifications.

        Args:
            value: Candidate classification value.

        Raises:
            RegistryCoreValidationError: If the value is not an accepted classification.
        """
        raise RegistryCoreValidationError("invalid ownership classification")


@dataclass(frozen=True, slots=True)
class OwnerDescriptor:
    """Describe one stable owning bounded context and its ownership classification.

    The descriptor is declarative only. It neither resolves an owner nor grants authority to it.

    Args:
        bounded_context_identifier: Stable lowercase, hyphen-separated bounded-context identifier.
        ownership_classification: The authoritative classification for the owning context.

    Raises:
        RegistryCoreValidationError: If either value is malformed or runtime-coupled.
    """

    bounded_context_identifier: str
    ownership_classification: OwnershipClassification

    def __post_init__(self) -> None:
        """Validate the descriptor's stable, declarative value fields.

        Raises:
            RegistryCoreValidationError: If the identifier or classification is invalid.
        """
        if not _is_valid_bounded_context_identifier(self.bounded_context_identifier):
            raise RegistryCoreValidationError("invalid owner bounded-context identifier")
        if not isinstance(self.ownership_classification, OwnershipClassification):
            raise RegistryCoreValidationError("invalid ownership classification")


def _is_valid_bounded_context_identifier(bounded_context_identifier: object) -> bool:
    """Determine whether a bounded-context identifier is stable and non-executable.

    Args:
        bounded_context_identifier: Candidate identifier to validate.

    Returns:
        Whether the identifier uses the canonical lowercase, hyphen-separated form.
    """
    return isinstance(bounded_context_identifier, str) and bool(
        re.fullmatch(_BOUNDED_CONTEXT_IDENTIFIER_PATTERN, bounded_context_identifier)
    )
