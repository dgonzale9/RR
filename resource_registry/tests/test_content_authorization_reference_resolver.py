"""Verify the in-memory Registry opaque-reference lifecycle."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest

from resource_registry.authorization.content_authorization_reference_resolver import (
    CandidateBoundPayloadFactory,
    ConsumedActiveReference,
    ContentAuthorizationReferenceBinding,
    ContentAuthorizationReferenceRejected,
    ContentAuthorizationReferenceResolver,
    ContentAuthorizationReferenceState,
    SingleAttemptConsumptionBinding,
)
from resource_registry.registry_core import (
    OpaqueReference,
    OwnerDescriptor,
    OwnershipClassification,
)


@dataclass(frozen=True, slots=True)
class _CandidatePayload:
    """Retain the exact candidate supplied by the neutral Registry boundary."""

    opaque_reference_binding: OpaqueReference


class _RecordingPayloadFactory(CandidateBoundPayloadFactory[_CandidatePayload]):
    """Build one payload only for the exact private candidate supplied by Registry."""

    def __init__(self, binding: ContentAuthorizationReferenceBinding) -> None:
        """Initialize the factory with valid neutral lifecycle binding evidence."""
        self.binding = binding
        self.candidates: list[OpaqueReference] = []
        self.payload: _CandidatePayload | None = None

    def build_payload(
        self, candidate: OpaqueReference
    ) -> tuple[ContentAuthorizationReferenceBinding, _CandidatePayload]:
        """Build one payload retaining the candidate object by identity."""
        self.candidates.append(candidate)
        self.payload = _CandidatePayload(candidate)
        return self.binding, self.payload

    def is_payload_bound_to_candidate(
        self, payload: _CandidatePayload, candidate: OpaqueReference
    ) -> bool:
        """Confirm the payload retains the exact candidate object."""
        return payload.opaque_reference_binding is candidate


class _ConfiguredPayloadFactory(CandidateBoundPayloadFactory[object]):
    """Build controlled neutral payload variants for resolver rejection tests."""

    def __init__(
        self,
        binding: ContentAuthorizationReferenceBinding,
        payload_builder: Callable[[OpaqueReference], object],
    ) -> None:
        """Initialize one factory with a typed payload builder and valid binding."""
        self.binding = binding
        self.payload_builder = payload_builder
        self.candidates: list[OpaqueReference] = []
        self.payload: object | None = None

    def build_payload(
        self, candidate: OpaqueReference
    ) -> tuple[ContentAuthorizationReferenceBinding, object]:
        """Build the configured payload variant for the supplied candidate."""
        self.candidates.append(candidate)
        self.payload = self.payload_builder(candidate)
        return self.binding, self.payload

    def is_payload_bound_to_candidate(self, payload: object, candidate: OpaqueReference) -> bool:
        """Report no additional binding evidence for controlled invalid payloads."""
        return False


class _RaisingPayloadFactory(CandidateBoundPayloadFactory[object]):
    """Raise while attempting neutral payload construction."""

    def __init__(self) -> None:
        """Initialize the factory call counter."""
        self.calls = 0

    def build_payload(
        self, candidate: OpaqueReference
    ) -> tuple[ContentAuthorizationReferenceBinding, object]:
        """Raise without exposing the candidate supplied by Registry."""
        self.calls += 1
        raise RuntimeError("protected factory payload")

    def is_payload_bound_to_candidate(self, payload: object, candidate: OpaqueReference) -> bool:
        """Report no binding after the configured construction failure."""
        return False


class _InvalidCandidateBindingPayload:
    """Expose a structurally present but invalid candidate-binding property."""

    @property
    def opaque_reference_binding(self) -> object:
        """Return an invalid protected binding value."""
        return "protected-invalid-binding"


class _Clocks:
    """Provide deterministic UTC and monotonic clock values."""

    def __init__(self) -> None:
        """Initialize the clocks at one trusted instant."""
        self.utc_now = datetime(2026, 8, 17, tzinfo=UTC)
        self.monotonic_now = 100.0

    def read_utc(self) -> datetime:
        """Return the trusted UTC test value."""
        return self.utc_now

    def read_monotonic(self) -> float:
        """Return the trusted monotonic test value."""
        return self.monotonic_now


def _owner() -> OwnerDescriptor:
    """Build the Registry reference owner used by the resolver."""
    return OwnerDescriptor("resource-registry", OwnershipClassification.REGISTRY_NATIVE)


def _opaque(value: str) -> OpaqueReference:
    """Build one Registry-owned opaque reference for neutral resolver tests."""
    return OpaqueReference(_owner(), value)


def _payload() -> OpaqueReference:
    """Build opaque protected payload identity without application coupling."""
    return _opaque("protected-payload")


def _binding(clocks: _Clocks, **changes: object) -> ContentAuthorizationReferenceBinding:
    """Build a valid lifecycle binding with optional replacements."""
    values: dict[str, object] = {
        "decision_id": "decision-1",
        "invocation_id": "invocation-1",
        "attempt_number": 1,
        "issued_at": clocks.utc_now,
        "expires_at": clocks.utc_now + timedelta(minutes=1),
        "invocation_expires_at": None,
        "single_attempt_consumption_binding": SingleAttemptConsumptionBinding(
            "decision-1", "invocation-1", 1
        ),
        "protected_payload_reference": _payload(),
    }
    values.update(changes)
    return ContentAuthorizationReferenceBinding(**values)  # type: ignore[arg-type]


def _resolver(
    clocks: _Clocks, random_bytes: bytes = b"a" * 32
) -> ContentAuthorizationReferenceResolver:
    """Build a resolver with deterministic dependencies."""
    return ContentAuthorizationReferenceResolver(
        _owner(),
        random_bytes_source=lambda size: random_bytes,
        utc_clock=clocks.read_utc,
        monotonic_clock=clocks.read_monotonic,
    )


def test_register_then_publish_generates_canonical_handle_and_consumes_once() -> None:
    """Register an opaque handle before making it active for one consumption."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    binding = _binding(clocks)

    reference = resolver.register_reference(binding)
    assert reference.value == "61" * 32
    with pytest.raises(ContentAuthorizationReferenceRejected):
        resolver.consume_active_reference(
            reference, binding, binding.single_attempt_consumption_binding
        )
    assert resolver.publish_reference(reference, binding) == reference
    assert (
        resolver.consume_active_reference(
            reference, binding, binding.single_attempt_consumption_binding
        )
        == _payload()
    )
    with pytest.raises(ContentAuthorizationReferenceRejected):
        resolver.consume_active_reference(
            reference, binding, binding.single_attempt_consumption_binding
        )


def test_payload_factory_registers_the_exact_candidate_without_publication() -> None:
    """Register one exact candidate and retain its unmodified neutral payload by identity."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    factory = _RecordingPayloadFactory(_binding(clocks))

    candidate, payload, _binding_evidence = resolver.register_reference_with_payload_factory(
        factory
    )

    record = resolver._records[candidate.value]
    assert len(factory.candidates) == 1
    assert factory.candidates[0] is candidate
    assert factory.payload is payload
    assert payload.opaque_reference_binding is candidate
    assert record.reference is candidate
    assert record.payload is payload
    assert record.state is ContentAuthorizationReferenceState.REGISTERED_UNPUBLISHED


def test_payload_is_returned_by_identity_only_after_the_winning_active_consumption() -> None:
    """Expose the retained payload only through the successful active-to-consumed transition."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    factory = _RecordingPayloadFactory(_binding(clocks))

    candidate, payload, binding = resolver.register_reference_with_payload_factory(factory)

    with pytest.raises(ContentAuthorizationReferenceRejected):
        resolver.consume_active_reference_with_payload(
            candidate, binding, binding.single_attempt_consumption_binding
        )
    assert (
        resolver._records[candidate.value].state
        is ContentAuthorizationReferenceState.REGISTERED_UNPUBLISHED
    )
    resolver.publish_reference(candidate, binding)

    consumed: ConsumedActiveReference[_CandidatePayload] = (
        resolver.consume_active_reference_with_payload(
            candidate, binding, binding.single_attempt_consumption_binding
        )
    )

    assert consumed.payload is payload
    assert consumed.binding is binding
    assert resolver._records == {}


def test_legacy_register_reference_remains_unpublished_without_a_payload() -> None:
    """Preserve the legacy registration contract without a protected payload record."""
    clocks = _Clocks()
    resolver = _resolver(clocks)

    reference = resolver.register_reference(_binding(clocks))

    record = resolver._records[reference.value]
    assert record.payload is None
    assert record.state is ContentAuthorizationReferenceState.REGISTERED_UNPUBLISHED


@pytest.mark.parametrize(
    ("payload_builder", "expects_value_equivalent_candidate"),
    (
        (lambda _candidate: None, False),
        (lambda _candidate: object(), False),
        (lambda _candidate: _InvalidCandidateBindingPayload(), False),
        (lambda _candidate: _CandidatePayload(_opaque("other-valid-candidate")), False),
        (
            lambda candidate: _CandidatePayload(OpaqueReference(candidate.owner, candidate.value)),
            True,
        ),
    ),
    ids=(
        "none_payload",
        "not_candidate_bound_payload",
        "invalid_binding_property",
        "other_valid_reference",
        "value_equivalent_but_not_identical_reference",
    ),
)
def test_invalid_payload_factory_results_reject_without_registration_or_disclosure(
    payload_builder: Callable[[OpaqueReference], object],
    expects_value_equivalent_candidate: bool,
) -> None:
    """Reject each neutral payload violation without persisting a candidate record."""
    clocks = _Clocks()
    entropy_calls: list[int] = []

    def record_entropy(size: int) -> bytes:
        """Record the one candidate generation attempted for this factory."""
        entropy_calls.append(size)
        return b"a" * size

    resolver = ContentAuthorizationReferenceResolver(
        _owner(), record_entropy, clocks.read_utc, clocks.read_monotonic
    )
    factory = _ConfiguredPayloadFactory(_binding(clocks), payload_builder)

    with pytest.raises(ContentAuthorizationReferenceRejected) as rejection:
        resolver.register_reference_with_payload_factory(factory)

    assert len(factory.candidates) == 1
    assert entropy_calls == [32]
    assert resolver._records == {}
    assert not any(
        record.state is ContentAuthorizationReferenceState.REGISTERED_UNPUBLISHED
        for record in resolver._records.values()
    )
    assert not any(
        record.state is ContentAuthorizationReferenceState.ACTIVE
        for record in resolver._records.values()
    )
    if expects_value_equivalent_candidate:
        assert isinstance(factory.payload, _CandidatePayload)
        assert factory.payload.opaque_reference_binding == factory.candidates[0]
        assert factory.payload.opaque_reference_binding is not factory.candidates[0]
    assert str(rejection.value) == "content authorization reference unavailable"
    assert factory.candidates[0].value not in str(rejection.value)
    assert "protected" not in str(rejection.value)


def test_raising_payload_factory_rejects_without_registration_or_disclosure() -> None:
    """Map one neutral factory exception to the stable undisclosing resolver rejection."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    factory = _RaisingPayloadFactory()

    with pytest.raises(ContentAuthorizationReferenceRejected) as rejection:
        resolver.register_reference_with_payload_factory(factory)

    assert factory.calls == 1
    assert resolver._records == {}
    assert str(rejection.value) == "content authorization reference unavailable"
    assert "61" * 32 not in str(rejection.value)
    assert "protected factory payload" not in str(rejection.value)


def test_payload_factory_cannot_reuse_a_different_known_reference() -> None:
    """Reject binding a new candidate to an existing Registry candidate by value or identity."""
    clocks = _Clocks()
    entropy_values = iter((b"a" * 32, b"b" * 32))
    resolver = ContentAuthorizationReferenceResolver(
        _owner(), lambda _size: next(entropy_values), clocks.read_utc, clocks.read_monotonic
    )
    original_factory = _RecordingPayloadFactory(_binding(clocks))
    known_reference, known_payload, _known_binding = (
        resolver.register_reference_with_payload_factory(original_factory)
    )
    reusing_factory = _ConfiguredPayloadFactory(
        _binding(clocks), lambda _candidate: _CandidatePayload(known_reference)
    )

    with pytest.raises(ContentAuthorizationReferenceRejected) as rejection:
        resolver.register_reference_with_payload_factory(reusing_factory)

    known_record = resolver._records[known_reference.value]
    assert len(reusing_factory.candidates) == 1
    assert len(resolver._records) == 1
    assert known_record.reference is known_reference
    assert known_record.payload is known_payload
    assert known_record.state is ContentAuthorizationReferenceState.REGISTERED_UNPUBLISHED
    assert str(rejection.value) == "content authorization reference unavailable"
    assert known_reference.value not in str(rejection.value)


def test_payload_registration_collision_rejects_without_retry_or_factory_invocation() -> None:
    """Reject an existing handle before building another payload or replacing the first record."""
    clocks = _Clocks()
    entropy_calls: list[int] = []

    def reuse_existing_handle(size: int) -> bytes:
        """Return the same deterministic bytes for every candidate request."""
        entropy_calls.append(size)
        return b"a" * size

    resolver = ContentAuthorizationReferenceResolver(
        _owner(), reuse_existing_handle, clocks.read_utc, clocks.read_monotonic
    )
    original_factory = _RecordingPayloadFactory(_binding(clocks))
    existing_reference, existing_payload, _existing_binding = (
        resolver.register_reference_with_payload_factory(original_factory)
    )
    entropy_calls.clear()
    collision_factory = _RecordingPayloadFactory(_binding(clocks))

    with pytest.raises(ContentAuthorizationReferenceRejected) as rejection:
        resolver.register_reference_with_payload_factory(collision_factory)

    existing_record = resolver._records[existing_reference.value]
    assert entropy_calls == [32]
    assert collision_factory.candidates == []
    assert len(resolver._records) == 1
    assert existing_record.reference is existing_reference
    assert existing_record.payload is existing_payload
    assert existing_record.state is ContentAuthorizationReferenceState.REGISTERED_UNPUBLISHED
    assert str(rejection.value) == "content authorization reference unavailable"
    assert existing_reference.value not in str(rejection.value)


def test_default_random_source_is_os_backed_and_resolver_has_no_serialization_surface() -> None:
    """Keep production generation OS-backed and lifecycle state non-serializable."""
    resolver = ContentAuthorizationReferenceResolver(_owner())

    assert resolver._random_bytes_source is secrets.token_bytes
    assert not hasattr(resolver, "to_dict")
    assert not hasattr(resolver, "serialize")


def test_collision_expiry_and_finalization_fail_without_disclosure() -> None:
    """Reject collisions, expired references, and finalized state without leaking handles."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    binding = _binding(clocks)
    reference = resolver.register_reference(binding)
    with pytest.raises(ContentAuthorizationReferenceRejected) as collision:
        resolver.register_reference(binding)
    assert reference.value not in str(collision.value)

    resolver.publish_reference(reference, binding)
    clocks.utc_now = binding.expires_at
    with pytest.raises(ContentAuthorizationReferenceRejected):
        resolver.consume_active_reference(
            reference, binding, binding.single_attempt_consumption_binding
        )
    resolver.finalize_invocation()
    with pytest.raises(ContentAuthorizationReferenceRejected):
        resolver.consume_active_reference(
            reference, binding, binding.single_attempt_consumption_binding
        )


def test_invocation_expiry_and_explicit_consumption_binding_are_enforced() -> None:
    """Limit validity by invocation expiry and require an exact explicit attempt binding."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    binding = _binding(clocks, invocation_expires_at=clocks.utc_now + timedelta(seconds=10))
    reference = resolver.register_reference(binding)
    resolver.publish_reference(reference, binding)
    clocks.monotonic_now += 10
    with pytest.raises(ContentAuthorizationReferenceRejected):
        resolver.consume_active_reference(
            reference, binding, binding.single_attempt_consumption_binding
        )
    with pytest.raises(ValueError):
        _binding(
            clocks,
            single_attempt_consumption_binding=SingleAttemptConsumptionBinding(
                "other-decision", "invocation-1", 1
            ),
        )


@pytest.mark.parametrize(
    "invocation_expires_at",
    (
        datetime(2026, 8, 17),
        datetime(2026, 8, 17, tzinfo=UTC),
    ),
)
def test_invalid_or_elapsed_invocation_expiry_rejects_before_handle_generation(
    invocation_expires_at: datetime,
) -> None:
    """Reject invalid invocation expiry before the random source can execute."""
    clocks = _Clocks()
    random_calls = 0

    def count_random_bytes(size: int) -> bytes:
        """Record unexpected randomness requests."""
        nonlocal random_calls
        random_calls += 1
        return b"a" * size

    resolver = ContentAuthorizationReferenceResolver(
        _owner(), count_random_bytes, clocks.read_utc, clocks.read_monotonic
    )
    with pytest.raises((ValueError, ContentAuthorizationReferenceRejected)):
        resolver.register_reference(_binding(clocks, invocation_expires_at=invocation_expires_at))
    assert random_calls == 0


@pytest.mark.parametrize(
    ("expires_after", "invocation_after", "monotonic_after"),
    (
        (timedelta(minutes=10), timedelta(seconds=10), 10.0),
        (timedelta(seconds=10), timedelta(minutes=10), 10.0),
        (timedelta(minutes=10), timedelta(minutes=10), 300.0),
    ),
)
def test_earliest_invocation_wall_or_ceiling_deadline_controls_consumption(
    expires_after: timedelta,
    invocation_after: timedelta,
    monotonic_after: float,
) -> None:
    """Reject consumption at the earliest immutable lifetime boundary."""
    clocks = _Clocks()
    binding = _binding(
        clocks,
        expires_at=clocks.utc_now + expires_after,
        invocation_expires_at=clocks.utc_now + invocation_after,
    )
    resolver = _resolver(clocks)
    reference = resolver.register_reference(binding)
    resolver.publish_reference(reference, binding)
    clocks.monotonic_now += monotonic_after
    with pytest.raises(ContentAuthorizationReferenceRejected):
        resolver.consume_active_reference(
            reference, binding, binding.single_attempt_consumption_binding
        )


def test_backward_utc_movement_cannot_extend_invocation_monotonic_deadline() -> None:
    """Keep the original monotonic deadline authoritative after UTC moves backward."""
    clocks = _Clocks()
    binding = _binding(clocks, invocation_expires_at=clocks.utc_now + timedelta(seconds=10))
    resolver = _resolver(clocks)
    reference = resolver.register_reference(binding)
    resolver.publish_reference(reference, binding)
    clocks.utc_now -= timedelta(hours=1)
    clocks.monotonic_now += 10
    with pytest.raises(ContentAuthorizationReferenceRejected):
        resolver.consume_active_reference(
            reference, binding, binding.single_attempt_consumption_binding
        )


def test_single_attempt_binding_is_immutable_and_exactly_required_for_consumption() -> None:
    """Require the stored immutable binding rather than a derivable substitute."""
    clocks = _Clocks()
    binding = _binding(clocks)
    with pytest.raises((AttributeError, TypeError)):
        binding.single_attempt_consumption_binding.attempt_number = 2  # type: ignore[misc]
    for attempt in (0, -1):
        with pytest.raises(ValueError):
            SingleAttemptConsumptionBinding("decision-1", "invocation-1", attempt)

    resolver = _resolver(clocks)
    reference = resolver.register_reference(binding)
    resolver.publish_reference(reference, binding)
    for expected in (
        SingleAttemptConsumptionBinding("other", "invocation-1", 1),
        SingleAttemptConsumptionBinding("decision-1", "other", 1),
        SingleAttemptConsumptionBinding("decision-1", "invocation-1", 2),
    ):
        with pytest.raises(ContentAuthorizationReferenceRejected) as rejection:
            resolver.consume_active_reference(reference, binding, expected)
        assert "decision-1" not in str(rejection.value)
    assert (
        resolver.consume_active_reference(
            reference, binding, binding.single_attempt_consumption_binding
        )
        == binding.protected_payload_reference
    )


def test_monotonic_deadline_context_mismatch_and_instances_fail_closed() -> None:
    """Enforce independent deadline, exact context, and instance-local state."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    binding = _binding(clocks)
    reference = resolver.register_reference(binding)
    resolver.publish_reference(reference, binding)
    other_binding = _binding(
        clocks,
        decision_id="other",
        single_attempt_consumption_binding=SingleAttemptConsumptionBinding(
            "other", "invocation-1", 1
        ),
    )
    with pytest.raises(ContentAuthorizationReferenceRejected):
        resolver.consume_active_reference(
            reference,
            other_binding,
            binding.single_attempt_consumption_binding,
        )
    clocks.monotonic_now += 60.0
    with pytest.raises(ContentAuthorizationReferenceRejected):
        resolver.consume_active_reference(
            reference, binding, binding.single_attempt_consumption_binding
        )
    with pytest.raises(ContentAuthorizationReferenceRejected):
        _resolver(clocks).consume_active_reference(
            reference, binding, binding.single_attempt_consumption_binding
        )


def test_only_one_concurrent_consumer_wins() -> None:
    """Make the ACTIVE-to-CONSUMED transition atomic across competing callers."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    binding = _binding(clocks)
    reference = resolver.register_reference(binding)
    resolver.publish_reference(reference, binding)
    barrier = Barrier(2)

    def consume_after_barrier() -> bool:
        """Attempt one simultaneous consumption and report whether it won."""
        barrier.wait()
        try:
            resolver.consume_active_reference(
                reference, binding, binding.single_attempt_consumption_binding
            )
        except ContentAuthorizationReferenceRejected:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(lambda _index: consume_after_barrier(), range(2))) == 1


def test_value_equivalent_reference_cannot_consume_the_registered_payload() -> None:
    """Reject a copied opaque reference without exposing the active payload."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    candidate, payload, binding = resolver.register_reference_with_payload_factory(
        _RecordingPayloadFactory(_binding(clocks))
    )
    resolver.publish_reference(candidate, binding)
    copied_candidate = OpaqueReference(candidate.owner, candidate.value)

    with pytest.raises(ContentAuthorizationReferenceRejected) as rejection:
        resolver.consume_active_reference_with_payload(
            copied_candidate, binding, binding.single_attempt_consumption_binding
        )

    assert str(rejection.value) == "content authorization reference unavailable"
    assert candidate.value not in str(rejection.value)
    assert resolver._records[candidate.value].state is ContentAuthorizationReferenceState.ACTIVE
    assert (
        resolver.consume_active_reference_with_payload(
            candidate, binding, binding.single_attempt_consumption_binding
        ).payload
        is payload
    )


@pytest.mark.parametrize(
    "build_mismatched_binding",
    (
        lambda binding: replace(
            binding,
            decision_id="other",
            single_attempt_consumption_binding=SingleAttemptConsumptionBinding(
                "other", "invocation-1", 1
            ),
        ),
        lambda binding: replace(
            binding,
            invocation_id="other",
            single_attempt_consumption_binding=SingleAttemptConsumptionBinding(
                "decision-1", "other", 1
            ),
        ),
        lambda binding: replace(
            binding,
            attempt_number=2,
            single_attempt_consumption_binding=SingleAttemptConsumptionBinding(
                "decision-1", "invocation-1", 2
            ),
        ),
        lambda binding: replace(binding, protected_payload_reference=_opaque("other-payload")),
        lambda binding: replace(
            binding, invocation_expires_at=datetime(2026, 8, 17, 12, 1, tzinfo=UTC)
        ),
    ),
)
def test_each_valid_but_wrong_neutral_binding_rejects_without_consuming(
    build_mismatched_binding: Callable[
        [ContentAuthorizationReferenceBinding], ContentAuthorizationReferenceBinding
    ],
) -> None:
    """Reject each independent neutral binding mismatch while retaining ACTIVE state."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    candidate, _payload_value, binding = resolver.register_reference_with_payload_factory(
        _RecordingPayloadFactory(_binding(clocks))
    )
    resolver.publish_reference(candidate, binding)
    presented_binding = build_mismatched_binding(binding)

    with pytest.raises(ContentAuthorizationReferenceRejected) as rejection:
        resolver.consume_active_reference_with_payload(
            candidate, presented_binding, presented_binding.single_attempt_consumption_binding
        )

    assert str(rejection.value) == "content authorization reference unavailable"
    assert resolver._records[candidate.value].state is ContentAuthorizationReferenceState.ACTIVE


@pytest.mark.parametrize("advance_utc, advance_monotonic", ((60, 0), (61, 0), (0, 60), (0, 61)))
def test_expiry_boundaries_invalidate_without_returning_payload(
    advance_utc: int, advance_monotonic: int
) -> None:
    """Reject consumption at or beyond either deterministic lifetime deadline."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    candidate, _payload_value, binding = resolver.register_reference_with_payload_factory(
        _RecordingPayloadFactory(_binding(clocks))
    )
    resolver.publish_reference(candidate, binding)
    record = resolver._records[candidate.value]
    clocks.utc_now += timedelta(seconds=advance_utc)
    clocks.monotonic_now += advance_monotonic

    with pytest.raises(ContentAuthorizationReferenceRejected):
        resolver.consume_active_reference_with_payload(
            candidate, binding, binding.single_attempt_consumption_binding
        )
    assert record.state is ContentAuthorizationReferenceState.INVALIDATED
    assert candidate.value not in resolver._records
    with pytest.raises(ContentAuthorizationReferenceRejected) as replay:
        resolver.consume_active_reference_with_payload(
            candidate, binding, binding.single_attempt_consumption_binding
        )
    assert str(replay.value) == "content authorization reference unavailable"


def test_six_concurrent_payload_consumers_have_one_exact_winner() -> None:
    """Return the retained payload to exactly one simultaneous payload consumer."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    candidate, payload, binding = resolver.register_reference_with_payload_factory(
        _RecordingPayloadFactory(_binding(clocks))
    )
    resolver.publish_reference(candidate, binding)
    barrier = Barrier(6)

    def consume() -> _CandidatePayload | None:
        """Attempt one barrier-synchronized payload consumption."""
        barrier.wait()
        try:
            return resolver.consume_active_reference_with_payload(
                candidate, binding, binding.single_attempt_consumption_binding
            ).payload
        except ContentAuthorizationReferenceRejected:
            return None

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _index: consume(), range(6)))
    assert results.count(payload) == 1
    assert results.count(None) == 5


def test_different_owner_reference_is_rejected_by_registered_reference_identity() -> None:
    """Cover owner through the sole canonical reference presentation."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    candidate, _payload_value, binding = resolver.register_reference_with_payload_factory(
        _RecordingPayloadFactory(_binding(clocks))
    )
    resolver.publish_reference(candidate, binding)
    foreign = OpaqueReference(
        OwnerDescriptor("other-registry", OwnershipClassification.REGISTRY_NATIVE), candidate.value
    )
    with pytest.raises(ContentAuthorizationReferenceRejected) as rejection:
        resolver.consume_active_reference_with_payload(
            foreign, binding, binding.single_attempt_consumption_binding
        )
    assert str(rejection.value) == "content authorization reference unavailable"
    assert resolver._records[candidate.value].state is ContentAuthorizationReferenceState.ACTIVE


def test_unpublished_unknown_and_replay_rejections_are_uniform_and_do_not_return_payload() -> None:
    """Reject non-consumable and sequential replay presentations without disclosure."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    candidate, payload, binding = resolver.register_reference_with_payload_factory(
        _RecordingPayloadFactory(_binding(clocks))
    )
    unknown = _opaque("b" * 64)
    rejections: list[str] = []
    for reference in (candidate, unknown):
        with pytest.raises(ContentAuthorizationReferenceRejected) as rejection:
            resolver.consume_active_reference_with_payload(
                reference, binding, binding.single_attempt_consumption_binding
            )
        rejections.append(str(rejection.value))
    assert (
        resolver._records[candidate.value].state
        is ContentAuthorizationReferenceState.REGISTERED_UNPUBLISHED
    )
    resolver.publish_reference(candidate, binding)
    assert (
        resolver.consume_active_reference_with_payload(
            candidate, binding, binding.single_attempt_consumption_binding
        ).payload
        is payload
    )
    for consume in (
        resolver.consume_active_reference,
        resolver.consume_active_reference_with_payload,
    ):
        with pytest.raises(ContentAuthorizationReferenceRejected) as rejection:
            consume(candidate, binding, binding.single_attempt_consumption_binding)
        rejections.append(str(rejection.value))
    assert rejections == ["content authorization reference unavailable"] * len(rejections)


def test_unverifiable_clock_invalidates_active_reference_without_disclosing_cause() -> None:
    """Fail closed when either trusted clock cannot be verified at consumption time."""
    clocks = _Clocks()
    resolver = ContentAuthorizationReferenceResolver(
        _owner(),
        random_bytes_source=lambda size: b"a" * size,
        utc_clock=clocks.read_utc,
        monotonic_clock=clocks.read_monotonic,
    )
    binding = _binding(clocks)
    candidate = resolver.register_reference(binding)
    resolver.publish_reference(candidate, binding)
    record = resolver._records[candidate.value]

    def raise_clock_error() -> datetime:
        """Simulate one ordinary trusted-clock failure."""
        raise RuntimeError("clock")

    resolver._utc_clock = raise_clock_error
    with pytest.raises(ContentAuthorizationReferenceRejected) as rejection:
        resolver.consume_active_reference(
            candidate, binding, binding.single_attempt_consumption_binding
        )
    assert str(rejection.value) == "content authorization reference unavailable"
    assert record.state is ContentAuthorizationReferenceState.INVALIDATED
    assert candidate.value not in resolver._records
    with pytest.raises(ContentAuthorizationReferenceRejected) as replay:
        resolver.consume_active_reference(
            candidate, binding, binding.single_attempt_consumption_binding
        )
    assert str(replay.value) == "content authorization reference unavailable"


def test_invalid_clock_value_invalidates_active_reference_without_retaining_payload() -> None:
    """Fail closed and clean up when a trusted clock returns a non-verifiable value."""
    clocks = _Clocks()
    return_invalid_value = False

    def read_monotonic() -> float:
        """Return a valid clock initially and an invalid runtime value on demand."""
        if return_invalid_value:
            return True
        return clocks.monotonic_now

    resolver = ContentAuthorizationReferenceResolver(
        _owner(),
        random_bytes_source=lambda size: b"a" * size,
        utc_clock=clocks.read_utc,
        monotonic_clock=read_monotonic,
    )
    binding = _binding(clocks)
    candidate = resolver.register_reference(binding)
    resolver.publish_reference(candidate, binding)
    record = resolver._records[candidate.value]
    return_invalid_value = True

    with pytest.raises(ContentAuthorizationReferenceRejected) as rejection:
        resolver.consume_active_reference(
            candidate, binding, binding.single_attempt_consumption_binding
        )

    assert str(rejection.value) == "content authorization reference unavailable"
    assert record.state is ContentAuthorizationReferenceState.INVALIDATED
    assert candidate.value not in resolver._records
    with pytest.raises(ContentAuthorizationReferenceRejected):
        resolver.consume_active_reference(
            candidate, binding, binding.single_attempt_consumption_binding
        )


def test_invalidated_reference_rejects_both_consumption_apis() -> None:
    """Keep invalidation terminal for both consumption APIs."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    candidate, _payload, binding = resolver.register_reference_with_payload_factory(
        _RecordingPayloadFactory(_binding(clocks))
    )
    resolver.publish_reference(candidate, binding)
    invalidated_record = resolver._records[candidate.value]
    resolver.invalidate_reference(candidate, binding)
    messages: list[str] = []
    for consume in (
        resolver.consume_active_reference,
        resolver.consume_active_reference_with_payload,
    ):
        with pytest.raises(ContentAuthorizationReferenceRejected) as rejection:
            consume(candidate, binding, binding.single_attempt_consumption_binding)
        messages.append(str(rejection.value))
    assert candidate.value not in resolver._records
    assert invalidated_record.state is ContentAuthorizationReferenceState.INVALIDATED
    assert messages == ["content authorization reference unavailable"] * 2


def test_sequential_legacy_and_payload_apis_allow_exactly_one_winner_in_each_order() -> None:
    """Prevent sequential replay across legacy and payload consumption APIs."""
    for first_payload in (False, True):
        clocks = _Clocks()
        resolver = _resolver(clocks)
        candidate, payload, binding = resolver.register_reference_with_payload_factory(
            _RecordingPayloadFactory(_binding(clocks))
        )
        resolver.publish_reference(candidate, binding)
        consumed_record = resolver._records[candidate.value]
        if first_payload:
            assert (
                resolver.consume_active_reference_with_payload(
                    candidate, binding, binding.single_attempt_consumption_binding
                ).payload
                is payload
            )
            with pytest.raises(ContentAuthorizationReferenceRejected):
                resolver.consume_active_reference(
                    candidate, binding, binding.single_attempt_consumption_binding
                )
        else:
            assert (
                resolver.consume_active_reference(
                    candidate, binding, binding.single_attempt_consumption_binding
                )
                == binding.protected_payload_reference
            )
            with pytest.raises(ContentAuthorizationReferenceRejected):
                resolver.consume_active_reference_with_payload(
                    candidate, binding, binding.single_attempt_consumption_binding
                )
        assert candidate.value not in resolver._records
        assert consumed_record.state is ContentAuthorizationReferenceState.CONSUMED


def test_atomic_expiry_check_beats_stale_validity_precheck_and_invocation_deadline() -> None:
    """Invalidate at the earlier invocation deadline despite an earlier valid observation."""
    clocks = _Clocks()
    initial = _binding(
        clocks,
        expires_at=clocks.utc_now + timedelta(minutes=2),
        invocation_expires_at=clocks.utc_now + timedelta(seconds=10),
    )
    resolver = _resolver(clocks)
    candidate, _payload, binding = resolver.register_reference_with_payload_factory(
        _RecordingPayloadFactory(initial)
    )
    resolver.publish_reference(candidate, binding)
    record = resolver._records[candidate.value]
    assert resolver._is_effectively_valid(record)
    clocks.utc_now += timedelta(seconds=10)
    with pytest.raises(ContentAuthorizationReferenceRejected):
        resolver.consume_active_reference_with_payload(
            candidate, binding, binding.single_attempt_consumption_binding
        )
    assert clocks.utc_now < binding.expires_at
    assert record.state is ContentAuthorizationReferenceState.INVALIDATED
    assert candidate.value not in resolver._records


def test_concurrent_legacy_and_payload_consumers_share_one_terminal_transition() -> None:
    """Allow exactly one competing legacy or payload consumption to win."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    candidate, payload, binding = resolver.register_reference_with_payload_factory(
        _RecordingPayloadFactory(_binding(clocks))
    )
    resolver.publish_reference(candidate, binding)
    consumed_record = resolver._records[candidate.value]
    barrier = Barrier(2)

    def consume_legacy() -> tuple[str, _CandidatePayload | None, str | None]:
        """Compete through the historical opaque-reference API."""
        barrier.wait()
        try:
            resolver.consume_active_reference(
                candidate, binding, binding.single_attempt_consumption_binding
            )
            return "legacy", None, None
        except ContentAuthorizationReferenceRejected as error:
            return "rejected", None, str(error)

    def consume_payload() -> tuple[str, _CandidatePayload | None, str | None]:
        """Compete through the payload-returning API."""
        barrier.wait()
        try:
            result: ConsumedActiveReference[_CandidatePayload] = (
                resolver.consume_active_reference_with_payload(
                    candidate, binding, binding.single_attempt_consumption_binding
                )
            )
            return "payload", result.payload, None
        except ContentAuthorizationReferenceRejected as error:
            return "rejected", None, str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        legacy_future = executor.submit(consume_legacy)
        payload_future = executor.submit(consume_payload)
        results = [legacy_future.result(), payload_future.result()]
    winners = [result for result in results if result[0] != "rejected"]
    rejections = [result[2] for result in results if result[0] == "rejected"]
    assert len(winners) == 1
    assert rejections == ["content authorization reference unavailable"]
    assert consumed_record.state is ContentAuthorizationReferenceState.CONSUMED
    assert candidate.value not in resolver._records
    if winners[0][0] == "payload":
        assert winners[0][1] is payload
    else:
        assert winners[0][1] is None


def test_concurrent_payload_consumption_and_invalidation_have_one_terminal_winner() -> None:
    """Allow exactly one payload consumption or invalidation terminal transition to win."""
    clocks = _Clocks()
    resolver = _resolver(clocks)
    candidate, payload, binding = resolver.register_reference_with_payload_factory(
        _RecordingPayloadFactory(_binding(clocks))
    )
    resolver.publish_reference(candidate, binding)
    terminal_record = resolver._records[candidate.value]
    barrier = Barrier(2)

    def consume() -> tuple[
        str, _CandidatePayload | None, ContentAuthorizationReferenceRejected | None
    ]:
        """Attempt the payload terminal transition."""
        barrier.wait()
        try:
            return (
                "consumed",
                resolver.consume_active_reference_with_payload(
                    candidate, binding, binding.single_attempt_consumption_binding
                ).payload,
                None,
            )
        except ContentAuthorizationReferenceRejected as error:
            return "rejected", None, error

    def invalidate() -> tuple[
        str, _CandidatePayload | None, ContentAuthorizationReferenceRejected | None
    ]:
        """Attempt the invalidation terminal transition."""
        barrier.wait()
        try:
            resolver.invalidate_reference(candidate, binding)
            return "invalidated", None, None
        except ContentAuthorizationReferenceRejected as error:
            return "rejected", None, error

    with ThreadPoolExecutor(max_workers=2) as executor:
        consumption_future = executor.submit(consume)
        invalidation_future = executor.submit(invalidate)
        results = [consumption_future.result(), invalidation_future.result()]
    winners = [result for result in results if result[0] != "rejected"]
    losers = [result[2] for result in results if result[0] == "rejected"]
    assert len(winners) == 1
    assert len(losers) == 1
    assert isinstance(losers[0], ContentAuthorizationReferenceRejected)
    message = str(losers[0])
    assert message == "content authorization reference unavailable"
    for protected_value in (
        candidate.value,
        binding.decision_id,
        binding.invocation_id,
        str(binding.attempt_number),
        binding.protected_payload_reference.value,
        ContentAuthorizationReferenceState.CONSUMED.value,
        ContentAuthorizationReferenceState.INVALIDATED.value,
    ):
        assert protected_value not in message
    assert terminal_record.state in {
        ContentAuthorizationReferenceState.CONSUMED,
        ContentAuthorizationReferenceState.INVALIDATED,
    }
    assert candidate.value not in resolver._records
    if winners[0][0] == "consumed":
        assert winners[0][1] is payload
    else:
        assert winners[0][1] is None
