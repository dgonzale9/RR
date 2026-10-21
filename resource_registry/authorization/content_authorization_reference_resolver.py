"""Manage invocation-scoped opaque-reference lifecycle without application dependencies."""

from __future__ import annotations

import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Generic, Protocol, TypeVar, cast, runtime_checkable

from resource_registry.registry_core import OpaqueReference, OwnerDescriptor

__all__ = (
    "CandidateBoundPayload",
    "CandidateBoundPayloadFactory",
    "ConsumedActiveReference",
    "ContentAuthorizationReferenceBinding",
    "ContentAuthorizationReferenceRejected",
    "ContentAuthorizationReferenceResolver",
    "ContentAuthorizationReferenceState",
    "SingleAttemptConsumptionBinding",
)

_HANDLE_PATTERN = re.compile(r"[0-9a-f]{64}")
_MAXIMUM_LIFETIME = timedelta(minutes=5)

PayloadT = TypeVar("PayloadT")


@dataclass(frozen=True, slots=True)
class ConsumedActiveReference(Generic[PayloadT]):
    """Return the exact retained payload and lifecycle binding after one winning consumption.

    Args:
        payload: Exact protected payload retained when the reference was registered.
        binding: Exact neutral binding registered with the reference.
    """

    payload: PayloadT
    binding: ContentAuthorizationReferenceBinding


@runtime_checkable
class CandidateBoundPayloadFactory(Protocol[PayloadT]):
    """Build a protected payload for one private Registry-generated candidate."""

    def build_payload(
        self, candidate: OpaqueReference
    ) -> tuple[ContentAuthorizationReferenceBinding, PayloadT]:
        """Build one neutral binding and protected payload for the exact candidate."""

    def is_payload_bound_to_candidate(self, payload: PayloadT, candidate: OpaqueReference) -> bool:
        """Determine whether one payload retains the exact candidate identity."""


@runtime_checkable
class CandidateBoundPayload(Protocol):
    """Expose the exact opaque candidate binding of a neutral protected payload."""

    @property
    def opaque_reference_binding(self) -> OpaqueReference:
        """Return the exact private Registry candidate retained by the payload."""


class ContentAuthorizationReferenceRejected(ValueError):  # noqa: N818
    """Report one non-disclosing opaque-reference lifecycle rejection."""

    def __init__(self) -> None:
        """Initialize the stable rejection without protected details."""
        super().__init__("content authorization reference unavailable")


class ContentAuthorizationReferenceState(Enum):
    """Enumerate the complete internal lifecycle for one registered reference."""

    REGISTERED_UNPUBLISHED = "registered_unpublished"
    ACTIVE = "active"
    CONSUMED = "consumed"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True)
class SingleAttemptConsumptionBinding:
    """Bind one decision, invocation, and positive attempt without exposing their values."""

    decision_id: str
    invocation_id: str
    attempt_number: int

    def __post_init__(self) -> None:
        """Validate exact, immutable components for one consumption attempt."""
        if (
            not _is_non_blank_string(self.decision_id)
            or not _is_non_blank_string(self.invocation_id)
            or type(self.attempt_number) is not int
            or self.attempt_number <= 0
        ):
            raise ValueError("invalid single-attempt consumption binding")

    def __repr__(self) -> str:
        """Return a representation without protected lifecycle identities."""
        return "SingleAttemptConsumptionBinding(<protected>)"


@dataclass(frozen=True, slots=True)
class ContentAuthorizationReferenceBinding:
    """Bind one opaque reference to immutable, resolver-neutral lifecycle context.

    Args:
        decision_id: Trusted decision identity for this lifecycle attempt.
        invocation_id: Trusted synchronous invocation identity.
        attempt_number: One-based attempt ordinal.
        issued_at: Trusted UTC issuance timestamp.
        expires_at: Trusted UTC expiration timestamp.
        invocation_expires_at: Optional trusted UTC invocation deadline.
        single_attempt_consumption_binding: Exact immutable binding for this attempt.
        protected_payload_reference: Opaque identity of immutable protected application payload.

    Raises:
        ValueError: If a lifecycle binding is incomplete or temporally invalid.
    """

    decision_id: str
    invocation_id: str
    attempt_number: int
    issued_at: datetime
    expires_at: datetime
    invocation_expires_at: datetime | None
    single_attempt_consumption_binding: SingleAttemptConsumptionBinding
    protected_payload_reference: OpaqueReference

    def __post_init__(self) -> None:
        """Validate immutable resolver-neutral bindings and trusted UTC chronology."""
        if (
            not _is_non_blank_string(self.decision_id)
            or not _is_non_blank_string(self.invocation_id)
            or type(self.attempt_number) is not int
            or self.attempt_number <= 0
            or not isinstance(self.protected_payload_reference, OpaqueReference)
            or not _is_utc_datetime(self.issued_at)
            or not _is_utc_datetime(self.expires_at)
            or self.issued_at >= self.expires_at
            or (
                self.invocation_expires_at is not None
                and not _is_utc_datetime(self.invocation_expires_at)
            )
            or not isinstance(
                self.single_attempt_consumption_binding, SingleAttemptConsumptionBinding
            )
            or self.single_attempt_consumption_binding.decision_id != self.decision_id
            or self.single_attempt_consumption_binding.invocation_id != self.invocation_id
            or self.single_attempt_consumption_binding.attempt_number != self.attempt_number
        ):
            raise ValueError("invalid content authorization reference binding")


@dataclass(slots=True)
class _ReferenceRecord:
    """Retain one protected in-memory lifecycle record owned by the resolver."""

    reference: OpaqueReference
    binding: ContentAuthorizationReferenceBinding
    decision_id: str
    invocation_id: str
    attempt_number: int
    single_attempt_consumption_binding: SingleAttemptConsumptionBinding
    monotonic_deadline: float
    state: ContentAuthorizationReferenceState
    payload: CandidateBoundPayload | None = None


class ContentAuthorizationReferenceResolver:
    """Issue, activate, consume, and finalize invocation-local opaque references.

    Args:
        owner: Registry owner recorded on issued opaque references.
        random_bytes_source: Source of exactly 32 cryptographically secure bytes.
        utc_clock: Trusted server UTC clock.
        monotonic_clock: Trusted process-local monotonic clock.

    Raises:
        ValueError: If construction dependencies are invalid.
    """

    def __init__(
        self,
        owner: OwnerDescriptor,
        random_bytes_source: Callable[[int], bytes] = secrets.token_bytes,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize one isolated, in-memory resolver scope."""
        if (
            not isinstance(owner, OwnerDescriptor)
            or not callable(random_bytes_source)
            or not callable(utc_clock)
            or not callable(monotonic_clock)
        ):
            raise ValueError("invalid content authorization reference resolver")
        self._owner = owner
        self._random_bytes_source = random_bytes_source
        self._utc_clock = utc_clock
        self._monotonic_clock = monotonic_clock
        self._records: dict[str, _ReferenceRecord] = {}
        self._lock = threading.RLock()
        self._is_finalized = False

    def register_reference(self, binding: ContentAuthorizationReferenceBinding) -> OpaqueReference:
        """Register one unpublishable opaque reference after lifecycle validation.

        Args:
            binding: Immutable trusted lifecycle context to bind to the reference.

        Returns:
            The private Registry-issued opaque reference in its unpublished state.

        Raises:
            ContentAuthorizationReferenceRejected: If issuance cannot safely complete.
        """
        with self._lock:
            self._reject_if_finalized()
            if not isinstance(binding, ContentAuthorizationReferenceBinding):
                raise ContentAuthorizationReferenceRejected()
            utc_now, monotonic_now = self._read_trusted_clocks()
            remaining = binding.expires_at - utc_now
            invocation_remaining = self._get_invocation_remaining(binding, utc_now)
            if remaining <= timedelta(0):
                raise ContentAuthorizationReferenceRejected()
            effective_remaining = min(remaining, _MAXIMUM_LIFETIME, invocation_remaining)
            if effective_remaining <= timedelta(0):
                raise ContentAuthorizationReferenceRejected()
            handle = self._generate_handle()
            if handle in self._records:
                raise ContentAuthorizationReferenceRejected()
            reference = OpaqueReference(self._owner, handle)
            self._records[handle] = _ReferenceRecord(
                reference,
                binding,
                binding.decision_id,
                binding.invocation_id,
                binding.attempt_number,
                binding.single_attempt_consumption_binding,
                monotonic_now + effective_remaining.total_seconds(),
                ContentAuthorizationReferenceState.REGISTERED_UNPUBLISHED,
            )
            return reference

    def register_reference_with_payload_factory(
        self,
        payload_factory: CandidateBoundPayloadFactory[PayloadT],
    ) -> tuple[OpaqueReference, PayloadT, ContentAuthorizationReferenceBinding]:
        """Register one payload built only for one private generated candidate.

        Args:
            payload_factory: Neutral factory that binds protected payload to the candidate.

        Returns:
            The unpublished reference, exact protected payload, and its neutral binding.

        Raises:
            ContentAuthorizationReferenceRejected: If any generation, binding, or registration check fails.
        """
        with self._lock:
            self._reject_if_finalized()
            if not isinstance(payload_factory, CandidateBoundPayloadFactory):
                raise ContentAuthorizationReferenceRejected()
            handle = self._generate_handle()
            if handle in self._records:
                raise ContentAuthorizationReferenceRejected()
            candidate = OpaqueReference(self._owner, handle)
            try:
                binding, payload = payload_factory.build_payload(candidate)
            except Exception as error:
                raise ContentAuthorizationReferenceRejected() from error
            if (
                not isinstance(binding, ContentAuthorizationReferenceBinding)
                or not isinstance(payload, CandidateBoundPayload)
                or payload.opaque_reference_binding is not candidate
                or not (payload_factory.is_payload_bound_to_candidate(payload, candidate))
            ):
                raise ContentAuthorizationReferenceRejected()
            utc_now, monotonic_now = self._read_trusted_clocks()
            remaining = binding.expires_at - utc_now
            invocation_remaining = self._get_invocation_remaining(binding, utc_now)
            effective_remaining = min(remaining, _MAXIMUM_LIFETIME, invocation_remaining)
            if effective_remaining <= timedelta(0):
                raise ContentAuthorizationReferenceRejected()
            self._records[handle] = _ReferenceRecord(
                candidate,
                binding,
                binding.decision_id,
                binding.invocation_id,
                binding.attempt_number,
                binding.single_attempt_consumption_binding,
                monotonic_now + effective_remaining.total_seconds(),
                ContentAuthorizationReferenceState.REGISTERED_UNPUBLISHED,
                payload,
            )
            return candidate, payload, binding

    def invalidate_reference(
        self, reference: OpaqueReference, binding: ContentAuthorizationReferenceBinding
    ) -> None:
        """Invalidate and remove one nonterminal reference after issuance failure."""
        with self._lock:
            record = self._find_record(reference, binding)
            if record.state not in {
                ContentAuthorizationReferenceState.REGISTERED_UNPUBLISHED,
                ContentAuthorizationReferenceState.ACTIVE,
            }:
                raise ContentAuthorizationReferenceRejected()
            record.state = ContentAuthorizationReferenceState.INVALIDATED
            del self._records[reference.value]

    def publish_reference(
        self,
        reference: OpaqueReference,
        binding: ContentAuthorizationReferenceBinding,
    ) -> OpaqueReference:
        """Activate one registered reference at the publication commitment.

        Args:
            reference: Exact Registry-issued reference to activate.
            binding: Exact trusted lifecycle binding used at registration.

        Returns:
            The active Registry-issued opaque reference.

        Raises:
            ContentAuthorizationReferenceRejected: If publication is invalid or unavailable.
        """
        with self._lock:
            record = self._find_record(reference, binding)
            if record.state is not ContentAuthorizationReferenceState.REGISTERED_UNPUBLISHED:
                raise ContentAuthorizationReferenceRejected()
            if not self._is_effectively_valid(record):
                record.state = ContentAuthorizationReferenceState.INVALIDATED
                raise ContentAuthorizationReferenceRejected()
            record.state = ContentAuthorizationReferenceState.ACTIVE
            return record.reference

    def consume_active_reference(
        self,
        reference: OpaqueReference,
        binding: ContentAuthorizationReferenceBinding,
        single_attempt_consumption_binding: SingleAttemptConsumptionBinding,
    ) -> OpaqueReference:
        """Consume one active reference exactly once and return its opaque payload identity.

        Args:
            reference: Exact Registry-issued reference to consume.
            binding: Exact trusted lifecycle binding for the current invocation.
            single_attempt_consumption_binding: Exact trusted attempt binding to consume.

        Returns:
            The protected application payload identity for the winning continuation.

        Raises:
            ContentAuthorizationReferenceRejected: If consumption is unavailable for any reason.
        """
        return self._consume_active_reference(
            reference, binding, single_attempt_consumption_binding, require_payload=False
        ).binding.protected_payload_reference

    def consume_active_reference_with_payload(
        self,
        reference: OpaqueReference,
        binding: ContentAuthorizationReferenceBinding,
        single_attempt_consumption_binding: SingleAttemptConsumptionBinding,
    ) -> ConsumedActiveReference[PayloadT]:
        """Consume one active reference and return its exact retained payload to the winner.

        Args:
            reference: Exact Registry-issued reference to consume.
            binding: Exact trusted lifecycle binding for the current invocation.
            single_attempt_consumption_binding: Exact trusted attempt binding to consume.

        Returns:
            The exact retained protected payload and its neutral lifecycle binding.

        Raises:
            ContentAuthorizationReferenceRejected: If consumption is unavailable or has no payload.
        """
        record = self._consume_active_reference(
            reference, binding, single_attempt_consumption_binding, require_payload=True
        )
        assert record.payload is not None
        return ConsumedActiveReference(cast(PayloadT, record.payload), record.binding)

    def _consume_active_reference(
        self,
        reference: OpaqueReference,
        binding: ContentAuthorizationReferenceBinding,
        single_attempt_consumption_binding: SingleAttemptConsumptionBinding,
        require_payload: bool,
    ) -> _ReferenceRecord:
        """Linearize one active-reference consumption under the resolver lock."""
        with self._lock:
            record = self._find_record(reference, binding)
            if record.state is not ContentAuthorizationReferenceState.ACTIVE:
                raise ContentAuthorizationReferenceRejected()
            if not self._has_exact_consumption_binding(record, single_attempt_consumption_binding):
                raise ContentAuthorizationReferenceRejected()
            if not self._is_effectively_valid(record):
                self._invalidate_record(record)
                raise ContentAuthorizationReferenceRejected()
            if require_payload and record.payload is None:
                raise ContentAuthorizationReferenceRejected()
            record.state = ContentAuthorizationReferenceState.CONSUMED
            del self._records[record.reference.value]
            return record

    def _invalidate_record(self, record: _ReferenceRecord) -> None:
        """Invalidate and remove one exact lifecycle record while the resolver lock is held."""
        record.state = ContentAuthorizationReferenceState.INVALIDATED
        if self._records.get(record.reference.value) is record:
            del self._records[record.reference.value]

    def finalize_invocation(self) -> None:
        """Invalidate nonterminal records and clear all resolver-owned mappings."""
        with self._lock:
            self._is_finalized = True
            for record in self._records.values():
                if record.state in {
                    ContentAuthorizationReferenceState.REGISTERED_UNPUBLISHED,
                    ContentAuthorizationReferenceState.ACTIVE,
                }:
                    record.state = ContentAuthorizationReferenceState.INVALIDATED
            self._records.clear()

    def _find_record(
        self,
        reference: OpaqueReference,
        binding: ContentAuthorizationReferenceBinding,
    ) -> _ReferenceRecord:
        """Find one exact reference and lifecycle binding without revealing lookup status."""
        self._reject_if_finalized()
        if not isinstance(reference, OpaqueReference) or not isinstance(
            binding, ContentAuthorizationReferenceBinding
        ):
            raise ContentAuthorizationReferenceRejected()
        record = self._records.get(reference.value)
        if (
            record is None
            or record.reference is not reference
            or record.binding != binding
            or reference.owner != self._owner
        ):
            raise ContentAuthorizationReferenceRejected()
        return record

    def _is_effectively_valid(self, record: _ReferenceRecord) -> bool:
        """Determine whether both trusted clocks remain strictly before their immutable deadlines."""
        try:
            utc_now, monotonic_now = self._read_trusted_clocks()
        except ContentAuthorizationReferenceRejected:
            return False
        return (
            utc_now < record.binding.expires_at
            and (
                record.binding.invocation_expires_at is None
                or utc_now < record.binding.invocation_expires_at
            )
            and monotonic_now < record.monotonic_deadline
        )

    def _get_invocation_remaining(
        self,
        binding: ContentAuthorizationReferenceBinding,
        utc_now: datetime,
    ) -> timedelta:
        """Return the trusted invocation lifetime without permitting deadline extension."""
        if binding.invocation_expires_at is None:
            return _MAXIMUM_LIFETIME
        remaining = binding.invocation_expires_at - utc_now
        if remaining <= timedelta(0):
            raise ContentAuthorizationReferenceRejected()
        return remaining

    def _has_exact_consumption_binding(
        self,
        record: _ReferenceRecord,
        expected_binding: SingleAttemptConsumptionBinding,
    ) -> bool:
        """Determine whether explicit and redundant attempt bindings match exactly."""
        return (
            isinstance(expected_binding, SingleAttemptConsumptionBinding)
            and record.single_attempt_consumption_binding == expected_binding
            and record.binding.single_attempt_consumption_binding == expected_binding
            and expected_binding.decision_id == record.decision_id
            and expected_binding.invocation_id == record.invocation_id
            and expected_binding.attempt_number == record.attempt_number
        )

    def _read_trusted_clocks(self) -> tuple[datetime, float]:
        """Read trusted UTC and monotonic clocks or reject unavailable values."""
        try:
            utc_now = self._utc_clock()
            monotonic_now = self._monotonic_clock()
        except Exception as error:
            raise ContentAuthorizationReferenceRejected() from error
        if not _is_utc_datetime(utc_now) or type(monotonic_now) not in (int, float):
            raise ContentAuthorizationReferenceRejected()
        return utc_now, float(monotonic_now)

    def _generate_handle(self) -> str:
        """Generate exactly one lowercase hexadecimal handle from 32 secure random bytes."""
        try:
            random_bytes = self._random_bytes_source(32)
        except Exception as error:
            raise ContentAuthorizationReferenceRejected() from error
        if type(random_bytes) is not bytes or len(random_bytes) != 32:
            raise ContentAuthorizationReferenceRejected()
        handle = random_bytes.hex()
        if not _HANDLE_PATTERN.fullmatch(handle):
            raise ContentAuthorizationReferenceRejected()
        return handle

    def _reject_if_finalized(self) -> None:
        """Reject every operation after resolver scope finalization."""
        if self._is_finalized:
            raise ContentAuthorizationReferenceRejected()


def _is_non_blank_string(value: object) -> bool:
    """Determine whether a lifecycle identity is a non-blank plain string."""
    return type(value) is str and bool(value.strip())


def _is_utc_datetime(value: object) -> bool:
    """Determine whether a timestamp is timezone-aware and normalized to UTC."""
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
    )
