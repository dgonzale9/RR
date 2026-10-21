"""Define pure immutable expiring Provider health-observation contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .contracts import ProviderIdentity
from .errors import ProviderHealthObservationValidationError

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
    "namespace",
    "password",
    "policy",
    "principal",
    "raw",
    "sdk",
    "secret",
    "tenant",
    "token",
    "uri",
    "url",
    "vendor",
)


class ProviderHealthObservationCategory(StrEnum):
    """Classify one sanitized time-bounded Provider health observation."""

    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderHealthObservationScope:
    """Identify one exact opaque Provider operational observation scope.

    Args:
        provider_identity: Exact Provider version observed.
        implementation_reference: Exact opaque Provider implementation identifier.
        deployment_reference: Exact opaque deployment scope identifier.
        region_reference: Exact opaque region scope identifier.
    """

    provider_identity: ProviderIdentity
    implementation_reference: str
    deployment_reference: str
    region_reference: str

    def __post_init__(self) -> None:
        """Validate scope without resolving endpoints or loading configuration.

        Raises:
            ProviderHealthObservationValidationError: If scope facts are unsafe or malformed.
        """
        if (
            not isinstance(self.provider_identity, ProviderIdentity)
            or not _valid_reference(self.implementation_reference)
            or not _valid_reference(self.deployment_reference)
            or not _valid_reference(self.region_reference)
        ):
            raise ProviderHealthObservationValidationError(
                "invalid provider health observation scope"
            )


@dataclass(frozen=True, slots=True)
class ProviderHealthObservation:
    """Carry one immutable expiring sanitized Provider health observation.

    Args:
        scope: Exact opaque Provider operational scope.
        category: Sanitized observed operational category.
        observed_at: Time at which the observation was made.
        expires_at: Required time after which the observation is no longer current.
        reason_code: Sanitized bounded observation reason category.
    """

    scope: ProviderHealthObservationScope
    category: ProviderHealthObservationCategory
    observed_at: datetime
    expires_at: datetime
    reason_code: str

    def __post_init__(self) -> None:
        """Validate expiring observation evidence without computing availability.

        Raises:
            ProviderHealthObservationValidationError: If observation evidence is malformed.
        """
        if (
            not isinstance(self.scope, ProviderHealthObservationScope)
            or not isinstance(self.category, ProviderHealthObservationCategory)
            or not _is_timezone_aware(self.observed_at)
            or not _is_timezone_aware(self.expires_at)
            or self.expires_at <= self.observed_at
            or not _valid_reference(self.reason_code)
        ):
            raise ProviderHealthObservationValidationError("invalid provider health observation")


@dataclass(frozen=True, slots=True)
class ProviderHealthObservationSet:
    """Hold immutable exact Provider health observations without freshness resolution.

    Args:
        observations: Exact expiring Provider health observations.
    """

    observations: tuple[ProviderHealthObservation, ...]

    def __post_init__(self) -> None:
        """Reject malformed or duplicate scope/time observation facts.

        Raises:
            ProviderHealthObservationValidationError: If observations are invalid or duplicate.
        """
        if type(self.observations) is not tuple:
            raise ProviderHealthObservationValidationError("invalid provider health observations")
        observations_by_scope_and_time: set[tuple[ProviderHealthObservationScope, datetime]] = set()
        for observation in self.observations:
            if not isinstance(observation, ProviderHealthObservation):
                raise ProviderHealthObservationValidationError(
                    "invalid provider health observation"
                )
            scope_and_time = (observation.scope, observation.observed_at)
            if scope_and_time in observations_by_scope_and_time:
                raise ProviderHealthObservationValidationError(
                    "duplicate provider health observation"
                )
            observations_by_scope_and_time.add(scope_and_time)


def _is_timezone_aware(value: object) -> bool:
    """Return whether a datetime has a resolvable timezone offset."""
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _valid_reference(value: object) -> bool:
    """Return whether a bounded opaque safe observation reference is valid."""
    return (
        type(value) is str
        and len(value) <= _MAXIMUM_LENGTH
        and _IDENTIFIER.fullmatch(value) is not None
        and value not in {"current", "latest"}
        and not any(token in value.lower() for token in _FORBIDDEN_TOKENS)
    )
