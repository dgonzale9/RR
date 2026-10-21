"""Define pure deterministic Router Provider-selection evidence contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from resource_registry.provider_contracts import (
    CertifiedProviderCapabilityBinding,
    ProviderHealthObservation,
)

from .contracts import ExecutionRequest
from .errors import ProviderSelectionValidationError

_CHECKSUM = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
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
    "principal",
    "sdk",
    "secret",
    "tenant",
    "token",
    "uri",
    "url",
)


class ProviderSelectionRejectionCategory(StrEnum):
    """Classify sanitized non-selected candidate evidence categories."""

    CONTRACT_MISMATCH = "contract_mismatch"
    DUPLICATE_CANDIDATE = "duplicate_candidate"
    STALE_HEALTH = "stale_health"
    UNSPECIFIED = "unspecified"


@dataclass(frozen=True, slots=True)
class ProviderSelectionPolicyRef:
    """Identify one exact opaque Router-owned candidate-ordering policy revision.

    Args:
        value: Exact safe policy revision identifier without policy content.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate policy revision identity without evaluating policy.

        Raises:
            ProviderSelectionValidationError: If the policy reference is malformed.
        """
        if not _valid_reference(self.value):
            raise ProviderSelectionValidationError("invalid provider selection policy reference")


@dataclass(frozen=True, slots=True)
class RouterProviderCandidate:
    """Bind exact certified Provider and fresh scoped health evidence.

    Args:
        certification_binding: Exact certified Provider Capability binding.
        health_observation: Exact scoped Provider health observation.
    """

    certification_binding: CertifiedProviderCapabilityBinding
    health_observation: ProviderHealthObservation

    def __post_init__(self) -> None:
        """Validate exact Provider and implementation alignment without availability calculation.

        Raises:
            ProviderSelectionValidationError: If candidate evidence is malformed or mismatched.
        """
        if not isinstance(
            self.certification_binding, CertifiedProviderCapabilityBinding
        ) or not isinstance(self.health_observation, ProviderHealthObservation):
            raise ProviderSelectionValidationError("invalid Router Provider candidate")
        certification = self.certification_binding.certification_record
        implementation = self.certification_binding.conformance_declaration.implementation_ref.value
        if (
            self.health_observation.scope.provider_identity != certification.provider_identity
            or self.health_observation.scope.implementation_reference != implementation
        ):
            raise ProviderSelectionValidationError("mismatched Router Provider candidate evidence")


@dataclass(frozen=True, slots=True)
class RouterProviderCandidateSet:
    """Carry Router-policy-provided exact Provider candidates in immutable explicit order.

    Args:
        candidates: Nonempty explicitly ordered exact candidate evidence.
    """

    candidates: tuple[RouterProviderCandidate, ...]

    def __post_init__(self) -> None:
        """Reject malformed or duplicate candidates without sorting or ranking them.

        Raises:
            ProviderSelectionValidationError: If candidates are invalid, empty, or duplicate.
        """
        if type(self.candidates) is not tuple or not self.candidates:
            raise ProviderSelectionValidationError("invalid Router Provider candidates")
        identities: set[tuple[object, str]] = set()
        for candidate in self.candidates:
            if not isinstance(candidate, RouterProviderCandidate):
                raise ProviderSelectionValidationError("invalid Router Provider candidate")
            identity = (
                candidate.certification_binding.certification_record.provider_identity,
                candidate.certification_binding.conformance_declaration.implementation_ref.value,
            )
            if identity in identities:
                raise ProviderSelectionValidationError("duplicate Router Provider candidate")
            identities.add(identity)


@dataclass(frozen=True, slots=True)
class RouterProviderSelectionEvidence:
    """Carry immutable deterministic Router selection evidence without invocation.

    Args:
        request: Exact execution request associated with this selection evidence.
        policy_reference: Exact opaque Router-owned ordering policy revision.
        selection_observed_at: Time at which supplied health evidence was assessed as fresh.
        candidate_set: Explicitly ordered exact candidate evidence.
        selected_candidate: Required first candidate in the supplied Router-policy order.
        rejection_categories: Sanitized non-selected candidate evidence categories.
        checksum: Deterministic checksum over safe selection evidence.
    """

    request: ExecutionRequest
    policy_reference: ProviderSelectionPolicyRef
    selection_observed_at: datetime
    candidate_set: RouterProviderCandidateSet
    selected_candidate: RouterProviderCandidate
    rejection_categories: tuple[ProviderSelectionRejectionCategory, ...]
    checksum: str

    def __post_init__(self) -> None:
        """Validate deterministic selection evidence without policy or authorization evaluation.

        Raises:
            ProviderSelectionValidationError: If selection evidence is unsafe or inconsistent.
        """
        if (
            not isinstance(self.request, ExecutionRequest)
            or not isinstance(self.policy_reference, ProviderSelectionPolicyRef)
            or not _is_timezone_aware(self.selection_observed_at)
            or not isinstance(self.candidate_set, RouterProviderCandidateSet)
            or not isinstance(self.selected_candidate, RouterProviderCandidate)
            or type(self.rejection_categories) is not tuple
            or any(
                not isinstance(category, ProviderSelectionRejectionCategory)
                for category in self.rejection_categories
            )
            or len(set(self.rejection_categories)) != len(self.rejection_categories)
            or type(self.checksum) is not str
            or _CHECKSUM.fullmatch(self.checksum) is None
        ):
            raise ProviderSelectionValidationError("invalid Router Provider selection evidence")
        if self.selected_candidate != self.candidate_set.candidates[0]:
            raise ProviderSelectionValidationError(
                "Router Provider selection is not first candidate"
            )
        for candidate in self.candidate_set.candidates:
            if (
                candidate.certification_binding.conformance_declaration.contract_ref
                != self.request.contract_ref
            ):
                raise ProviderSelectionValidationError(
                    "Router Provider candidate contract mismatch"
                )
            observation = candidate.health_observation
            if not observation.observed_at <= self.selection_observed_at < observation.expires_at:
                raise ProviderSelectionValidationError("stale Router Provider health observation")
        if self.checksum != _calculate_selection_checksum(
            self.request,
            self.policy_reference,
            self.selection_observed_at,
            self.candidate_set,
            self.rejection_categories,
        ):
            raise ProviderSelectionValidationError("invalid Router Provider selection checksum")


def build_router_provider_selection_evidence(
    request: ExecutionRequest,
    policy_reference: ProviderSelectionPolicyRef,
    selection_observed_at: datetime,
    candidate_set: RouterProviderCandidateSet,
    rejection_categories: tuple[ProviderSelectionRejectionCategory, ...] = (),
) -> RouterProviderSelectionEvidence:
    """Build deterministic Router selection evidence from policy-ordered candidates.

    Args:
        request: Exact execution request associated with selection evidence.
        policy_reference: Exact opaque Router-owned ordering policy revision.
        selection_observed_at: Time at which supplied health evidence is assessed as fresh.
        candidate_set: Explicitly ordered exact candidate evidence.
        rejection_categories: Sanitized non-selected candidate evidence categories.

    Returns:
        Immutable evidence selecting the first supplied candidate with a deterministic checksum.

    Raises:
        ProviderSelectionValidationError: If supplied selection evidence is inconsistent.
    """
    if not isinstance(candidate_set, RouterProviderCandidateSet):
        raise ProviderSelectionValidationError("invalid Router Provider candidates")
    checksum = _calculate_selection_checksum(
        request,
        policy_reference,
        selection_observed_at,
        candidate_set,
        rejection_categories,
    )
    return RouterProviderSelectionEvidence(
        request,
        policy_reference,
        selection_observed_at,
        candidate_set,
        candidate_set.candidates[0],
        rejection_categories,
        checksum,
    )


def _calculate_selection_checksum(
    request: ExecutionRequest,
    policy_reference: ProviderSelectionPolicyRef,
    selection_observed_at: datetime,
    candidate_set: RouterProviderCandidateSet,
    rejection_categories: tuple[ProviderSelectionRejectionCategory, ...],
) -> str:
    """Calculate a deterministic checksum from safe Router selection evidence."""
    material = json.dumps(
        {
            "candidates": tuple(
                _serialize_candidate(candidate) for candidate in candidate_set.candidates
            ),
            "policy_reference": policy_reference.value,
            "rejection_categories": tuple(category.value for category in rejection_categories),
            "request": json.loads(request.to_canonical_json()),
            "selection_observed_at": selection_observed_at.isoformat(),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _serialize_candidate(candidate: RouterProviderCandidate) -> dict[str, str]:
    """Serialize safe exact candidate evidence without runtime Provider details."""
    certification = candidate.certification_binding.certification_record
    declaration = candidate.certification_binding.conformance_declaration
    observation = candidate.health_observation
    return {
        "certification_evidence": certification.evidence_reference.value,
        "health_category": observation.category.value,
        "health_expires_at": observation.expires_at.isoformat(),
        "health_observed_at": observation.observed_at.isoformat(),
        "health_reason_code": observation.reason_code,
        "implementation_reference": declaration.implementation_ref.value,
        "provider_key": certification.provider_identity.key.value,
        "provider_version": certification.provider_identity.version.value,
    }


def _is_timezone_aware(value: object) -> bool:
    """Return whether a datetime has a resolvable timezone offset."""
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _valid_reference(value: object) -> bool:
    """Return whether a bounded exact safe policy reference is valid."""
    return (
        type(value) is str
        and len(value) <= _MAXIMUM_LENGTH
        and _IDENTIFIER.fullmatch(value) is not None
        and value not in {"current", "latest"}
        and not any(token in value.lower() for token in _FORBIDDEN_TOKENS)
    )
