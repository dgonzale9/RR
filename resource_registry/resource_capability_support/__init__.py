"""Expose pure immutable Resource and ResourceType capability-support declarations."""

from .contracts import (
    ResourceCapabilitySupportDeclaration,
    ResourceCapabilitySupportDeclarationSet,
    ResourceTypeCapabilitySupportDeclaration,
)
from .errors import ResourceCapabilitySupportValidationError

__all__ = (  # noqa: RUF022
    "ResourceCapabilitySupportValidationError",
    "ResourceTypeCapabilitySupportDeclaration",
    "ResourceCapabilitySupportDeclaration",
    "ResourceCapabilitySupportDeclarationSet",
)
