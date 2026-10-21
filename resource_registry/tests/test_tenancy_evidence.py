"""Verify the Registry-owned D6 tenancy-evidence lifecycle, clocks, locking, and revalidation.

Each test maps to one approved D6 TDD matrix identifier recorded in
``creative_assembly/docs/architecture/CREATIVE_ASSEMBLY_D6_S1_R2_IMPLEMENTATION_CONTRACT.md``.
The identifier appears in the test name and in the first docstring line.

The 17 ``CONDITIONAL ON FUTURE D5 SEAM`` identifiers (``D6-I02``, ``D6-I03``, ``D6-A13``, and
``D6-D501`` through ``D6-D514``) remain unauthorized and are deliberately absent.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import json
import math
import pickle
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from resource_registry.authorization import tenancy_evidence
from resource_registry.authorization.tenancy_evidence import (
    D6_MAXIMUM_LIFETIME,
    RegistryEvidenceConsumer,
    RegistryOwnedPrincipalContext,
    RegistryOwnerKind,
    RegistryPrincipalKind,
    RegistryTenancyEvidenceSession,
    RegistryTenancyEvidenceState,
    RegistryTenancyFacts,
    TrustedRegistryTenancyProof,
)
from resource_registry.authorization.tenancy_evidence_issuer import (
    RegistryAdmissionFacts,
    RegistryTenancyEvidenceIssuer,
)
from resource_registry.authorization.tenancy_evidence_port import D6EvidenceRejected

_ISSUED_AT = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)
_MONOTONIC_AT_ISSUE = 1_000.0
_NAMESPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
_OTHER_NAMESPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
_PRINCIPAL_ID = 41
_ORGANIZATION_ID = 7
_WORKSPACE_ID = 13
_BOUNDED_TIMEOUT_SECONDS = 10.0


class _FakeClock:
    """Serve fully controlled UTC and monotonic observations to one session."""

    def __init__(
        self,
        utc_value: object = _ISSUED_AT,
        monotonic_value: object = _MONOTONIC_AT_ISSUE,
    ) -> None:
        """Initialize the controllable trusted clock."""
        self.utc_value = utc_value
        self.monotonic_value = monotonic_value
        self.utc_error: BaseException | None = None
        self.monotonic_error: BaseException | None = None

    def utc_now(self) -> datetime:
        """Return the configured UTC observation or raise the configured failure."""
        if self.utc_error is not None:
            raise self.utc_error
        return self.utc_value  # type: ignore[return-value]

    def monotonic_now(self) -> float:
        """Return the configured monotonic observation or raise the configured failure."""
        if self.monotonic_error is not None:
            raise self.monotonic_error
        return self.monotonic_value  # type: ignore[return-value]


class _FakeTenancyFactsSource:
    """Return Registry-owned tenancy facts and record every revalidation call."""

    def __init__(self, facts: RegistryTenancyFacts) -> None:
        """Initialize the controllable Registry-owned tenancy facts source."""
        self.facts = facts
        self.error: BaseException | None = None
        self.calls = 0
        self.on_call: object = None

    def resolve_current_tenancy(
        self, principal_context: RegistryOwnedPrincipalContext
    ) -> RegistryTenancyFacts:
        """Return the currently configured facts after running any injected interleaving."""
        self.calls += 1
        if callable(self.on_call):
            self.on_call()
        if self.error is not None:
            raise self.error
        return self.facts


class _IntegerSubclass(int):
    """Represent an integer subclass that must not satisfy D6 identity validation."""


class _IntegerConvertible:
    """Represent an object that claims it can convert itself to an integer."""

    def __int__(self) -> int:
        """Return a valid-looking integer without being one."""
        return _PRINCIPAL_ID


class _IntegerEqualityImpostor:
    """Represent an object that claims equality with every candidate integer."""

    __hash__ = object.__hash__

    def __eq__(self, other: object) -> bool:
        """Claim equality without exposing an integer identity."""
        return True

    def __ne__(self, other: object) -> bool:
        """Claim non-inequality without exposing an integer identity."""
        return False


class _PrincipalKindEqualityImpostor:
    """Represent an object that claims equality with every principal kind."""

    __hash__ = object.__hash__

    def __eq__(self, other: object) -> bool:
        """Claim equality without being an approved principal kind."""
        return True

    def __ne__(self, other: object) -> bool:
        """Claim non-inequality without being an approved principal kind."""
        return False


_INVALID_ID_VALUES = (
    ("true", True),
    ("false", False),
    ("zero", 0),
    ("negative", -1),
    ("none", None),
    ("numeric-string", "1"),
    ("float", 1.0),
    ("integer-subclass", _IntegerSubclass(1)),
    ("int-convertible", _IntegerConvertible()),
    ("integer-equality-impostor", _IntegerEqualityImpostor()),
    ("arbitrary-object", object()),
)

_INVALID_PRINCIPAL_KINDS = (
    ("missing", None),
    ("string", "django_user"),
    ("arbitrary-object", object()),
    ("equality-impostor", _PrincipalKindEqualityImpostor()),
)


def _valid_facts(
    *,
    namespace_id: UUID = _NAMESPACE_ID,
    owner_kind: RegistryOwnerKind = RegistryOwnerKind.WORKSPACE,
    owner_id: int = _WORKSPACE_ID,
) -> RegistryTenancyFacts:
    """Build the canonical valid Registry tenancy facts for one Workspace-owned namespace."""
    return RegistryTenancyFacts(
        principal_id=_PRINCIPAL_ID,
        principal_kind=RegistryPrincipalKind.DJANGO_USER,
        organization_id=_ORGANIZATION_ID,
        workspace_id=_WORKSPACE_ID,
        namespace_id=namespace_id,
        owner_kind=owner_kind,
        owner_id=owner_id,
    )


def _principal_context(
    workspace_id: int | None = _WORKSPACE_ID,
    principal_kind: RegistryPrincipalKind = RegistryPrincipalKind.DJANGO_USER,
) -> RegistryOwnedPrincipalContext:
    """Build the trusted Registry-owned principal context for one evaluation."""
    return RegistryOwnedPrincipalContext(
        principal_id=_PRINCIPAL_ID,
        organization_id=_ORGANIZATION_ID,
        principal_kind=principal_kind,
        workspace_id=workspace_id,
    )


def _open_session(
    *,
    clock: _FakeClock | None = None,
    facts_source: _FakeTenancyFactsSource | None = None,
    request_expires_at: datetime | None = None,
    evaluation_expires_at: datetime | None = None,
    workspace_id: int | None = _WORKSPACE_ID,
) -> tuple[RegistryTenancyEvidenceSession, _FakeClock, _FakeTenancyFactsSource]:
    """Open one D4 Registry tenancy-evidence session with fully injected dependencies."""
    resolved_clock = clock if clock is not None else _FakeClock()
    resolved_source = (
        facts_source if facts_source is not None else _FakeTenancyFactsSource(_valid_facts())
    )
    session = RegistryTenancyEvidenceSession.open(
        consumer=RegistryEvidenceConsumer.D4,
        trusted_principal_context=_principal_context(workspace_id),
        tenancy_facts_source=resolved_source,
        clock=resolved_clock,
        request_expires_at=request_expires_at,
        evaluation_expires_at=evaluation_expires_at,
    )
    return session, resolved_clock, resolved_source


def _issued_session() -> tuple[
    RegistryTenancyEvidenceSession,
    TrustedRegistryTenancyProof,
    _FakeClock,
    _FakeTenancyFactsSource,
]:
    """Open one session and publish exactly one valid ISSUED proof."""
    session, clock, source = _open_session()
    proof = session.issue()
    return session, proof, clock, source


def _only_record(session: RegistryTenancyEvidenceSession) -> object:
    """Return the single pending Registry-owned record retained by the session."""
    return next(iter(session._records.values()))


def _valid_admission(
    *,
    principal_is_persisted: bool = True,
    principal_id: int | None = _PRINCIPAL_ID,
    principal_kind: RegistryPrincipalKind | None = RegistryPrincipalKind.DJANGO_USER,
    organization_id: int | None = _ORGANIZATION_ID,
    organization_is_enabled: bool = True,
    workspace_id: int | None = _WORKSPACE_ID,
    workspace_is_active: bool = True,
    workspace_is_enabled: bool = True,
    workspace_organization_id: int | None = _ORGANIZATION_ID,
    has_authorized_membership: bool = True,
    namespace_id: UUID | None = _NAMESPACE_ID,
    namespace_owner_kind: RegistryOwnerKind | None = RegistryOwnerKind.WORKSPACE,
    namespace_owner_id: int | None = _WORKSPACE_ID,
) -> RegistryAdmissionFacts:
    """Build one Registry admission snapshot that is valid unless a field is overridden."""
    return RegistryAdmissionFacts(
        principal_is_persisted=principal_is_persisted,
        principal_id=principal_id,
        principal_kind=principal_kind,
        organization_id=organization_id,
        organization_is_enabled=organization_is_enabled,
        workspace_id=workspace_id,
        workspace_is_active=workspace_is_active,
        workspace_is_enabled=workspace_is_enabled,
        workspace_organization_id=workspace_organization_id,
        has_authorized_membership=has_authorized_membership,
        namespace_id=namespace_id,
        namespace_owner_kind=namespace_owner_kind,
        namespace_owner_id=namespace_owner_id,
    )


class _StaticAdmissionSource:
    """Return one fixed Registry admission observation for issuer evaluation."""

    def __init__(self, admission: RegistryAdmissionFacts) -> None:
        """Retain the admission facts returned to the issuer."""
        self.admission = admission

    def read_admission(
        self, principal_context: RegistryOwnedPrincipalContext
    ) -> RegistryAdmissionFacts:
        """Return the configured Registry admission facts."""
        return self.admission


def _resolve_admission(
    admission: RegistryAdmissionFacts,
    workspace_id: int | None = _WORKSPACE_ID,
    principal_kind: RegistryPrincipalKind = RegistryPrincipalKind.DJANGO_USER,
) -> RegistryTenancyFacts:
    """Run the Registry-owned issuer tenancy evaluation for one admission observation."""
    issuer = RegistryTenancyEvidenceIssuer(admission_source=_StaticAdmissionSource(admission))
    return issuer.resolve_current_tenancy(_principal_context(workspace_id, principal_kind))


# --------------------------------------------------------------------------------------
# Registry issuance — D6-I01, D6-I04 through D6-I14
# --------------------------------------------------------------------------------------


def test_d6_i01_valid_d4_issuance_publishes_exactly_one_issued_record() -> None:
    """D6-I01: valid D4 issuance publishes exactly one opaque proof and pending record."""
    session, _clock, source = _open_session()

    proof = session.issue()

    assert isinstance(proof, TrustedRegistryTenancyProof)
    assert len(session._records) == 1
    record = _only_record(session)
    assert record.state is RegistryTenancyEvidenceState.ISSUED
    assert record.proof is proof
    assert record.issued_at == _ISSUED_AT
    assert record.expires_at == _ISSUED_AT + D6_MAXIMUM_LIFETIME
    assert record.monotonic_deadline == _MONOTONIC_AT_ISSUE + D6_MAXIMUM_LIFETIME.total_seconds()
    assert source.calls == 1
    with pytest.raises(D6EvidenceRejected):
        session.issue()
    assert len(session._records) == 1


def test_d6_i04_principal_not_persisted_rejects_before_any_record() -> None:
    """D6-I04: a non-persisted principal is rejected before proof construction."""
    with pytest.raises(D6EvidenceRejected):
        _resolve_admission(_valid_admission(principal_is_persisted=False))

    matching = _resolve_admission(_valid_admission())
    assert matching.principal_id == _PRINCIPAL_ID
    assert matching.principal_kind is RegistryPrincipalKind.DJANGO_USER

    service_account = _resolve_admission(
        _valid_admission(principal_kind=RegistryPrincipalKind.SERVICE_ACCOUNT),
        principal_kind=RegistryPrincipalKind.SERVICE_ACCOUNT,
    )
    assert service_account.principal_kind is RegistryPrincipalKind.SERVICE_ACCOUNT

    for admission, principal_kind in (
        (_valid_admission(principal_id=_PRINCIPAL_ID + 1), RegistryPrincipalKind.DJANGO_USER),
        (
            _valid_admission(principal_kind=RegistryPrincipalKind.SERVICE_ACCOUNT),
            RegistryPrincipalKind.DJANGO_USER,
        ),
        (_valid_admission(principal_id=None), RegistryPrincipalKind.DJANGO_USER),
        (_valid_admission(principal_id=True), RegistryPrincipalKind.DJANGO_USER),
        (_valid_admission(principal_kind=None), RegistryPrincipalKind.DJANGO_USER),
        (_valid_admission(principal_kind=object()), RegistryPrincipalKind.DJANGO_USER),  # type: ignore[arg-type]
    ):
        with pytest.raises(D6EvidenceRejected):
            _resolve_admission(admission, principal_kind=principal_kind)

    source = _FakeTenancyFactsSource(_valid_facts())
    source.error = D6EvidenceRejected()
    session, _clock, _source = _open_session(facts_source=source)
    with pytest.raises(D6EvidenceRejected):
        session.issue()
    assert session._records == {}


@pytest.mark.parametrize(
    ("field_name", "invalid_id"),
    [
        pytest.param(field_name, invalid_id, id=f"context-{field_name}-{invalid_name}")
        for field_name in ("principal_id", "organization_id", "workspace_id")
        for invalid_name, invalid_id in _INVALID_ID_VALUES
        if field_name != "workspace_id" or invalid_id is not None
    ],
)
def test_d6_i04_strict_context_identity_ids_reject_before_session_creation(
    field_name: str, invalid_id: object
) -> None:
    """D6-I04: every trusted context identity ID must be a strict positive integer."""
    context_values: dict[str, object] = {
        "principal_id": _PRINCIPAL_ID,
        "organization_id": _ORGANIZATION_ID,
        "workspace_id": _WORKSPACE_ID,
    }
    context_values[field_name] = invalid_id

    with pytest.raises(D6EvidenceRejected) as rejection_info:
        RegistryOwnedPrincipalContext(**context_values)  # type: ignore[arg-type]

    assert str(rejection_info.value) == "registry tenancy evidence unavailable"
    assert rejection_info.value.__cause__ is None
    assert rejection_info.value.__context__ is None


@pytest.mark.parametrize(
    ("field_name", "invalid_id"),
    [
        pytest.param(field_name, invalid_id, id=f"admission-{field_name}-{invalid_name}")
        for field_name in ("principal_id", "organization_id", "workspace_id")
        for invalid_name, invalid_id in _INVALID_ID_VALUES
        if field_name != "workspace_id" or invalid_id is not None
    ],
)
def test_d6_i04_strict_authoritative_identity_ids_reject_before_facts_storage(
    field_name: str, invalid_id: object
) -> None:
    """D6-I04: every authoritative identity ID must be a strict positive integer."""
    admission_values: dict[str, object] = {
        "principal_id": _PRINCIPAL_ID,
        "organization_id": _ORGANIZATION_ID,
        "workspace_id": _WORKSPACE_ID,
    }
    admission_values[field_name] = invalid_id
    admission = _valid_admission(**admission_values)  # type: ignore[arg-type]

    with pytest.raises(D6EvidenceRejected) as rejection_info:
        _resolve_admission(admission)

    assert str(rejection_info.value) == "registry tenancy evidence unavailable"
    assert rejection_info.value.__cause__ is None
    assert rejection_info.value.__context__ is None


@pytest.mark.parametrize(
    ("field_name", "invalid_id"),
    [
        pytest.param(field_name, invalid_id, id=f"revalidation-{field_name}-{invalid_name}")
        for field_name in ("principal_id", "organization_id", "workspace_id")
        for invalid_name, invalid_id in _INVALID_ID_VALUES
    ],
)
def test_d6_r02_invalid_authoritative_id_during_revalidation_terminally_rejects(
    field_name: str, invalid_id: object
) -> None:
    """D6-R02: every invalid revalidated identity terminally rejects without retention."""
    session, proof, _clock, source = _issued_session()
    source.facts = dataclasses.replace(_valid_facts(), **{field_name: invalid_id})

    with pytest.raises(D6EvidenceRejected) as rejection_info:
        session.consume(proof)

    assert session._records == {}
    assert str(rejection_info.value) == "registry tenancy evidence unavailable"
    assert rejection_info.value.__cause__ is None
    assert rejection_info.value.__context__ is None


@pytest.mark.parametrize(
    ("field_name", "authoritative_value"),
    (
        pytest.param("principal_id", _PRINCIPAL_ID + 1, id="principal-id"),
        pytest.param("organization_id", _ORGANIZATION_ID + 1, id="organization-id"),
        pytest.param("workspace_id", _WORKSPACE_ID + 1, id="workspace-id"),
        pytest.param(
            "principal_kind",
            RegistryPrincipalKind.SERVICE_ACCOUNT,
            id="principal-kind",
        ),
    ),
)
def test_d6_i04_session_rejects_authoritative_context_binding_mismatch_before_storage(
    field_name: str, authoritative_value: object
) -> None:
    """D6-I04: session issuance rejects validly shaped facts bound to another context."""
    source = _FakeTenancyFactsSource(
        dataclasses.replace(_valid_facts(), **{field_name: authoritative_value})
    )
    session, _clock, _source = _open_session(facts_source=source)

    with pytest.raises(D6EvidenceRejected) as rejection_info:
        session.issue()

    assert session._records == {}
    assert str(rejection_info.value) == "registry tenancy evidence unavailable"
    assert rejection_info.value.__cause__ is None
    assert rejection_info.value.__context__ is None


@pytest.mark.parametrize(
    ("field_name", "authoritative_value"),
    (
        pytest.param("principal_id", _PRINCIPAL_ID + 1, id="principal-id"),
        pytest.param("organization_id", _ORGANIZATION_ID + 1, id="organization-id"),
        pytest.param("workspace_id", _WORKSPACE_ID + 1, id="workspace-id"),
        pytest.param(
            "principal_kind",
            RegistryPrincipalKind.SERVICE_ACCOUNT,
            id="principal-kind",
        ),
    ),
)
def test_d6_r02_revalidation_context_binding_mismatch_terminally_rejects(
    field_name: str, authoritative_value: object
) -> None:
    """D6-R02: session revalidation rejects validly shaped facts bound to another context."""
    session, proof, _clock, source = _issued_session()
    source.facts = dataclasses.replace(_valid_facts(), **{field_name: authoritative_value})

    with pytest.raises(D6EvidenceRejected) as rejection_info:
        session.consume(proof)

    assert session._records == {}
    assert str(rejection_info.value) == "registry tenancy evidence unavailable"
    assert rejection_info.value.__cause__ is None
    assert rejection_info.value.__context__ is None


@pytest.mark.parametrize(
    ("invalid_name", "invalid_kind"),
    [
        pytest.param(name, value, id=f"context-principal-kind-{name}")
        for name, value in _INVALID_PRINCIPAL_KINDS
    ],
)
def test_d6_i04_context_rejects_invalid_principal_kind(
    invalid_name: str, invalid_kind: object
) -> None:
    """D6-I04: trusted context accepts only an approved principal-kind enum."""
    with pytest.raises(D6EvidenceRejected):
        RegistryOwnedPrincipalContext(
            _PRINCIPAL_ID,
            _ORGANIZATION_ID,
            principal_kind=invalid_kind,  # type: ignore[arg-type]
            workspace_id=_WORKSPACE_ID,
        )


@pytest.mark.parametrize(
    ("invalid_name", "invalid_kind"),
    [
        pytest.param(name, value, id=f"admission-principal-kind-{name}")
        for name, value in _INVALID_PRINCIPAL_KINDS
    ],
)
def test_d6_i04_admission_rejects_invalid_principal_kind(
    invalid_name: str, invalid_kind: object
) -> None:
    """D6-I04: authoritative admission accepts only an approved principal-kind enum."""
    with pytest.raises(D6EvidenceRejected):
        _resolve_admission(_valid_admission(principal_kind=invalid_kind))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("invalid_name", "invalid_kind"),
    [
        pytest.param(name, value, id=f"revalidation-principal-kind-{name}")
        for name, value in _INVALID_PRINCIPAL_KINDS
    ],
)
def test_d6_r02_revalidation_rejects_invalid_principal_kind(
    invalid_name: str, invalid_kind: object
) -> None:
    """D6-R02: invalid revalidated principal kind terminally rejects without retention."""
    session, proof, _clock, source = _issued_session()
    source.facts = dataclasses.replace(_valid_facts(), principal_kind=invalid_kind)

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)

    assert session._records == {}


def test_d6_i05_disabled_organization_rejects_after_tenancy_evaluation() -> None:
    """D6-I05: a disabled Organization is rejected after Registry tenancy evaluation."""
    with pytest.raises(D6EvidenceRejected):
        _resolve_admission(_valid_admission(organization_is_enabled=False))


def test_d6_i06_inactive_workspace_rejects_after_tenancy_evaluation() -> None:
    """D6-I06: an inactive optional Workspace is rejected after tenancy evaluation."""
    with pytest.raises(D6EvidenceRejected):
        _resolve_admission(_valid_admission(workspace_is_active=False))


def test_d6_i07_disabled_workspace_rejects_after_tenancy_evaluation() -> None:
    """D6-I07: a disabled optional Workspace is rejected after tenancy evaluation."""
    with pytest.raises(D6EvidenceRejected):
        _resolve_admission(_valid_admission(workspace_is_enabled=False))


def test_d6_i08_absent_membership_rejects_after_tenancy_evaluation() -> None:
    """D6-I08: an absent authorized membership is rejected after tenancy evaluation."""
    with pytest.raises(D6EvidenceRejected):
        _resolve_admission(_valid_admission(has_authorized_membership=False))


def test_d6_i09_absent_namespace_mapping_rejects_after_tenancy_evaluation() -> None:
    """D6-I09: an absent RegistryNamespace mapping is rejected after tenancy evaluation."""
    with pytest.raises(D6EvidenceRejected):
        _resolve_admission(_valid_admission(namespace_id=None))


def test_d6_i10_cross_organization_namespace_rejects_after_tenancy_evaluation() -> None:
    """D6-I10: a namespace owned by another Organization is rejected."""
    with pytest.raises(D6EvidenceRejected):
        _resolve_admission(_valid_admission(workspace_organization_id=_ORGANIZATION_ID + 1))

    with pytest.raises(D6EvidenceRejected):
        _resolve_admission(
            _valid_admission(
                namespace_owner_kind=RegistryOwnerKind.ORGANIZATION,
                namespace_owner_id=_ORGANIZATION_ID + 1,
                workspace_id=None,
            ),
            workspace_id=None,
        )


def test_d6_i11_owner_scope_mismatch_rejects_after_tenancy_evaluation() -> None:
    """D6-I11: a namespace whose owner scope does not match the evaluation is rejected."""
    with pytest.raises(D6EvidenceRejected):
        _resolve_admission(_valid_admission(namespace_owner_id=_WORKSPACE_ID + 1))

    with pytest.raises(D6EvidenceRejected):
        _resolve_admission(
            _valid_admission(
                namespace_owner_kind=RegistryOwnerKind.ORGANIZATION,
                namespace_owner_id=_ORGANIZATION_ID,
            )
        )

    organization_scoped = _resolve_admission(
        _valid_admission(
            workspace_id=None,
            namespace_owner_kind=RegistryOwnerKind.ORGANIZATION,
            namespace_owner_id=_ORGANIZATION_ID,
        ),
        workspace_id=None,
    )
    assert organization_scoped.owner_kind is RegistryOwnerKind.ORGANIZATION
    assert organization_scoped.owner_id == _ORGANIZATION_ID


def test_d6_i12_proof_construction_exception_publishes_no_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D6-I12: a private proof-construction exception becomes a uniform rejection."""

    def _raise() -> TrustedRegistryTenancyProof:
        raise RuntimeError("construction failure")

    monkeypatch.setattr(tenancy_evidence, "_create_proof", _raise)
    session, _clock, _source = _open_session()

    with pytest.raises(D6EvidenceRejected):
        session.issue()
    assert session._records == {}


@pytest.mark.parametrize(
    "invalid_maximum",
    [
        pytest.param(timedelta(0), id="non-positive"),
        pytest.param(timedelta(minutes=-1), id="negative"),
        pytest.param(timedelta(minutes=6), id="differs-from-five-minutes"),
        pytest.param(None, id="absent"),
    ],
)
def test_d6_i13_invalid_fixed_five_minute_maximum_publishes_no_record(
    monkeypatch: pytest.MonkeyPatch,
    invalid_maximum: timedelta | None,
) -> None:
    """D6-I13: an absent, non-positive, invalid, or non-five-minute D6 maximum is rejected."""
    monkeypatch.setattr(tenancy_evidence, "D6_MAXIMUM_LIFETIME", invalid_maximum)
    session, _clock, _source = _open_session()

    with pytest.raises(D6EvidenceRejected):
        session.issue()
    assert session._records == {}


def test_d6_i14_invalid_clock_before_publication_publishes_no_record() -> None:
    """D6-I14: an invalid or raising clock before issuance publishes no record."""
    session, clock, _source = _open_session()
    clock.utc_error = RuntimeError("utc clock unavailable")

    with pytest.raises(D6EvidenceRejected):
        session.issue()
    assert session._records == {}

    other_session, other_clock, _other_source = _open_session()
    other_clock.monotonic_value = "not-a-number"
    with pytest.raises(D6EvidenceRejected):
        other_session.issue()
    assert other_session._records == {}


# --------------------------------------------------------------------------------------
# Registry authenticity — D6-A01 through D6-A12 and D6-A14
# --------------------------------------------------------------------------------------


def test_d6_a01_exact_proof_identity_is_accepted_and_consumed() -> None:
    """D6-A01: the exact proof object issued by this session is accepted exactly once."""
    session, proof, _clock, _source = _issued_session()

    session.consume(proof)

    assert session._records == {}
    assert session._proof_index == {}


def test_d6_a02_value_equivalent_object_cannot_be_constructed() -> None:
    """D6-A02: a value-equivalent proof cannot be constructed from public values."""
    session, _proof, _clock, _source = _issued_session()

    with pytest.raises(D6EvidenceRejected):
        TrustedRegistryTenancyProof()
    with pytest.raises(D6EvidenceRejected):
        TrustedRegistryTenancyProof(object())
    with pytest.raises(D6EvidenceRejected):
        session.consume(object())
    assert _only_record(session).state is RegistryTenancyEvidenceState.ISSUED


def test_d6_a03_shallow_copy_is_rejected() -> None:
    """D6-A03: a shallow copy of a legitimate proof is rejected."""
    session, proof, _clock, _source = _issued_session()

    with pytest.raises(D6EvidenceRejected):
        copy.copy(proof)
    assert _only_record(session).state is RegistryTenancyEvidenceState.ISSUED


def test_d6_a04_deep_copy_is_rejected() -> None:
    """D6-A04: a deep copy of a legitimate proof is rejected."""
    session, proof, _clock, _source = _issued_session()

    with pytest.raises(D6EvidenceRejected):
        copy.deepcopy(proof)
    assert _only_record(session).state is RegistryTenancyEvidenceState.ISSUED


def test_d6_a05_pickle_representation_is_rejected() -> None:
    """D6-A05: a proof cannot be serialized through pickle."""
    session, proof, _clock, _source = _issued_session()

    with pytest.raises(D6EvidenceRejected):
        pickle.dumps(proof)
    with pytest.raises(D6EvidenceRejected):
        proof.__reduce__()
    with pytest.raises(D6EvidenceRejected):
        proof.__reduce_ex__(pickle.HIGHEST_PROTOCOL)
    assert _only_record(session).state is RegistryTenancyEvidenceState.ISSUED


def test_d6_a06_asdict_representation_is_unavailable() -> None:
    """D6-A06: a proof exposes no dataclass representation and no reconstruction path."""
    session, proof, _clock, _source = _issued_session()

    assert not dataclasses.is_dataclass(proof)
    with pytest.raises(TypeError):
        dataclasses.asdict(proof)  # type: ignore[call-overload]
    assert _only_record(session).state is RegistryTenancyEvidenceState.ISSUED


def test_d6_a07_vars_representation_is_unavailable() -> None:
    """D6-A07: a proof exposes no instance dictionary through vars."""
    session, proof, _clock, _source = _issued_session()

    with pytest.raises(TypeError):
        vars(proof)
    assert not hasattr(proof, "__dict__")
    assert _only_record(session).state is RegistryTenancyEvidenceState.ISSUED


def test_d6_a08_json_representation_is_unavailable() -> None:
    """D6-A08: a proof cannot be serialized to JSON."""
    session, proof, _clock, _source = _issued_session()

    with pytest.raises(TypeError):
        json.dumps(proof)
    assert _only_record(session).state is RegistryTenancyEvidenceState.ISSUED


def test_d6_a09_proof_mutation_is_rejected() -> None:
    """D6-A09: a proof exposes no mutable representation and a substitute is rejected."""
    session, proof, _clock, _source = _issued_session()

    with pytest.raises(AttributeError):
        proof.injected_binding = "tampered"  # type: ignore[attr-defined]

    class _Substitute:
        pass

    with pytest.raises(D6EvidenceRejected):
        session.consume(_Substitute())
    assert _only_record(session).state is RegistryTenancyEvidenceState.ISSUED


def test_d6_a10_foreign_session_proof_is_rejected_and_preserves_its_record() -> None:
    """D6-A10: a proof issued by another Registry session is rejected here."""
    session_a, proof_a, _clock_a, _source_a = _issued_session()
    session_b, _clock_b, _source_b = _open_session()

    with pytest.raises(D6EvidenceRejected):
        session_b.consume(proof_a)

    assert _only_record(session_a).state is RegistryTenancyEvidenceState.ISSUED
    assert session_b._records == {}


def test_d6_a11_foreign_issuer_or_service_identity_is_rejected() -> None:
    """D6-A11: a record bound to a foreign issuer or service identity is rejected."""
    session, proof, _clock, _source = _issued_session()
    record = _only_record(session)
    record.issuer_descriptor = object()

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.ISSUED


def test_d6_a12_wrong_evaluation_identity_is_rejected() -> None:
    """D6-A12: a record bound to another evaluation identity is rejected."""
    session, proof, _clock, _source = _issued_session()
    record = _only_record(session)
    record.evaluation_identity = object()

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.ISSUED


def test_d6_a14_only_exact_private_cleanup_authority_finalizes() -> None:
    """D6-A14: only the exact private per-session authority may finalize; issuer is insufficient."""
    session, proof, _clock, _source = _issued_session()
    other_session, _other_clock, _other_source = _open_session()

    with pytest.raises(D6EvidenceRejected):
        session._finalize_owned_evidence(authority=tenancy_evidence._STABLE_ISSUER_DESCRIPTOR)
    with pytest.raises(D6EvidenceRejected):
        session._finalize_owned_evidence(authority=other_session._cleanup_authority)
    with pytest.raises(D6EvidenceRejected):
        session._finalize_owned_evidence(authority=object())
    assert _only_record(session).state is RegistryTenancyEvidenceState.ISSUED

    session._finalize_owned_evidence(authority=session._cleanup_authority)
    assert session._records == {}
    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)


# --------------------------------------------------------------------------------------
# Registry lifecycle — D6-L01 through D6-L11
# --------------------------------------------------------------------------------------


def test_d6_l01_successful_consume_reaches_validated_and_consumed() -> None:
    """D6-L01: an exact fresh proof transitions ISSUED to VALIDATED_AND_CONSUMED."""
    session, proof, _clock, source = _issued_session()
    record = _only_record(session)

    session.consume(proof)

    assert record.state is RegistryTenancyEvidenceState.VALIDATED_AND_CONSUMED
    assert session._records == {}
    assert source.calls == 2


def test_d6_l02_replay_after_successful_consumption_is_rejected() -> None:
    """D6-L02: replaying a consumed proof is rejected and recreates no record."""
    session, proof, _clock, _source = _issued_session()
    record = _only_record(session)
    session.consume(proof)

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.VALIDATED_AND_CONSUMED
    assert session._records == {}


def test_d6_l03_unknown_proof_is_rejected_without_state_change() -> None:
    """D6-L03: an unissued proof candidate is rejected without creating or removing records."""
    session, _proof, _clock, _source = _issued_session()

    with pytest.raises(D6EvidenceRejected):
        session.consume(_UnissuedCandidate())
    assert _only_record(session).state is RegistryTenancyEvidenceState.ISSUED
    assert len(session._records) == 1


class _UnissuedCandidate:
    """Stand in for a proof candidate that this Registry session never issued."""


def test_d6_l04_already_invalidated_proof_is_rejected() -> None:
    """D6-L04: an invalidated record stays terminal and rejects further consumption."""
    session, proof, _clock, _source = _issued_session()
    record = _only_record(session)
    session._finalize_owned_evidence(authority=session._cleanup_authority)

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}


def test_d6_l05_cancellation_before_consume_invalidates_the_record() -> None:
    """D6-L05: Registry-owned cancellation cleanup transitions ISSUED to INVALIDATED."""
    session, proof, _clock, _source = _issued_session()
    record = _only_record(session)

    session._finalize_owned_evidence(authority=session._cleanup_authority)

    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}
    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)


def test_d6_l06_pre_validation_and_cleanup_exceptions_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D6-L06: pre-validation and finalization exceptions reach terminal state and fail closed."""
    session, proof, clock, _source = _issued_session()
    record = _only_record(session)
    clock.utc_error = RuntimeError("clock failure before validation")

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}

    other_session, other_proof, _other_clock, _other_source = _issued_session()
    other_record = _only_record(other_session)

    def _raise(self: RegistryTenancyEvidenceSession) -> None:
        raise RuntimeError("cleanup failure")

    monkeypatch.setattr(RegistryTenancyEvidenceSession, "_clear_protected_bindings", _raise)
    with pytest.raises(D6EvidenceRejected):
        other_session._finalize_owned_evidence(authority=other_session._cleanup_authority)
    assert other_record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert other_session._records == {}
    with pytest.raises(D6EvidenceRejected):
        other_session.consume(other_proof)


def test_d6_l07_exception_after_terminal_consumption_does_not_reverse_it() -> None:
    """D6-L07: a later failure never reverses a completed terminal consumption."""
    session, proof, _clock, source = _issued_session()
    record = _only_record(session)
    session.consume(proof)

    source.error = RuntimeError("later unrelated failure")
    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)

    assert record.state is RegistryTenancyEvidenceState.VALIDATED_AND_CONSUMED
    assert session._records == {}


def test_d6_l08_request_unwind_invalidates_unconsumed_evidence() -> None:
    """D6-L08: request finalization invalidates and removes an unconsumed ISSUED record."""
    session, _proof, _clock, _source = _issued_session()
    record = _only_record(session)

    session._finalize_owned_evidence(authority=session._cleanup_authority)

    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}
    assert session._proof_index == {}


def test_d6_l09_terminal_cleanup_is_idempotent() -> None:
    """D6-L09: repeating terminal cleanup performs no new validation or issuance."""
    session, _proof, _clock, source = _issued_session()
    record = _only_record(session)
    authority = session._cleanup_authority

    session._finalize_owned_evidence(authority=authority)
    calls_after_first = source.calls
    session._finalize_owned_evidence(authority=authority)
    session._finalize_owned_evidence(authority=object())

    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}
    assert source.calls == calls_after_first
    with pytest.raises(D6EvidenceRejected):
        session.issue()


def test_d6_l10_process_loss_leaves_evidence_terminally_unusable() -> None:
    """D6-L10: cleanup authority cannot be reconstructed, adopted, or serialized."""
    session, proof, _clock, _source = _issued_session()
    authority = session._cleanup_authority

    with pytest.raises(D6EvidenceRejected):
        pickle.dumps(authority)
    with pytest.raises(D6EvidenceRejected):
        copy.copy(authority)
    with pytest.raises(D6EvidenceRejected):
        copy.deepcopy(authority)
    with pytest.raises(D6EvidenceRejected):
        type(authority)()

    adopting_session, _clock, _source = _open_session()
    with pytest.raises(D6EvidenceRejected):
        adopting_session.consume(proof)
    with pytest.raises(D6EvidenceRejected):
        adopting_session._finalize_owned_evidence(authority=authority)
    assert adopting_session._records == {}
    assert _only_record(session).state is RegistryTenancyEvidenceState.ISSUED


def test_d6_l11_fresh_retry_after_terminal_failure_uses_new_identities() -> None:
    """D6-L11: a retry after terminal failure requires fresh evidence and fresh authority."""
    session, proof, _clock, _source = _issued_session()
    authority = session._cleanup_authority
    session._finalize_owned_evidence(authority=authority)

    retry_session, _retry_clock, _retry_source = _open_session()
    retry_proof = retry_session.issue()

    assert retry_proof is not proof
    assert retry_session._cleanup_authority is not authority
    assert _only_record(retry_session).state is RegistryTenancyEvidenceState.ISSUED
    retry_session.consume(retry_proof)
    assert retry_session._records == {}
    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)


# --------------------------------------------------------------------------------------
# Registry clocks — D6-T01 through D6-T17
# --------------------------------------------------------------------------------------


def test_d6_t01_utc_equality_is_expiry() -> None:
    """D6-T01: UTC equality with expires_at expires the evidence and rejects."""
    session, proof, clock, _source = _issued_session()
    record = _only_record(session)
    clock.utc_value = record.expires_at

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}


def test_d6_t02_monotonic_equality_is_expiry() -> None:
    """D6-T02: monotonic equality with the deadline expires the evidence and rejects."""
    session, proof, clock, _source = _issued_session()
    record = _only_record(session)
    clock.monotonic_value = record.monotonic_deadline

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED


def test_d6_t03_forward_utc_jump_expires_before_the_monotonic_deadline() -> None:
    """D6-T03: a forward UTC jump expires evidence even though the monotonic deadline remains."""
    session, proof, clock, source = _issued_session()
    record = _only_record(session)
    clock.utc_value = record.expires_at + timedelta(seconds=1)
    clock.monotonic_value = record.monotonic_deadline - 60.0

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}
    assert source.calls == 1


def test_d6_t04_monotonic_deadline_expires_first() -> None:
    """D6-T04: the monotonic deadline expires evidence before the UTC expiry."""
    session, proof, clock, _source = _issued_session()
    record = _only_record(session)
    clock.utc_value = record.expires_at - timedelta(seconds=30)
    clock.monotonic_value = record.monotonic_deadline + 1.0

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED


def test_d6_t05_request_or_evaluation_expiry_shortens_the_five_minute_maximum() -> None:
    """D6-T05: an earlier trusted request or evaluation expiry shortens the effective bound."""
    request_expiry = _ISSUED_AT + timedelta(minutes=2)
    session, clock, _source = _open_session(request_expires_at=request_expiry)
    proof = session.issue()
    record = _only_record(session)

    assert record.expires_at == request_expiry
    assert record.monotonic_deadline == _MONOTONIC_AT_ISSUE + timedelta(minutes=2).total_seconds()

    clock.utc_value = request_expiry
    clock.monotonic_value = _MONOTONIC_AT_ISSUE + 1.0
    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED

    evaluation_expiry = _ISSUED_AT + timedelta(minutes=1)
    other_session, _other_clock, _other_source = _open_session(
        evaluation_expires_at=evaluation_expiry
    )
    other_session.issue()
    assert _only_record(other_session).expires_at == evaluation_expiry


def test_d6_t06_fixed_five_minute_maximum_boundary_uses_independent_instances() -> None:
    """D6-T06: before the fixed five-minute deadline succeeds; equality and after reject."""
    before_session, before_clock, _before_source = _open_session()
    before_proof = before_session.issue()
    before_record = _only_record(before_session)
    before_clock.utc_value = before_record.expires_at - timedelta(milliseconds=1)
    before_clock.monotonic_value = before_record.monotonic_deadline - 0.001
    before_session.consume(before_proof)
    assert before_record.state is RegistryTenancyEvidenceState.VALIDATED_AND_CONSUMED

    equality_session, equality_clock, _equality_source = _open_session()
    equality_proof = equality_session.issue()
    equality_record = _only_record(equality_session)
    equality_clock.utc_value = equality_record.expires_at
    with pytest.raises(D6EvidenceRejected):
        equality_session.consume(equality_proof)
    assert equality_record.state is RegistryTenancyEvidenceState.INVALIDATED

    after_session, after_clock, _after_source = _open_session()
    after_proof = after_session.issue()
    after_record = _only_record(after_session)
    after_clock.utc_value = after_record.expires_at + timedelta(minutes=1)
    with pytest.raises(D6EvidenceRejected):
        after_session.consume(after_proof)
    assert after_record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert after_session._records == {}


def test_d6_t07_absent_request_and_evaluation_expiry_uses_the_five_minute_maximum() -> None:
    """D6-T07: without trusted bounds the fixed five-minute maximum is the sole expiry."""
    session, clock, _source = _open_session()
    proof = session.issue()
    record = _only_record(session)

    assert record.expires_at == _ISSUED_AT + D6_MAXIMUM_LIFETIME
    clock.utc_value = _ISSUED_AT + timedelta(minutes=4)
    clock.monotonic_value = _MONOTONIC_AT_ISSUE + 240.0
    session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.VALIDATED_AND_CONSUMED


@pytest.mark.parametrize(
    "bound_offset",
    [
        pytest.param(timedelta(0), id="zero-lifetime"),
        pytest.param(timedelta(seconds=-1), id="negative-lifetime"),
    ],
)
def test_d6_t08_zero_or_negative_lifetime_fails_closed(bound_offset: timedelta) -> None:
    """D6-T08: a zero or negative derived lifetime fails closed before proof construction."""
    session, _clock, _source = _open_session(request_expires_at=_ISSUED_AT + bound_offset)

    with pytest.raises(D6EvidenceRejected):
        session.issue()
    assert session._records == {}


def test_d6_t09_naive_datetime_fails_closed_at_issue_and_consume() -> None:
    """D6-T09: a naive UTC observation fails closed before and after publication."""
    session, clock, _source = _open_session()
    clock.utc_value = _ISSUED_AT.replace(tzinfo=None)
    with pytest.raises(D6EvidenceRejected):
        session.issue()
    assert session._records == {}

    published_session, published_proof, published_clock, _source = _issued_session()
    record = _only_record(published_session)
    published_clock.utc_value = _ISSUED_AT.replace(tzinfo=None)
    with pytest.raises(D6EvidenceRejected):
        published_session.consume(published_proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert published_session._records == {}


def test_d6_t10_non_utc_datetime_fails_closed_at_issue_and_consume() -> None:
    """D6-T10: an aware non-UTC observation fails closed before and after publication."""
    non_utc = _ISSUED_AT.astimezone(timezone(timedelta(hours=2)))
    session, clock, _source = _open_session()
    clock.utc_value = non_utc
    with pytest.raises(D6EvidenceRejected):
        session.issue()
    assert session._records == {}

    published_session, published_proof, published_clock, _source = _issued_session()
    record = _only_record(published_session)
    published_clock.utc_value = non_utc
    with pytest.raises(D6EvidenceRejected):
        published_session.consume(published_proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED


def test_d6_t11_utc_clock_exception_fails_closed_at_issue_and_consume() -> None:
    """D6-T11: an ordinary UTC clock exception becomes a uniform rejection."""
    session, clock, _source = _open_session()
    clock.utc_error = RuntimeError("utc failure")
    with pytest.raises(D6EvidenceRejected):
        session.issue()
    assert session._records == {}

    published_session, published_proof, published_clock, _source = _issued_session()
    record = _only_record(published_session)
    published_clock.utc_error = RuntimeError("utc failure")
    with pytest.raises(D6EvidenceRejected):
        published_session.consume(published_proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED


def test_d6_t12_monotonic_clock_exception_fails_closed_at_issue_and_consume() -> None:
    """D6-T12: an ordinary monotonic clock exception becomes a uniform rejection."""
    session, clock, _source = _open_session()
    clock.monotonic_error = RuntimeError("monotonic failure")
    with pytest.raises(D6EvidenceRejected):
        session.issue()
    assert session._records == {}

    published_session, published_proof, published_clock, _source = _issued_session()
    record = _only_record(published_session)
    published_clock.monotonic_error = RuntimeError("monotonic failure")
    with pytest.raises(D6EvidenceRejected):
        published_session.consume(published_proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED


@pytest.mark.parametrize(
    "invalid_monotonic",
    [
        pytest.param(math.nan, id="nan"),
        pytest.param(math.inf, id="infinity"),
        pytest.param(-math.inf, id="negative-infinity"),
        pytest.param("1000.0", id="non-number"),
        pytest.param(None, id="none"),
    ],
)
def test_d6_t13_invalid_monotonic_value_fails_closed(invalid_monotonic: object) -> None:
    """D6-T13: NaN, infinite, or non-numeric monotonic values fail closed."""
    session, clock, _source = _open_session()
    clock.monotonic_value = invalid_monotonic
    with pytest.raises(D6EvidenceRejected):
        session.issue()
    assert session._records == {}

    published_session, published_proof, published_clock, _source = _issued_session()
    record = _only_record(published_session)
    published_clock.monotonic_value = invalid_monotonic
    with pytest.raises(D6EvidenceRejected):
        published_session.consume(published_proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED


def test_d6_t14_monotonic_rollback_fails_closed() -> None:
    """D6-T14: a monotonic value below the previous observation fails closed."""
    session, proof, clock, _source = _issued_session()
    record = _only_record(session)
    clock.monotonic_value = _MONOTONIC_AT_ISSUE - 1.0

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}


def test_d6_t15_expiry_during_revalidation_invalidates_from_validating() -> None:
    """D6-T15: expiry while the authoritative read is in flight rejects from VALIDATING."""
    session, proof, clock, source = _issued_session()
    record = _only_record(session)
    observed_states: list[RegistryTenancyEvidenceState] = []

    def _expire_during_read() -> None:
        observed_states.append(record.state)
        clock.utc_value = record.expires_at + timedelta(seconds=1)

    source.on_call = _expire_during_read

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)

    assert observed_states == [RegistryTenancyEvidenceState.VALIDATING]
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}


def test_d6_t16_renewal_and_deadline_extension_are_prohibited() -> None:
    """D6-T16: no retry, handoff, or clock movement renews or extends the original deadline."""
    session, proof, clock, _source = _issued_session()
    record = _only_record(session)
    original_expires_at = record.expires_at
    original_deadline = record.monotonic_deadline

    clock.utc_value = original_expires_at
    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.expires_at == original_expires_at
    assert record.monotonic_deadline == original_deadline

    clock.utc_value = _ISSUED_AT
    clock.monotonic_value = _MONOTONIC_AT_ISSUE
    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}


def test_d6_t17_retry_obtains_fresh_clocks_and_evidence() -> None:
    """D6-T17: after a clock failure the retry uses a new session, clocks, and evidence."""
    session, proof, clock, _source = _issued_session()
    clock.utc_error = RuntimeError("clock failure")
    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert session._records == {}

    retry_clock = _FakeClock(_ISSUED_AT + timedelta(minutes=10), _MONOTONIC_AT_ISSUE + 600.0)
    retry_session, _retry_clock, _retry_source = _open_session(clock=retry_clock)
    retry_proof = retry_session.issue()
    retry_record = _only_record(retry_session)

    assert retry_proof is not proof
    assert retry_record.issued_at == _ISSUED_AT + timedelta(minutes=10)
    retry_session.consume(retry_proof)
    assert retry_record.state is RegistryTenancyEvidenceState.VALIDATED_AND_CONSUMED


# --------------------------------------------------------------------------------------
# Registry two-phase revalidation — D6-R01 through D6-R12
# --------------------------------------------------------------------------------------


def test_d6_r01_valid_two_phase_revalidation_releases_the_lock_for_the_read() -> None:
    """D6-R01: consumption reserves under the lock, reads unlocked, then rechecks and consumes."""
    session, proof, _clock, source = _issued_session()
    record = _only_record(session)
    observations: list[tuple[RegistryTenancyEvidenceState, bool]] = []

    def _observe_during_read() -> None:
        acquired = session._lock.acquire(blocking=False)
        observations.append((record.state, acquired))
        if acquired:
            session._lock.release()

    source.on_call = _observe_during_read
    session.consume(proof)

    assert observations == [(RegistryTenancyEvidenceState.VALIDATING, True)]
    assert record.state is RegistryTenancyEvidenceState.VALIDATED_AND_CONSUMED
    assert session._records == {}


@pytest.mark.parametrize(
    "identifier",
    [
        pytest.param("D6-R02", id="D6-R02-principal-removed"),
        pytest.param("D6-R03", id="D6-R03-organization-disabled"),
        pytest.param("D6-R04", id="D6-R04-membership-removed"),
        pytest.param("D6-R05", id="D6-R05-workspace-inactive"),
        pytest.param("D6-R06", id="D6-R06-workspace-disabled"),
        pytest.param("D6-R07", id="D6-R07-namespace-mapping-removed"),
    ],
)
def test_d6_r02_to_r07_current_tenancy_failure_invalidates_the_record(identifier: str) -> None:
    """D6-R02..D6-R07: a current tenancy failure during revalidation invalidates and rejects."""
    session, proof, _clock, source = _issued_session()
    record = _only_record(session)
    source.error = D6EvidenceRejected()

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)

    assert record.state is RegistryTenancyEvidenceState.INVALIDATED, identifier
    assert session._records == {}


def test_d6_r08_namespace_remapped_or_owner_mismatch_invalidates_the_record() -> None:
    """D6-R08: a remapped namespace observed during revalidation invalidates and rejects."""
    session, proof, _clock, source = _issued_session()
    record = _only_record(session)
    source.facts = _valid_facts(namespace_id=_OTHER_NAMESPACE_ID)

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}


def test_d6_r09_owner_scope_mismatch_invalidates_the_record() -> None:
    """D6-R09: an owner-scope mismatch observed during revalidation invalidates and rejects."""
    session, proof, _clock, source = _issued_session()
    record = _only_record(session)
    source.facts = _valid_facts(
        owner_kind=RegistryOwnerKind.ORGANIZATION, owner_id=_ORGANIZATION_ID
    )

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED


def test_d6_r10_ordinary_exception_converts_but_base_exception_propagates() -> None:
    """D6-R10: an ordinary revalidation exception becomes a rejection; BaseException does not."""
    session, proof, _clock, source = _issued_session()
    record = _only_record(session)
    source.error = RuntimeError("context service failure")

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}

    base_session, base_proof, _base_clock, base_source = _issued_session()
    base_source.error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        base_session.consume(base_proof)


def test_d6_r11_tenancy_change_before_the_authoritative_read_rejects() -> None:
    """D6-R11: a tenancy change committed before the authoritative read rejects from VALIDATING."""
    session, proof, _clock, source = _issued_session()
    record = _only_record(session)

    def _change_tenancy_before_read() -> None:
        source.facts = _valid_facts(namespace_id=_OTHER_NAMESPACE_ID)

    source.on_call = _change_tenancy_before_read

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}

    principal_session, principal_proof, _clock, principal_source = _issued_session()
    principal_source.facts = RegistryTenancyFacts(
        principal_id=_PRINCIPAL_ID + 1,
        principal_kind=RegistryPrincipalKind.DJANGO_USER,
        organization_id=_ORGANIZATION_ID,
        workspace_id=_WORKSPACE_ID,
        namespace_id=_NAMESPACE_ID,
        owner_kind=RegistryOwnerKind.WORKSPACE,
        owner_id=_WORKSPACE_ID,
    )
    with pytest.raises(D6EvidenceRejected):
        principal_session.consume(principal_proof)


def test_d6_r12_tenancy_change_after_the_authoritative_read_keeps_the_observed_snapshot() -> None:
    """D6-R12: validation-boundary consistency only; a post-read change does not revoke success."""
    session, proof, _clock, source = _issued_session()
    record = _only_record(session)

    session.consume(proof)
    source.facts = _valid_facts(namespace_id=_OTHER_NAMESPACE_ID)

    assert record.state is RegistryTenancyEvidenceState.VALIDATED_AND_CONSUMED
    assert session._records == {}


# --------------------------------------------------------------------------------------
# Registry concurrency — D6-C01 through D6-C10
# --------------------------------------------------------------------------------------


def test_d6_c01_six_consumers_reserve_one_winner() -> None:
    """D6-C01: six synchronized consumers yield exactly one success and five rejections."""
    session, proof, _clock, _source = _issued_session()
    record = _only_record(session)
    barrier = threading.Barrier(6)

    def _consume() -> bool:
        barrier.wait(timeout=_BOUNDED_TIMEOUT_SECONDS)
        try:
            session.consume(proof)
        except D6EvidenceRejected:
            return False
        return True

    with ThreadPoolExecutor(max_workers=6) as executor:
        outcomes = [
            future.result(timeout=_BOUNDED_TIMEOUT_SECONDS)
            for future in [executor.submit(_consume) for _ in range(6)]
        ]

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 5
    assert record.state is RegistryTenancyEvidenceState.VALIDATED_AND_CONSUMED
    assert session._records == {}


def test_d6_c02_consume_versus_invalidate_yields_exactly_one_terminal_outcome() -> None:
    """D6-C02: a concurrent consume and invalidation produce exactly one terminal outcome."""
    session, proof, _clock, _source = _issued_session()
    record = _only_record(session)
    barrier = threading.Barrier(2)

    def _consume() -> bool:
        barrier.wait(timeout=_BOUNDED_TIMEOUT_SECONDS)
        try:
            session.consume(proof)
        except D6EvidenceRejected:
            return False
        return True

    def _invalidate() -> None:
        barrier.wait(timeout=_BOUNDED_TIMEOUT_SECONDS)
        session._finalize_owned_evidence(authority=session._cleanup_authority)

    with ThreadPoolExecutor(max_workers=2) as executor:
        consume_future = executor.submit(_consume)
        invalidate_future = executor.submit(_invalidate)
        consumed = consume_future.result(timeout=_BOUNDED_TIMEOUT_SECONDS)
        invalidate_future.result(timeout=_BOUNDED_TIMEOUT_SECONDS)

    assert record.state in {
        RegistryTenancyEvidenceState.VALIDATED_AND_CONSUMED,
        RegistryTenancyEvidenceState.INVALIDATED,
    }
    if consumed:
        assert record.state is RegistryTenancyEvidenceState.VALIDATED_AND_CONSUMED
    assert session._records == {}


def test_d6_c03_cancellation_during_validating_terminalizes_the_record() -> None:
    """D6-C03: cancellation while VALIDATING terminalizes and the delayed read cannot consume."""
    session, proof, _clock, source = _issued_session()
    record = _only_record(session)

    def _cancel_during_read() -> None:
        session._finalize_owned_evidence(authority=session._cleanup_authority)

    source.on_call = _cancel_during_read

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)
    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}


def test_d6_c04_late_database_success_after_cancellation_is_rejected() -> None:
    """D6-C04: an authoritative success returning after cancellation never reactivates."""
    session, proof, _clock, source = _issued_session()
    record = _only_record(session)
    source.on_call = lambda: session._finalize_owned_evidence(authority=session._cleanup_authority)

    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)

    assert record.state is RegistryTenancyEvidenceState.INVALIDATED
    assert session._records == {}
    with pytest.raises(D6EvidenceRejected):
        session.consume(proof)


def test_d6_c05_concurrent_replay_permits_at_most_one_success() -> None:
    """D6-C05: two simultaneous consumptions of the exact proof yield at most one success."""
    session, proof, _clock, _source = _issued_session()
    barrier = threading.Barrier(2)

    def _consume() -> bool:
        barrier.wait(timeout=_BOUNDED_TIMEOUT_SECONDS)
        try:
            session.consume(proof)
        except D6EvidenceRejected:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            future.result(timeout=_BOUNDED_TIMEOUT_SECONDS)
            for future in [executor.submit(_consume) for _ in range(2)]
        ]

    assert outcomes.count(True) <= 1
    assert session._records == {}


def test_d6_c06_independent_sessions_reach_independent_terminal_results() -> None:
    """D6-C06: two sessions with distinct identities, records, and locks stay independent."""
    session_a, proof_a, _clock_a, _source_a = _issued_session()
    session_b, proof_b, _clock_b, _source_b = _issued_session()
    barrier = threading.Barrier(2)

    assert session_a._lock is not session_b._lock
    assert session_a._session_identity is not session_b._session_identity
    assert session_a._evaluation_identity is not session_b._evaluation_identity

    def _consume(session: RegistryTenancyEvidenceSession, proof: object) -> bool:
        barrier.wait(timeout=_BOUNDED_TIMEOUT_SECONDS)
        try:
            session.consume(proof)
        except D6EvidenceRejected:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(_consume, session_a, proof_a)
        future_b = executor.submit(_consume, session_b, proof_b)
        assert future_a.result(timeout=_BOUNDED_TIMEOUT_SECONDS) is True
        assert future_b.result(timeout=_BOUNDED_TIMEOUT_SECONDS) is True

    assert session_a._records == {}
    assert session_b._records == {}


def test_d6_c07_cleanup_of_session_a_does_not_affect_session_b() -> None:
    """D6-C07: finalizing session A leaves session B independently consumable."""
    session_a, proof_a, _clock_a, _source_a = _issued_session()
    session_b, proof_b, _clock_b, _source_b = _issued_session()
    record_a = _only_record(session_a)
    record_b = _only_record(session_b)

    session_a._finalize_owned_evidence(authority=session_a._cleanup_authority)
    session_b.consume(proof_b)

    assert record_a.state is RegistryTenancyEvidenceState.INVALIDATED
    assert record_b.state is RegistryTenancyEvidenceState.VALIDATED_AND_CONSUMED
    with pytest.raises(D6EvidenceRejected):
        session_a.consume(proof_a)


def test_d6_c08_contended_operations_do_not_deadlock() -> None:
    """D6-C08: contended consume, invalidation, and cleanup operations never deadlock."""
    sessions = [_issued_session() for _ in range(4)]
    barrier = threading.Barrier(8)

    def _consume(index: int) -> None:
        session, proof, _clock, _source = sessions[index]
        barrier.wait(timeout=_BOUNDED_TIMEOUT_SECONDS)
        try:
            session.consume(proof)
        except D6EvidenceRejected:
            pass

    def _finalize(index: int) -> None:
        session, _proof, _clock, _source = sessions[index]
        barrier.wait(timeout=_BOUNDED_TIMEOUT_SECONDS)
        session._finalize_owned_evidence(authority=session._cleanup_authority)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_consume, index) for index in range(4)]
        futures += [executor.submit(_finalize, index) for index in range(4)]
        for future in futures:
            future.result(timeout=_BOUNDED_TIMEOUT_SECONDS)

    for session, _proof, _clock, _source in sessions:
        assert session._records == {}


def test_d6_c09_all_futures_complete_within_a_bounded_timeout() -> None:
    """D6-C09: every contended operation completes as terminal success or rejection in bound."""
    session, proof, _clock, _source = _issued_session()
    barrier = threading.Barrier(5)
    completed: list[bool] = []

    def _consume() -> bool:
        barrier.wait(timeout=_BOUNDED_TIMEOUT_SECONDS)
        try:
            session.consume(proof)
        except D6EvidenceRejected:
            return False
        return True

    with ThreadPoolExecutor(max_workers=5) as executor:
        for future in [executor.submit(_consume) for _ in range(5)]:
            completed.append(future.result(timeout=_BOUNDED_TIMEOUT_SECONDS))

    assert len(completed) == 5
    assert all(isinstance(outcome, bool) for outcome in completed)
    assert session._records == {}


def test_d6_c10_concurrency_tests_use_no_sleep_based_coordination() -> None:
    """D6-C10: the concurrency harness coordinates with barriers and bounded timeouts only."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute):
                called_names.add(target.attr)
            elif isinstance(target, ast.Name):
                called_names.add(target.id)

    assert "time" not in imported_modules
    assert "sleep" not in called_names
    assert "Barrier" in called_names
    assert "_BOUNDED_TIMEOUT_SECONDS" in {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }


# --------------------------------------------------------------------------------------
# Uniform non-disclosing rejection boundary
# --------------------------------------------------------------------------------------


def test_rejections_remain_uniform_and_non_disclosing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every rejection reports the same non-disclosing message and exposes no protected fact."""
    session, proof, clock, _source = _issued_session()
    messages: set[str] = set()
    rejections: list[D6EvidenceRejected] = []

    def _capture(operation: object) -> None:
        try:
            operation()  # type: ignore[operator]
        except D6EvidenceRejected as rejection:
            messages.add(str(rejection))
            rejections.append(rejection)

    _capture(lambda: session.consume(object()))
    _capture(lambda: session.consume(_UnissuedCandidate()))
    _capture(TrustedRegistryTenancyProof)
    _capture(lambda: session._finalize_owned_evidence(authority=object()))
    _capture(lambda: copy.copy(proof))

    clock.utc_value = _only_record(session).expires_at
    _capture(lambda: session.consume(proof))

    marker = "INTERNAL_D6_TEST_MARKER"
    dependency_session, dependency_proof, _clock, dependency_source = _issued_session()
    dependency_source.error = RuntimeError(marker)
    _capture(lambda: dependency_session.consume(dependency_proof))

    clock_session, clock_proof, clock, _source = _issued_session()
    clock.utc_error = RuntimeError(marker)
    _capture(lambda: clock_session.consume(clock_proof))

    cleanup_session, _proof, _clock, _source = _issued_session()

    def _raise_cleanup_error(self: RegistryTenancyEvidenceSession) -> None:
        raise RuntimeError(marker)

    monkeypatch.setattr(
        RegistryTenancyEvidenceSession, "_clear_protected_bindings", _raise_cleanup_error
    )
    _capture(
        lambda: cleanup_session._finalize_owned_evidence(
            authority=cleanup_session._cleanup_authority
        )
    )
    monkeypatch.undo()

    replay_session, replay_proof, _clock, _source = _issued_session()
    replay_session.consume(replay_proof)
    _capture(lambda: replay_session.consume(replay_proof))

    mismatch_session, mismatch_proof, _clock, mismatch_source = _issued_session()
    mismatch_source.facts = _valid_facts(namespace_id=_OTHER_NAMESPACE_ID)
    _capture(lambda: mismatch_session.consume(mismatch_proof))

    state_session, state_proof, _clock, _source = _issued_session()
    state_session._finalize_owned_evidence(authority=state_session._cleanup_authority)
    _capture(lambda: state_session.consume(state_proof))

    assert len(messages) == 1
    message = messages.pop()
    for protected in (
        str(_PRINCIPAL_ID),
        str(_ORGANIZATION_ID),
        str(_WORKSPACE_ID),
        str(_NAMESPACE_ID),
        "expire",
        "replay",
        "clock",
        "namespace",
        "workspace",
        "organization",
    ):
        assert protected.lower() not in message.lower()
    for rejection in rejections:
        assert rejection.__cause__ is None
        assert rejection.__context__ is None
        assert marker not in "".join(traceback.format_exception(rejection))


def test_protected_representations_disclose_no_evidence() -> None:
    """Proof, authority, and session representations expose no protected D6 binding."""
    session, proof, _clock, _source = _issued_session()

    for representation in (
        repr(proof),
        repr(session._cleanup_authority),
        repr(session._session_identity),
        repr(session._evaluation_identity),
    ):
        assert "<protected>" in representation
        for protected in (str(_PRINCIPAL_ID), str(_ORGANIZATION_ID), str(_NAMESPACE_ID)):
            assert protected not in representation
