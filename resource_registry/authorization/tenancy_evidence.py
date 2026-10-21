"""Own the private D6 tenancy-evidence proof, identities, state, clocks, and lifecycle.

Resource Registry is the sole issuer, validator, lifecycle, and cleanup owner of this evidence.
Possession of a proof grants no authority: only Registry validation has that effect. The evidence
is request-scoped, in-process, and memory-only; it is never serialized, persisted, or continued
across processes.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from resource_registry.authorization.tenancy_evidence_port import (
    D6EvidenceRejected,
    RegistryEvidenceConsumer,
    RegistryOwnedPrincipalContext,
    RegistryOwnerKind,
    RegistryPrincipalKind,
    RegistryTenancyFacts,
    RegistryTenancyFactsSource,
    TrustedClock,
)

__all__ = (
    "D6_MAXIMUM_LIFETIME",
    "D6EvidenceRejected",
    "RegistryEvidenceConsumer",
    "RegistryOwnedPrincipalContext",
    "RegistryOwnerKind",
    "RegistryPrincipalKind",
    "RegistryTenancyEvidenceSession",
    "RegistryTenancyEvidenceState",
    "RegistryTenancyFacts",
    "RegistryTenancyFactsSource",
    "TrustedClock",
    "TrustedRegistryTenancyProof",
)

D6_MAXIMUM_LIFETIME: timedelta = timedelta(minutes=5)

_FIXED_MAXIMUM_LIFETIME: timedelta = timedelta(minutes=5)
_CONSTRUCTION_TOKEN = object()
_PROTECTED_REPRESENTATION = "<protected>"


class _ProtectedIdentity:
    """Provide one non-disclosing, non-copyable, non-serializable Registry-private identity."""

    __slots__ = ()

    def __init__(self, construction_token: object = None) -> None:
        """Reject every construction that does not present the private Registry token."""
        if construction_token is not _CONSTRUCTION_TOKEN:
            raise D6EvidenceRejected()

    def __repr__(self) -> str:
        """Return a fixed representation without protected identities."""
        return _PROTECTED_REPRESENTATION

    def __copy__(self) -> _ProtectedIdentity:
        """Reject copying of a protected Registry identity."""
        raise D6EvidenceRejected()

    def __deepcopy__(self, memo: dict[int, object]) -> _ProtectedIdentity:
        """Reject deep copying of a protected Registry identity."""
        raise D6EvidenceRejected()

    def __reduce__(self) -> tuple[object, ...]:
        """Reject serialization of a protected Registry identity."""
        raise D6EvidenceRejected()

    def __reduce_ex__(self, protocol: int) -> tuple[object, ...]:
        """Reject protocol serialization of a protected Registry identity."""
        raise D6EvidenceRejected()


class _SessionIdentity(_ProtectedIdentity):
    """Identify one exact Registry evidence session and its private issuer/service identity."""

    __slots__ = ()


class _EvaluationIdentity(_ProtectedIdentity):
    """Identify the single downstream evaluation bound to one Registry evidence session."""

    __slots__ = ()


class _ProofIdentity(_ProtectedIdentity):
    """Identify one Registry-only proof inside the private lifecycle map."""

    __slots__ = ()


class _StableIssuerDescriptor(_ProtectedIdentity):
    """Identify only the D6 implementation family and version.

    A stable issuer identity proves neither session ownership nor cleanup authority.
    """

    __slots__ = ()


class _SessionCleanupAuthority(_ProtectedIdentity):
    """Authorize Registry-private terminal cleanup for exactly one session.

    The authority is created and retained by the exact Registry session, is never exported, and is
    validated only by object identity.
    """

    __slots__ = ()


_STABLE_ISSUER_DESCRIPTOR = _StableIssuerDescriptor(_CONSTRUCTION_TOKEN)


class TrustedRegistryTenancyProof:
    """Carry one opaque Registry-owned tenancy evidence reference.

    The proof exposes no bindings, no representation, no equality by value, and no reconstruction
    path. Only the exact object issued by its Registry session is ever accepted.
    """

    __slots__ = ()

    def __init__(self, construction_token: object = None) -> None:
        """Reject every construction that does not present the private Registry token."""
        if construction_token is not _CONSTRUCTION_TOKEN:
            raise D6EvidenceRejected()

    def __repr__(self) -> str:
        """Return a fixed representation without protected bindings."""
        return _PROTECTED_REPRESENTATION

    def __copy__(self) -> TrustedRegistryTenancyProof:
        """Reject copying of Registry-owned tenancy evidence."""
        raise D6EvidenceRejected()

    def __deepcopy__(self, memo: dict[int, object]) -> TrustedRegistryTenancyProof:
        """Reject deep copying of Registry-owned tenancy evidence."""
        raise D6EvidenceRejected()

    def __reduce__(self) -> tuple[object, ...]:
        """Reject serialization of Registry-owned tenancy evidence."""
        raise D6EvidenceRejected()

    def __reduce_ex__(self, protocol: int) -> tuple[object, ...]:
        """Reject protocol serialization of Registry-owned tenancy evidence."""
        raise D6EvidenceRejected()


class RegistryTenancyEvidenceState(Enum):
    """Enumerate the complete internal lifecycle for one issued evidence record."""

    ISSUED = "issued"
    VALIDATING = "validating"
    VALIDATED_AND_CONSUMED = "validated_and_consumed"
    INVALIDATED = "invalidated"


@dataclass(slots=True)
class _IssuedTenancyEvidence:
    """Retain one protected in-memory lifecycle record owned by a single Registry session."""

    proof: TrustedRegistryTenancyProof
    proof_identity: _ProofIdentity
    evaluation_identity: _EvaluationIdentity
    session_identity: _SessionIdentity
    issuer_descriptor: _StableIssuerDescriptor
    consumer: RegistryEvidenceConsumer
    facts: RegistryTenancyFacts
    issued_at: datetime
    expires_at: datetime
    monotonic_deadline: float
    last_monotonic_observation: float
    generation: int
    state: RegistryTenancyEvidenceState

    def __repr__(self) -> str:
        """Return a fixed representation without protected bindings."""
        return _PROTECTED_REPRESENTATION


def _create_proof() -> TrustedRegistryTenancyProof:
    """Construct exactly one Registry-owned opaque proof through private issuer authority."""
    return TrustedRegistryTenancyProof(_CONSTRUCTION_TOKEN)


def _is_utc_datetime(value: object) -> bool:
    """Determine whether a timestamp is timezone-aware and normalized to UTC."""
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
    )


def _validated_maximum_lifetime() -> timedelta:
    """Return the fixed five-minute D6 maximum or fail closed on any other value."""
    maximum = D6_MAXIMUM_LIFETIME
    if not isinstance(maximum, timedelta) or maximum != _FIXED_MAXIMUM_LIFETIME:
        raise D6EvidenceRejected()
    return maximum


class RegistryTenancyEvidenceSession:
    """Issue, validate, consume, and terminally clean one evaluation's tenancy evidence.

    The session owns a non-reentrant lock that protects only local Registry state. It provides
    neither database nor distributed serialization, and no authoritative revalidation is performed
    while the lock is held.
    """

    __slots__ = (
        "_cleanup_authority",
        "_clock",
        "_consumer",
        "_evaluation_expires_at",
        "_evaluation_identity",
        "_has_issued",
        "_is_finalized",
        "_lock",
        "_principal_context",
        "_proof_index",
        "_records",
        "_request_expires_at",
        "_session_identity",
        "_tenancy_facts_source",
    )

    def __init__(
        self,
        *,
        consumer: RegistryEvidenceConsumer,
        trusted_principal_context: RegistryOwnedPrincipalContext,
        tenancy_facts_source: RegistryTenancyFactsSource,
        clock: TrustedClock,
        request_expires_at: datetime | None = None,
        evaluation_expires_at: datetime | None = None,
    ) -> None:
        """Initialize one request-scoped Registry-owned evidence session.

        Raises:
            D6EvidenceRejected: If any session dependency is invalid.
        """
        if (
            not isinstance(consumer, RegistryEvidenceConsumer)
            or not isinstance(trusted_principal_context, RegistryOwnedPrincipalContext)
            or not callable(getattr(tenancy_facts_source, "resolve_current_tenancy", None))
            or not callable(getattr(clock, "utc_now", None))
            or not callable(getattr(clock, "monotonic_now", None))
        ):
            raise D6EvidenceRejected()
        self._consumer = consumer
        self._principal_context = trusted_principal_context
        self._tenancy_facts_source = tenancy_facts_source
        self._clock = clock
        self._request_expires_at = request_expires_at
        self._evaluation_expires_at = evaluation_expires_at
        self._session_identity = _SessionIdentity(_CONSTRUCTION_TOKEN)
        self._evaluation_identity = _EvaluationIdentity(_CONSTRUCTION_TOKEN)
        self._cleanup_authority = _SessionCleanupAuthority(_CONSTRUCTION_TOKEN)
        self._records: dict[_ProofIdentity, _IssuedTenancyEvidence] = {}
        self._proof_index: dict[TrustedRegistryTenancyProof, _ProofIdentity] = {}
        self._lock = threading.Lock()
        self._is_finalized = False
        self._has_issued = False

    @classmethod
    def open(
        cls,
        *,
        consumer: RegistryEvidenceConsumer,
        trusted_principal_context: RegistryOwnedPrincipalContext,
        tenancy_facts_source: RegistryTenancyFactsSource,
        clock: TrustedClock,
        request_expires_at: datetime | None = None,
        evaluation_expires_at: datetime | None = None,
    ) -> RegistryTenancyEvidenceSession:
        """Open one Registry-owned session immediately before a single evaluation.

        Args:
            consumer: Downstream evaluation that will obtain this evidence.
            trusted_principal_context: Trusted Registry-owned principal context.
            tenancy_facts_source: Registry-owned authoritative tenancy resolver.
            clock: Trusted UTC and monotonic clock.
            request_expires_at: Optional trusted UTC request bound that may only shorten lifetime.
            evaluation_expires_at: Optional trusted UTC evaluation bound that may only shorten it.

        Returns:
            One request-scoped Registry-owned evidence session with distinct private identities.

        Raises:
            D6EvidenceRejected: If any session dependency is invalid.
        """
        return cls(
            consumer=consumer,
            trusted_principal_context=trusted_principal_context,
            tenancy_facts_source=tenancy_facts_source,
            clock=clock,
            request_expires_at=request_expires_at,
            evaluation_expires_at=evaluation_expires_at,
        )

    def __repr__(self) -> str:
        """Return a representation without protected identities or evidence."""
        return f"RegistryTenancyEvidenceSession({_PROTECTED_REPRESENTATION})"

    def issue(self) -> TrustedRegistryTenancyProof:
        """Re-evaluate current tenancy and publish exactly one opaque proof for this evaluation.

        Returns:
            The exact Registry-owned opaque proof for this single evaluation.

        Raises:
            D6EvidenceRejected: If issuance cannot safely complete for any reason.
        """
        with self._lock:
            if self._is_finalized or self._has_issued:
                raise D6EvidenceRejected()
        maximum_lifetime = _validated_maximum_lifetime()
        facts = self._resolve_current_tenancy()
        issued_at, monotonic_issued_at = self._read_trusted_clocks()
        expires_at = self._effective_expiry(issued_at, maximum_lifetime)
        effective_lifetime = (expires_at - issued_at).total_seconds()
        if not math.isfinite(effective_lifetime) or effective_lifetime <= 0.0:
            raise D6EvidenceRejected()
        proof = self._construct_proof()
        proof_identity = _ProofIdentity(_CONSTRUCTION_TOKEN)
        record = _IssuedTenancyEvidence(
            proof=proof,
            proof_identity=proof_identity,
            evaluation_identity=self._evaluation_identity,
            session_identity=self._session_identity,
            issuer_descriptor=_STABLE_ISSUER_DESCRIPTOR,
            consumer=self._consumer,
            facts=facts,
            issued_at=issued_at,
            expires_at=expires_at,
            monotonic_deadline=monotonic_issued_at + effective_lifetime,
            last_monotonic_observation=monotonic_issued_at,
            generation=0,
            state=RegistryTenancyEvidenceState.ISSUED,
        )
        with self._lock:
            if self._is_finalized or self._has_issued or self._records:
                raise D6EvidenceRejected()
            self._has_issued = True
            self._records[proof_identity] = record
            self._proof_index[proof] = proof_identity
        return proof

    def consume(self, proof: TrustedRegistryTenancyProof) -> None:
        """Validate and terminally consume the exact proof issued by this session.

        The reservation, the authoritative revalidation, and the terminal confirmation form the
        approved two-phase protocol: no authoritative read runs while the local lock is held.

        Args:
            proof: The exact Registry-owned opaque proof presented for consumption.

        Raises:
            D6EvidenceRejected: If consumption is unavailable for any reason.
        """
        record, generation = self._reserve_candidate(proof)
        try:
            facts = self._resolve_current_tenancy()
        except BaseException:
            self._terminalize(record, generation)
            raise
        if facts != record.facts:
            self._terminalize(record, generation)
            raise D6EvidenceRejected()
        self._confirm_consumption(record, generation)

    def _reserve_candidate(
        self, proof: TrustedRegistryTenancyProof
    ) -> tuple[_IssuedTenancyEvidence, int]:
        """Reserve the only candidate for validation under the non-reentrant session lock."""
        with self._lock:
            if self._is_finalized or not isinstance(proof, TrustedRegistryTenancyProof):
                raise D6EvidenceRejected()
            proof_identity = self._proof_index.get(proof)
            if proof_identity is None:
                raise D6EvidenceRejected()
            record = self._records.get(proof_identity)
            if (
                record is None
                or record.proof is not proof
                or record.proof_identity is not proof_identity
                or record.session_identity is not self._session_identity
                or record.evaluation_identity is not self._evaluation_identity
                or record.issuer_descriptor is not _STABLE_ISSUER_DESCRIPTOR
                or record.consumer is not self._consumer
                or record.state is not RegistryTenancyEvidenceState.ISSUED
            ):
                raise D6EvidenceRejected()
            if not self._is_unexpired(record):
                self._invalidate(record)
                raise D6EvidenceRejected()
            record.state = RegistryTenancyEvidenceState.VALIDATING
            record.generation += 1
            return record, record.generation

    def _confirm_consumption(self, record: _IssuedTenancyEvidence, generation: int) -> None:
        """Reacquire the lock and terminalize the reserved candidate after the authoritative read."""
        with self._lock:
            if (
                self._is_finalized
                or record.generation != generation
                or record.state is not RegistryTenancyEvidenceState.VALIDATING
                or self._records.get(record.proof_identity) is not record
            ):
                raise D6EvidenceRejected()
            if not self._is_unexpired(record):
                self._invalidate(record)
                raise D6EvidenceRejected()
            record.state = RegistryTenancyEvidenceState.VALIDATED_AND_CONSUMED
            record.generation += 1
            self._remove(record)

    def _terminalize(self, record: _IssuedTenancyEvidence, generation: int) -> None:
        """Invalidate the reserved candidate unless another terminal outcome already won."""
        with self._lock:
            if (
                record.generation != generation
                or self._records.get(record.proof_identity) is not record
            ):
                return
            self._invalidate(record)

    def _finalize_owned_evidence(self, *, authority: _SessionCleanupAuthority) -> None:
        """Invalidate and remove every remaining record using the exact private authority.

        Args:
            authority: The exact private per-session cleanup authority.

        Raises:
            D6EvidenceRejected: If the authority is not this session's exact authority, or if
                cleanup fails after the required terminal state has been reached.
        """
        with self._lock:
            if self._is_finalized:
                return
            if authority is not self._cleanup_authority:
                raise D6EvidenceRejected()
            for record in self._records.values():
                if record.state in (
                    RegistryTenancyEvidenceState.ISSUED,
                    RegistryTenancyEvidenceState.VALIDATING,
                ):
                    record.state = RegistryTenancyEvidenceState.INVALIDATED
                    record.generation += 1
            try:
                self._clear_protected_bindings()
            except Exception:
                self._records.clear()
                self._proof_index.clear()
            else:
                self._is_finalized = True
                return
        raise D6EvidenceRejected()

    def _clear_protected_bindings(self) -> None:
        """Remove every request-scoped protected reference retained by this session."""
        for record in self._records.values():
            record.proof = None  # type: ignore[assignment]
            record.proof_identity = None  # type: ignore[assignment]
            record.evaluation_identity = None  # type: ignore[assignment]
            record.session_identity = None  # type: ignore[assignment]
            record.issuer_descriptor = None  # type: ignore[assignment]
            record.facts = None  # type: ignore[assignment]
            record.issued_at = None  # type: ignore[assignment]
            record.expires_at = None  # type: ignore[assignment]
            record.monotonic_deadline = 0.0
            record.last_monotonic_observation = 0.0
            record.generation = 0
        self._records.clear()
        self._proof_index.clear()
        for attribute_name in (
            "_cleanup_authority",
            "_clock",
            "_consumer",
            "_evaluation_expires_at",
            "_evaluation_identity",
            "_principal_context",
            "_request_expires_at",
            "_session_identity",
            "_tenancy_facts_source",
        ):
            delattr(self, attribute_name)

    def _invalidate(self, record: _IssuedTenancyEvidence) -> None:
        """Invalidate and remove one record while the session lock is held."""
        record.state = RegistryTenancyEvidenceState.INVALIDATED
        record.generation += 1
        self._remove(record)

    def _remove(self, record: _IssuedTenancyEvidence) -> None:
        """Remove one record and its proof index entry while the session lock is held."""
        self._records.pop(record.proof_identity, None)
        self._proof_index.pop(record.proof, None)

    def _is_unexpired(self, record: _IssuedTenancyEvidence) -> bool:
        """Determine whether both trusted clocks remain strictly before their fixed deadlines."""
        try:
            utc_now, monotonic_now = self._read_trusted_clocks()
        except D6EvidenceRejected:
            return False
        if monotonic_now < record.last_monotonic_observation:
            return False
        record.last_monotonic_observation = monotonic_now
        return utc_now < record.expires_at and monotonic_now < record.monotonic_deadline

    def _effective_expiry(self, issued_at: datetime, maximum_lifetime: timedelta) -> datetime:
        """Return the earliest applicable authorized expiry bound for this evaluation."""
        expires_at = issued_at + maximum_lifetime
        for bound in (self._request_expires_at, self._evaluation_expires_at):
            if bound is None:
                continue
            if not _is_utc_datetime(bound) or bound <= issued_at:
                raise D6EvidenceRejected()
            expires_at = min(expires_at, bound)
        return expires_at

    def _read_trusted_clocks(self) -> tuple[datetime, float]:
        """Read the trusted UTC and monotonic clocks or fail closed on any invalid value."""
        try:
            utc_now = self._clock.utc_now()
            monotonic_now = self._clock.monotonic_now()
        except Exception:
            utc_now = None
            monotonic_now = None
        if not _is_utc_datetime(utc_now):
            raise D6EvidenceRejected()
        if type(monotonic_now) not in (int, float) or not math.isfinite(monotonic_now):
            raise D6EvidenceRejected()
        return utc_now, float(monotonic_now)

    def _resolve_current_tenancy(self) -> RegistryTenancyFacts:
        """Resolve current Registry-owned tenancy facts without holding the local lock."""
        principal_context = self._principal_context
        try:
            facts = self._tenancy_facts_source.resolve_current_tenancy(principal_context)
        except Exception:
            facts = None
        if not isinstance(facts, RegistryTenancyFacts):
            raise D6EvidenceRejected()
        if (
            not _is_strict_positive_integer(facts.principal_id)
            or not _is_strict_positive_integer(facts.organization_id)
            or (
                facts.workspace_id is not None
                and not _is_strict_positive_integer(facts.workspace_id)
            )
            or not _is_strict_positive_integer(facts.owner_id)
            or not isinstance(facts.principal_kind, RegistryPrincipalKind)
        ):
            raise D6EvidenceRejected()
        if (
            facts.principal_id != principal_context.principal_id
            or facts.organization_id != principal_context.organization_id
            or facts.workspace_id != principal_context.workspace_id
            or facts.principal_kind is not principal_context.principal_kind
        ):
            raise D6EvidenceRejected()
        return facts

    def _construct_proof(self) -> TrustedRegistryTenancyProof:
        """Construct one opaque proof and convert any construction failure to a rejection."""
        try:
            proof = _create_proof()
        except Exception:
            proof = None
        if not isinstance(proof, TrustedRegistryTenancyProof):
            raise D6EvidenceRejected()
        return proof


def _is_strict_positive_integer(value: object) -> bool:
    """Determine whether one retained D6 integer identity excludes boolean values."""
    return type(value) is int and value > 0
