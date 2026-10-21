"""Define pure immutable Provider identity and safe descriptor contracts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum

from .errors import ProviderContractValidationError

_KEY = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+\Z", re.ASCII)
_SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]*\Z", re.ASCII)
_MAXIMUM_LENGTH = 256


class ProviderFunctionalClassification(StrEnum):
    """Classify one Provider's technical responsibility."""

    STORAGE = "storage"
    SEARCH = "search"
    EXECUTION = "execution"
    TRANSFORMATION = "transformation"
    RENDERING = "rendering"
    AI = "ai"
    AUTHENTICATION = "authentication"
    NOTIFICATION = "notification"
    EXTERNAL_SERVICE = "external_service"


class ProviderOriginClassification(StrEnum):
    """Classify one Provider's deployment origin."""

    INTERNAL = "internal"
    PLUGIN = "plugin"
    EXTERNAL = "external"


class ProviderLifecycleStatus(StrEnum):
    """Classify the governed lifecycle of one exact Provider version."""

    DRAFT = "draft"
    TESTING = "testing"
    CERTIFIED = "certified"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"
    RETIRED = "retired"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class ProviderKey:
    """Identify one stable source-qualified Provider identity."""

    value: str

    def __post_init__(self) -> None:
        """Validate the exact lower-case Provider identity."""
        if (
            type(self.value) is not str
            or len(self.value) > _MAXIMUM_LENGTH
            or _KEY.fullmatch(self.value) is None
        ):
            raise ProviderContractValidationError("invalid provider key")


@dataclass(frozen=True, slots=True)
class ProviderVersion:
    """Identify one exact immutable Provider release or external API version."""

    scheme: str
    value: str

    def __post_init__(self) -> None:
        """Validate exact version evidence without accepting selectors or latest aliases."""
        if not _valid_identifier(self.scheme) or not _valid_value(self.value):
            raise ProviderContractValidationError("invalid provider version")
        if self.value.lower() in {"latest", "current"} or any(
            token in self.value for token in ("*", "^", "~", ">", "<")
        ):
            raise ProviderContractValidationError("invalid provider version")
        if self.scheme == "semver" and _SEMVER.fullmatch(self.value) is None:
            raise ProviderContractValidationError("invalid provider version")


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    """Bind one exact Provider key and immutable version."""

    key: ProviderKey
    version: ProviderVersion

    def __post_init__(self) -> None:
        """Validate the exact Provider identity components."""
        if not isinstance(self.key, ProviderKey) or not isinstance(self.version, ProviderVersion):
            raise ProviderContractValidationError("invalid provider identity")


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Declare safe immutable Provider facts without runtime authority or secrets."""

    identity: ProviderIdentity
    functional_classifications: tuple[ProviderFunctionalClassification, ...]
    origin_classification: ProviderOriginClassification
    lifecycle_status: ProviderLifecycleStatus
    implementation_identifiers: tuple[str, ...] = ()
    adapter_contract_reference: str | None = None
    configuration_schema_reference: str | None = None
    provenance_reference: str | None = None
    operational_traits: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate safe declarative descriptor fields without loading a Provider."""
        if not isinstance(self.identity, ProviderIdentity):
            raise ProviderContractValidationError("invalid provider descriptor identity")
        if (
            type(self.functional_classifications) is not tuple
            or not self.functional_classifications
            or any(
                not isinstance(value, ProviderFunctionalClassification)
                for value in self.functional_classifications
            )
            or len(set(self.functional_classifications)) != len(self.functional_classifications)
        ):
            raise ProviderContractValidationError("invalid provider descriptor classifications")
        if not isinstance(
            self.origin_classification, ProviderOriginClassification
        ) or not isinstance(self.lifecycle_status, ProviderLifecycleStatus):
            raise ProviderContractValidationError("invalid provider descriptor classification")
        if (
            type(self.implementation_identifiers) is not tuple
            or any(not _valid_identifier(value) for value in self.implementation_identifiers)
            or len(set(self.implementation_identifiers)) != len(self.implementation_identifiers)
        ):
            raise ProviderContractValidationError("invalid provider descriptor implementations")
        references = (
            self.adapter_contract_reference,
            self.configuration_schema_reference,
            self.provenance_reference,
        )
        if any(value is not None and not _valid_identifier(value) for value in references):
            raise ProviderContractValidationError("invalid provider descriptor reference")
        if (
            type(self.operational_traits) is not tuple
            or any(not _valid_identifier(value) for value in self.operational_traits)
            or len(set(self.operational_traits)) != len(self.operational_traits)
        ):
            raise ProviderContractValidationError("invalid provider descriptor traits")

    def to_canonical_json(self) -> str:
        """Serialize safe descriptor facts into deterministic canonical JSON."""
        return json.dumps(
            {
                "adapter_contract_reference": self.adapter_contract_reference,
                "configuration_schema_reference": self.configuration_schema_reference,
                "functional_classifications": tuple(
                    value.value for value in self.functional_classifications
                ),
                "identity": {
                    "key": self.identity.key.value,
                    "version": {
                        "scheme": self.identity.version.scheme,
                        "value": self.identity.version.value,
                    },
                },
                "implementation_identifiers": self.implementation_identifiers,
                "lifecycle_status": self.lifecycle_status.value,
                "operational_traits": self.operational_traits,
                "origin_classification": self.origin_classification.value,
                "provenance_reference": self.provenance_reference,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


def _valid_identifier(value: object) -> bool:
    """Return whether one safe descriptor identifier is bounded and lower-case."""
    return (
        type(value) is str
        and len(value) <= _MAXIMUM_LENGTH
        and _IDENTIFIER.fullmatch(value) is not None
    )


def _valid_value(value: object) -> bool:
    """Return whether one exact version value is bounded and nonblank."""
    return type(value) is str and bool(value.strip()) and len(value) <= _MAXIMUM_LENGTH
