"""Define provider-neutral adapter port envelopes without invocation behavior."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .configuration import ProviderConfigurationRef
from .contracts import ProviderIdentity
from .errors import ProviderContractValidationError

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]*\Z", re.ASCII)
_MAXIMUM_LENGTH = 256
_FORBIDDEN_TOKENS = (
    "callable",
    "class",
    "client",
    "credential",
    "endpoint",
    "http",
    "import",
    "password",
    "sdk",
    "secret",
    "token",
    "uri",
    "url",
)


class ProviderAdapterErrorCategory(StrEnum):
    """Classify sanitized normalized Provider adapter failure categories."""

    INVALID_REQUEST = "invalid_request"
    UNAVAILABLE = "unavailable"
    THROTTLED = "throttled"
    TIMEOUT = "timeout"
    INTEGRITY = "integrity"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderAdapterRequest:
    """Carry one immutable provider-neutral operation request by exact references.

    Args:
        provider_identity: Exact Provider identity already selected by later composition work.
        configuration_reference: Exact non-secret configuration revision for the Provider.
        operation_identifier: Safe opaque operation contract identifier.
        input_references: Safe opaque input evidence references, never raw payloads.
    """

    provider_identity: ProviderIdentity
    configuration_reference: ProviderConfigurationRef
    operation_identifier: str
    input_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate adapter request evidence without selecting or invoking a Provider.

        Raises:
            ProviderContractValidationError: If request evidence is inconsistent or unsafe.
        """
        if (
            not isinstance(self.provider_identity, ProviderIdentity)
            or not isinstance(self.configuration_reference, ProviderConfigurationRef)
            or self.configuration_reference.provider_identity != self.provider_identity
            or not _valid_identifier(self.operation_identifier)
            or type(self.input_references) is not tuple
            or any(not _valid_identifier(reference) for reference in self.input_references)
        ):
            raise ProviderContractValidationError("invalid provider adapter request")

    def to_canonical_json(self) -> str:
        """Serialize deterministic request references without payload or authority data.

        Returns:
            Canonical JSON containing only provider, configuration, operation, and input references.
        """
        return json.dumps(
            {
                "configuration_revision": self.configuration_reference.revision,
                "input_references": self.input_references,
                "operation_identifier": self.operation_identifier,
                "provider_key": self.provider_identity.key.value,
                "provider_version": self.provider_identity.version.value,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class ProviderAdapterResult:
    """Carry immutable normalized adapter output references without registering outputs.

    Args:
        request: Original normalized request that produced this result.
        output_references: Safe opaque output evidence references.
    """

    request: ProviderAdapterRequest
    output_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate normalized output reference evidence without lifecycle mutation.

        Raises:
            ProviderContractValidationError: If the request or output references are invalid.
        """
        if (
            not isinstance(self.request, ProviderAdapterRequest)
            or type(self.output_references) is not tuple
            or any(not _valid_identifier(reference) for reference in self.output_references)
        ):
            raise ProviderContractValidationError("invalid provider adapter result")


@dataclass(frozen=True, slots=True)
class ProviderAdapterError:
    """Carry one sanitized provider-neutral adapter error.

    Args:
        category: Normalized failure category with no vendor exception type.
        code: Stable safe error code with no raw vendor payload.
    """

    category: ProviderAdapterErrorCategory
    code: str

    def __post_init__(self) -> None:
        """Validate stable safe error facts without wrapping vendor exceptions.

        Raises:
            ProviderContractValidationError: If category or code is invalid.
        """
        if not isinstance(self.category, ProviderAdapterErrorCategory) or not _valid_identifier(
            self.code
        ):
            raise ProviderContractValidationError("invalid provider adapter error")


class ProviderAdapterPort(Protocol):
    """Define the later Atlas adapter dependency boundary without a concrete implementation."""

    def execute_provider_operation(
        self, request: ProviderAdapterRequest
    ) -> ProviderAdapterResult | ProviderAdapterError:
        """Return normalized evidence for a later concrete adapter implementation.

        Args:
            request: Immutable normalized Provider operation request.

        Returns:
            Normalized result references or a sanitized normalized error.
        """


def _valid_identifier(value: object) -> bool:
    """Return whether one bounded opaque adapter identifier is valid."""
    return (
        type(value) is str
        and len(value) <= _MAXIMUM_LENGTH
        and _IDENTIFIER.fullmatch(value) is not None
        and not any(token in value.lower() for token in _FORBIDDEN_TOKENS)
    )
