"""Define pure non-secret Provider configuration contracts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .contracts import ProviderIdentity
from .errors import ProviderContractValidationError

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]*\Z", re.ASCII)
_SETTING_VALUE = re.compile(r"[a-z0-9][a-z0-9_.-]*\Z", re.ASCII)
_MAXIMUM_LENGTH = 256
_FORBIDDEN_SETTING_TOKENS = (
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


@dataclass(frozen=True, slots=True)
class OpaqueSecretReference:
    """Carry an opaque secret-manager reference without a secret value.

    Args:
        identifier: Stable opaque identifier resolved only by later secret-management work.
    """

    identifier: str

    def __post_init__(self) -> None:
        """Validate bounded opaque reference evidence without fetching a secret.

        Raises:
            ProviderContractValidationError: If the opaque identifier is malformed.
        """
        if not _valid_identifier(self.identifier):
            raise ProviderContractValidationError("invalid opaque secret reference")


@dataclass(frozen=True, slots=True)
class ProviderConfigurationRef:
    """Identify one exact externalized non-secret Provider configuration revision.

    Args:
        provider_identity: Exact Provider identity for this configuration revision.
        schema_reference: Safe identifier for the configuration schema contract.
        scope_reference: Opaque allowed-scope identifier without tenant authorization authority.
        revision: Exact immutable configuration revision identifier.
    """

    provider_identity: ProviderIdentity
    schema_reference: str
    scope_reference: str
    revision: str

    def __post_init__(self) -> None:
        """Validate exact configuration identity without loading configuration.

        Raises:
            ProviderContractValidationError: If identity or reference evidence is malformed.
        """
        if (
            not isinstance(self.provider_identity, ProviderIdentity)
            or not _valid_identifier(self.schema_reference)
            or not _valid_identifier(self.scope_reference)
            or not _valid_identifier(self.revision)
        ):
            raise ProviderContractValidationError("invalid provider configuration reference")


@dataclass(frozen=True, slots=True)
class ProviderConfiguration:
    """Carry immutable non-secret settings and opaque secret references.

    Args:
        reference: Exact configuration revision metadata.
        settings: Bounded non-secret configuration setting pairs.
        secret_references: Opaque references to secrets held outside this contract.
    """

    reference: ProviderConfigurationRef
    settings: tuple[tuple[str, str], ...] = ()
    secret_references: tuple[OpaqueSecretReference, ...] = ()

    def __post_init__(self) -> None:
        """Validate bounded safe configuration values without exposing secret or endpoint data.

        Raises:
            ProviderContractValidationError: If settings or opaque references are invalid.
        """
        if not isinstance(self.reference, ProviderConfigurationRef):
            raise ProviderContractValidationError("invalid provider configuration reference")
        if (
            type(self.settings) is not tuple
            or any(
                type(setting) is not tuple
                or len(setting) != 2
                or not _valid_safe_setting(setting[0], setting[1])
                for setting in self.settings
            )
            or len({key for key, _ in self.settings}) != len(self.settings)
        ):
            raise ProviderContractValidationError("invalid provider configuration settings")
        if (
            type(self.secret_references) is not tuple
            or any(
                not isinstance(reference, OpaqueSecretReference)
                for reference in self.secret_references
            )
            or len(set(self.secret_references)) != len(self.secret_references)
        ):
            raise ProviderContractValidationError("invalid provider configuration secrets")

    def to_canonical_json(self) -> str:
        """Serialize only non-secret configuration facts deterministically.

        Returns:
            Canonical JSON containing settings and opaque references, never secret values.
        """
        return json.dumps(
            {
                "reference": {
                    "provider_key": self.reference.provider_identity.key.value,
                    "provider_version": {
                        "scheme": self.reference.provider_identity.version.scheme,
                        "value": self.reference.provider_identity.version.value,
                    },
                    "revision": self.reference.revision,
                    "schema_reference": self.reference.schema_reference,
                    "scope_reference": self.reference.scope_reference,
                },
                "secret_references": tuple(
                    reference.identifier for reference in self.secret_references
                ),
                "settings": tuple(sorted(self.settings)),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


def _valid_identifier(value: object) -> bool:
    """Return whether a bounded opaque configuration identifier is valid."""
    return (
        type(value) is str
        and len(value) <= _MAXIMUM_LENGTH
        and _IDENTIFIER.fullmatch(value) is not None
    )


def _valid_safe_setting(key: object, value: object) -> bool:
    """Return whether one non-secret non-endpoint setting is bounded."""
    if type(key) is not str or type(value) is not str:
        return False
    return (
        _valid_identifier(key)
        and len(value) <= _MAXIMUM_LENGTH
        and _SETTING_VALUE.fullmatch(value) is not None
        and not any(
            token in key.lower() or token in value.lower() for token in _FORBIDDEN_SETTING_TOKENS
        )
    )
