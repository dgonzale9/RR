"""Define pure immutable Provider certification evidence contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import ProviderIdentity
from .errors import ProviderCertificationBindingValidationError

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]*\Z", re.ASCII)
_MAXIMUM_LENGTH = 256
_FORBIDDEN_TOKENS = (
    "active",
    "availability",
    "callable",
    "capacity",
    "class",
    "client",
    "credential",
    "default",
    "endpoint",
    "fallback",
    "health",
    "http",
    "import",
    "namespace",
    "password",
    "policy",
    "priority",
    "router",
    "sdk",
    "secret",
    "tenant",
    "token",
    "uri",
    "url",
)


@dataclass(frozen=True, slots=True)
class ProviderCertificationProfileRef:
    """Identify one exact immutable Provider certification profile revision.

    Args:
        value: Exact safe certification-profile revision identifier.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate one exact profile reference without loading a certification suite.

        Raises:
            ProviderCertificationBindingValidationError: If the profile reference is unsafe.
        """
        if not _valid_reference(self.value):
            raise ProviderCertificationBindingValidationError(
                "invalid provider certification profile"
            )


@dataclass(frozen=True, slots=True)
class ProviderCertificationEvidenceRef:
    """Identify one exact immutable Provider certification evidence record.

    Args:
        value: Exact safe opaque certification evidence identifier.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate one evidence reference without evaluating its certification claim.

        Raises:
            ProviderCertificationBindingValidationError: If the evidence reference is unsafe.
        """
        if not _valid_reference(self.value):
            raise ProviderCertificationBindingValidationError(
                "invalid provider certification evidence"
            )


@dataclass(frozen=True, slots=True)
class ProviderCertificationRecord:
    """Carry immutable certification evidence for one exact Provider contract profile.

    Args:
        provider_identity: Exact Provider version covered by the certification evidence.
        adapter_contract_reference: Exact safe adapter-contract revision identifier.
        configuration_schema_reference: Exact safe configuration-schema revision identifier.
        profile_reference: Exact certification profile revision.
        evidence_reference: Exact immutable certification evidence identifier.
    """

    provider_identity: ProviderIdentity
    adapter_contract_reference: str
    configuration_schema_reference: str
    profile_reference: ProviderCertificationProfileRef
    evidence_reference: ProviderCertificationEvidenceRef

    def __post_init__(self) -> None:
        """Validate certification provenance without certifying or authorizing a Provider.

        Raises:
            ProviderCertificationBindingValidationError: If certification facts are malformed.
        """
        if (
            not isinstance(self.provider_identity, ProviderIdentity)
            or not _valid_reference(self.adapter_contract_reference)
            or not _valid_reference(self.configuration_schema_reference)
            or not isinstance(self.profile_reference, ProviderCertificationProfileRef)
            or not isinstance(self.evidence_reference, ProviderCertificationEvidenceRef)
        ):
            raise ProviderCertificationBindingValidationError(
                "invalid provider certification record"
            )


def _valid_reference(value: object) -> bool:
    """Return whether a bounded exact safe certification reference is valid."""
    return (
        type(value) is str
        and len(value) <= _MAXIMUM_LENGTH
        and _IDENTIFIER.fullmatch(value) is not None
        and value not in {"current", "latest"}
        and not any(token in value.lower() for token in _FORBIDDEN_TOKENS)
    )
