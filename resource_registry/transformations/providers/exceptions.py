from __future__ import annotations


class TransformationProviderContractError(Exception):
    """Base error for transformation provider contracts and registry operations."""


class InvalidProviderKeyError(TransformationProviderContractError):
    """Raise when a provider key is not an exact non-whitespace string."""


class InvalidProviderVersionError(TransformationProviderContractError):
    """Raise when a provider version is not an exact non-whitespace string."""


class InvalidOperationKeyError(TransformationProviderContractError):
    """Raise when an operation key is not an exact non-whitespace string."""


class InvalidOperationVersionError(TransformationProviderContractError):
    """Raise when an operation version is not an exact non-whitespace string."""


class ProviderContractInconsistencyError(TransformationProviderContractError):
    """Raise when a provider instance and its immutable descriptor disagree."""


class DuplicateProviderRegistrationError(TransformationProviderContractError):
    """Raise when an exact provider identity is already registered."""


class DuplicateOperationDescriptorError(TransformationProviderContractError):
    """Raise when a provider declares an operation identity more than once."""


class TransformationProviderRegistryLifecycleError(TransformationProviderContractError):
    """Raise when a registry operation violates its irreversible lifecycle."""


class TransformationProviderNotFoundError(TransformationProviderContractError):
    """Raise when no trusted provider matches a requested identity."""


class TransformationProviderVersionAmbiguousError(TransformationProviderContractError):
    """Raise when an omitted provider version has multiple exact candidates."""


class TransformationOperationNotFoundError(TransformationProviderContractError):
    """Raise when no operation matches inside the resolved provider version."""


class TransformationOperationVersionAmbiguousError(TransformationProviderContractError):
    """Raise when an omitted operation version has multiple exact candidates."""
