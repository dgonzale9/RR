"""Verify ERR-4.1-S1 pure execution request contracts."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from resource_registry.authorization.contracts import (
    AuthorizedNamespace,
    AuthorizedNamespaceSet,
    AuthorizedWorkspaceSet,
    RegistryAuthorizationContext,
)
from resource_registry.capability_definitions import (
    CapabilityContractRef,
    CapabilityExecutionMode,
    CapabilityKey,
    CapabilityVersion,
)
from resource_registry.capability_implementation_bindings import (
    CapabilityImplementationConformanceDeclaration,
    CapabilityImplementationConformanceDeclarationSet,
    CapabilityImplementationRef,
    ConformanceEvidenceRef,
)
from resource_registry.execution_contracts import (
    ExecutionAdmissionStatus,
    ExecutionAttemptFence,
    ExecutionCheckpoint,
    ExecutionContext,
    ExecutionCoordinatorTransitionStatus,
    ExecutionEngineCommand,
    ExecutionEngineOutcome,
    ExecutionEngineOutcomeKind,
    ExecutionInputReference,
    ExecutionInstanceRef,
    ExecutionLegacyRouteEvidence,
    ExecutionLifecycleCommand,
    ExecutionLifecycleSnapshot,
    ExecutionLifecycleState,
    ExecutionLineageReference,
    ExecutionOutputRegistrationRequest,
    ExecutionPlan,
    ExecutionPlanRef,
    ExecutionRequest,
    ExecutionRequestRef,
    ExecutionRequestValidationError,
    ExecutionResult,
    ExecutionResultStatus,
    ExecutionShadowPlanStatus,
    ExecutionStep,
    build_execution_shadow_plan,
    coordinate_execution_lifecycle_transition,
    evaluate_execution_admission,
    evaluate_execution_output_registration,
    evaluate_execution_worker_assignment,
    route_execution_request,
)
from resource_registry.registry_core import (
    NamespaceKey,
    OpaqueReference,
    OwnerDescriptor,
    OwnershipClassification,
    ResourceKey,
    ResourceTypeKey,
)

_CONTRACTS_PATH = Path(__file__).resolve().parents[1] / "execution_contracts" / "contracts.py"
_LIFECYCLE_PATH = Path(__file__).resolve().parents[1] / "execution_contracts" / "lifecycle.py"
_NAMESPACE = NamespaceKey(UUID("123e4567-e89b-12d3-a456-426614174000"))


def _build_request() -> ExecutionRequest:
    """Build one valid exact untrusted execution request.

    Returns:
        A valid immutable request contract.
    """
    return ExecutionRequest(
        ExecutionRequestRef("request-1"),
        CapabilityContractRef(
            CapabilityKey("atlas.document.content.read"), CapabilityVersion("1.0.0")
        ),
        (
            ExecutionInputReference(
                "source",
                ResourceKey(_NAMESPACE, ResourceTypeKey("atlas.document.content"), "document-1"),
                "primary-input",
            ),
        ),
        CapabilityExecutionMode.SYNCHRONOUS,
        datetime(2026, 7, 30, 12, tzinfo=UTC),
    )


def test_request_preserves_exact_immutable_intent_with_canonical_json() -> None:
    """Preserve untrusted exact request intent without routing behavior."""
    request = _build_request()

    assert request.to_canonical_json() == (
        '{"contract":{"key":"atlas.document.content.read","version":"1.0.0"},'
        '"inputs":[{"name":"source","role":"primary-input","target":'
        '{"kind":"resource","namespace":"123e4567-e89b-12d3-a456-426614174000",'
        '"resource_id":"document-1","resource_type":"atlas.document.content"}}],'
        '"request_ref":"request-1","requested_deadline_at":"2026-07-30T12:00:00+00:00",'
        '"requested_execution_mode":"synchronous"}'
    )
    with pytest.raises(FrozenInstanceError):
        request.request_ref = ExecutionRequestRef("request-2")  # type: ignore[misc]


def test_request_rejects_duplicate_input_names() -> None:
    """Reject ambiguous input declarations before admission."""
    input_reference = ExecutionInputReference(
        "source",
        ResourceKey(_NAMESPACE, ResourceTypeKey("atlas.document.content"), "document-1"),
    )

    with pytest.raises(
        ExecutionRequestValidationError,
        match=r"^duplicate execution input name$",
    ):
        ExecutionRequest(
            ExecutionRequestRef("request-1"),
            CapabilityContractRef(
                CapabilityKey("atlas.document.content.read"), CapabilityVersion("1.0.0")
            ),
            (input_reference, input_reference),
        )


def test_request_canonically_preserves_qualified_external_inputs() -> None:
    """Preserve qualified external references without resolving their owner data."""
    request = ExecutionRequest(
        ExecutionRequestRef("request-2"),
        CapabilityContractRef(
            CapabilityKey("atlas.document.content.read"), CapabilityVersion("1.0.0")
        ),
        (
            ExecutionInputReference(
                "external-source",
                OpaqueReference(
                    OwnerDescriptor("external-owner", OwnershipClassification.DOMAIN_BACKED),
                    "external-document-1",
                ),
            ),
        ),
    )

    assert '"kind":"opaque"' in request.to_canonical_json()
    assert '"bounded_context_identifier":"external-owner"' in request.to_canonical_json()


def test_s2_contracts_preserve_immutable_exact_evidence() -> None:
    """Preserve exact plan, context, result, and checkpoint values without runtime behavior."""
    request = _build_request()
    plan_ref = ExecutionPlanRef("plan-1", "checksum-1")
    step = ExecutionStep("step-1", request.contract_ref, ("source",))
    plan = ExecutionPlan(plan_ref, request.request_ref, (step,))
    context = ExecutionContext(
        request.request_ref, plan_ref, request.contract_ref, request.requested_deadline_at
    )
    output = ResourceKey(_NAMESPACE, ResourceTypeKey("atlas.document.content"), "document-2")

    assert plan.steps == (step,)
    assert context.plan_ref == plan_ref
    assert ExecutionResult(
        plan_ref, ExecutionResultStatus.SUCCESS, (output,)
    ).output_references == (output,)
    assert ExecutionCheckpoint(plan_ref, "step-1", (output,)).output_references == (output,)


def test_s2_contracts_reject_invalid_exact_evidence() -> None:
    """Reject duplicate steps and non-reference checkpoint outputs."""
    request = _build_request()
    plan_ref = ExecutionPlanRef("plan-1", "checksum-1")
    step = ExecutionStep("step-1", request.contract_ref)

    with pytest.raises(ExecutionRequestValidationError, match="duplicate execution plan step"):
        ExecutionPlan(plan_ref, request.request_ref, (step, step))
    with pytest.raises(
        ExecutionRequestValidationError, match="invalid execution checkpoint outputs"
    ):
        ExecutionCheckpoint(plan_ref, "step-1", ("not-a-reference",))  # type: ignore[arg-type]


def test_admission_derives_scope_and_rejects_unverifiable_inputs() -> None:
    """Admit only exact inputs in the server-derived namespace context."""
    context = RegistryAuthorizationContext(
        AuthorizedWorkspaceSet(organization_id=1, user_id=2, workspace_ids=frozenset({3})),
        AuthorizedNamespaceSet((AuthorizedNamespace(_NAMESPACE.value, 3),)),
    )
    admitted = evaluate_execution_admission(_build_request(), context)
    assert admitted.status is ExecutionAdmissionStatus.ADMITTED
    assert (
        admitted.idempotency_scope
        == evaluate_execution_admission(_build_request(), context).idempotency_scope
    )

    rejected = evaluate_execution_admission(
        ExecutionRequest(
            ExecutionRequestRef("request-external"),
            _build_request().contract_ref,
            (
                ExecutionInputReference(
                    "external",
                    OpaqueReference(
                        OwnerDescriptor("external-owner", OwnershipClassification.DOMAIN_BACKED),
                        "external-1",
                    ),
                ),
            ),
        ),
        context,
    )
    assert rejected.status is ExecutionAdmissionStatus.REJECTED
    assert rejected.idempotency_scope is None


def test_router_selects_exact_candidate_deterministically_and_fails_closed() -> None:
    """Select stable exact declarations without invoking a provider."""
    request = _build_request()
    declarations = CapabilityImplementationConformanceDeclarationSet(
        (
            CapabilityImplementationConformanceDeclaration(
                CapabilityImplementationRef("implementation-b"),
                request.contract_ref,
                ConformanceEvidenceRef("evidence-b"),
            ),
            CapabilityImplementationConformanceDeclaration(
                CapabilityImplementationRef("implementation-a"),
                request.contract_ref,
                ConformanceEvidenceRef("evidence-a"),
            ),
        )
    )
    decision = route_execution_request(request, declarations)
    assert decision.chosen.implementation_ref.value == "implementation-a"
    assert decision.rejected_implementation_references == ("implementation-b",)
    assert decision.checksum == route_execution_request(request, declarations).checksum
    with pytest.raises(
        ExecutionRequestValidationError, match="no exact execution routing candidate"
    ):
        route_execution_request(request, CapabilityImplementationConformanceDeclarationSet(()))


def test_coordinator_applies_fenced_lifecycle_transitions_without_runtime_effects() -> None:
    """Apply canonical lifecycle transitions through the pure Coordinator reducer."""
    context = RegistryAuthorizationContext(
        AuthorizedWorkspaceSet(organization_id=1, user_id=2, workspace_ids=frozenset({3})),
        AuthorizedNamespaceSet((AuthorizedNamespace(_NAMESPACE.value, 3),)),
    )
    snapshot = ExecutionLifecycleSnapshot(
        ExecutionInstanceRef("execution-1"), _NAMESPACE, ExecutionLifecycleState.CREATED
    )
    for command, fence in (
        (ExecutionLifecycleCommand.VALIDATE, None),
        (ExecutionLifecycleCommand.PLAN, None),
        (ExecutionLifecycleCommand.QUEUE, None),
        (ExecutionLifecycleCommand.CLAIM, ExecutionAttemptFence("fence-1")),
    ):
        transition = coordinate_execution_lifecycle_transition(
            snapshot, command, context, snapshot.revision, fence
        )
        assert transition.status is ExecutionCoordinatorTransitionStatus.APPLIED
        snapshot = transition.snapshot

    completed = coordinate_execution_lifecycle_transition(
        snapshot,
        ExecutionLifecycleCommand.COMPLETE,
        context,
        snapshot.revision,
        ExecutionAttemptFence("fence-1"),
    )
    assert completed.status is ExecutionCoordinatorTransitionStatus.APPLIED
    assert completed.previous_state is ExecutionLifecycleState.RUNNING
    assert completed.snapshot.state is ExecutionLifecycleState.COMPLETED
    assert completed.snapshot.active_fence is None


def test_coordinator_rejects_stale_fences_revisions_and_cross_tenant_context() -> None:
    """Reject stale concurrency evidence and unauthorized tenant scopes deterministically."""
    authorized_context = RegistryAuthorizationContext(
        AuthorizedWorkspaceSet(organization_id=1, user_id=2, workspace_ids=frozenset({3})),
        AuthorizedNamespaceSet((AuthorizedNamespace(_NAMESPACE.value, 3),)),
    )
    running = ExecutionLifecycleSnapshot(
        ExecutionInstanceRef("execution-1"),
        _NAMESPACE,
        ExecutionLifecycleState.RUNNING,
        revision=4,
        active_fence=ExecutionAttemptFence("fence-1"),
    )
    stale_fence = coordinate_execution_lifecycle_transition(
        running,
        ExecutionLifecycleCommand.COMPLETE,
        authorized_context,
        expected_revision=4,
        fence=ExecutionAttemptFence("fence-2"),
    )
    assert stale_fence.status is ExecutionCoordinatorTransitionStatus.REJECTED
    assert stale_fence.rejection_reason == "execution lifecycle fence does not match"

    stale_revision = coordinate_execution_lifecycle_transition(
        running,
        ExecutionLifecycleCommand.COMPLETE,
        authorized_context,
        expected_revision=3,
        fence=ExecutionAttemptFence("fence-1"),
    )
    assert stale_revision.status is ExecutionCoordinatorTransitionStatus.REJECTED
    assert stale_revision.rejection_reason == "execution lifecycle revision does not match"

    denied_context = RegistryAuthorizationContext(
        AuthorizedWorkspaceSet(organization_id=1, user_id=2, workspace_ids=frozenset({3})),
        AuthorizedNamespaceSet(()),
    )
    denied = coordinate_execution_lifecycle_transition(
        running,
        ExecutionLifecycleCommand.COMPLETE,
        denied_context,
        expected_revision=4,
        fence=ExecutionAttemptFence("fence-1"),
    )
    assert denied.status is ExecutionCoordinatorTransitionStatus.REJECTED
    assert denied.rejection_reason == "execution lifecycle namespace is not authorized"


def test_coordinator_preserves_idempotence_and_rejects_illegal_transitions() -> None:
    """Preserve repeat-safe commands while rejecting transitions outside the canonical graph."""
    context = RegistryAuthorizationContext(
        AuthorizedWorkspaceSet(organization_id=1, user_id=2, workspace_ids=frozenset({3})),
        AuthorizedNamespaceSet((AuthorizedNamespace(_NAMESPACE.value, 3),)),
    )
    completed = ExecutionLifecycleSnapshot(
        ExecutionInstanceRef("execution-1"), _NAMESPACE, ExecutionLifecycleState.COMPLETED
    )
    repeated = coordinate_execution_lifecycle_transition(
        completed, ExecutionLifecycleCommand.COMPLETE, context, expected_revision=0
    )
    assert repeated.status is ExecutionCoordinatorTransitionStatus.IDEMPOTENT
    assert repeated.snapshot == completed

    rejected = coordinate_execution_lifecycle_transition(
        completed, ExecutionLifecycleCommand.RETRY, context, expected_revision=0
    )
    assert rejected.status is ExecutionCoordinatorTransitionStatus.REJECTED
    assert rejected.rejection_reason == "invalid execution lifecycle transition"


def test_coordinator_models_cancellation_retry_and_compensation_paths() -> None:
    """Model approved cancellation, retry, and compensation paths without execution behavior."""
    context = RegistryAuthorizationContext(
        AuthorizedWorkspaceSet(organization_id=1, user_id=2, workspace_ids=frozenset({3})),
        AuthorizedNamespaceSet((AuthorizedNamespace(_NAMESPACE.value, 3),)),
    )
    running = ExecutionLifecycleSnapshot(
        ExecutionInstanceRef("execution-1"),
        _NAMESPACE,
        ExecutionLifecycleState.RUNNING,
        active_fence=ExecutionAttemptFence("fence-1"),
    )
    retry = coordinate_execution_lifecycle_transition(
        running,
        ExecutionLifecycleCommand.RETRY,
        context,
        expected_revision=0,
        fence=ExecutionAttemptFence("fence-1"),
    )
    assert retry.snapshot.state is ExecutionLifecycleState.RETRYING
    queued = coordinate_execution_lifecycle_transition(
        retry.snapshot, ExecutionLifecycleCommand.QUEUE, context, expected_revision=1
    )
    assert queued.snapshot.state is ExecutionLifecycleState.QUEUED

    cancellation = coordinate_execution_lifecycle_transition(
        running,
        ExecutionLifecycleCommand.CANCEL,
        context,
        expected_revision=0,
        fence=ExecutionAttemptFence("fence-1"),
    )
    confirmed = coordinate_execution_lifecycle_transition(
        cancellation.snapshot,
        ExecutionLifecycleCommand.CONFIRM_CANCELLATION,
        context,
        expected_revision=1,
    )
    compensated = coordinate_execution_lifecycle_transition(
        confirmed.snapshot,
        ExecutionLifecycleCommand.COMPENSATE,
        context,
        expected_revision=2,
    )
    assert compensated.snapshot.state is ExecutionLifecycleState.COMPENSATED
    repeated_cancel = coordinate_execution_lifecycle_transition(
        confirmed.snapshot,
        ExecutionLifecycleCommand.CANCEL,
        context,
        expected_revision=2,
    )
    assert repeated_cancel.status is ExecutionCoordinatorTransitionStatus.IDEMPOTENT


def test_engine_worker_contracts_require_exact_running_fenced_authorized_work() -> None:
    """Validate only exact authorized Worker-to-Engine assignments without execution behavior."""
    request = _build_request()
    plan_ref = ExecutionPlanRef("plan-1", "checksum-1")
    step = ExecutionStep("step-1", request.contract_ref, ("source",))
    plan = ExecutionPlan(plan_ref, request.request_ref, (step,))
    context = ExecutionContext(
        request.request_ref, plan_ref, request.contract_ref, datetime.now(UTC)
    )
    fence = ExecutionAttemptFence("fence-1")
    command = ExecutionEngineCommand(
        ExecutionLifecycleSnapshot(
            ExecutionInstanceRef("execution-1"),
            _NAMESPACE,
            ExecutionLifecycleState.RUNNING,
            active_fence=fence,
        ),
        plan,
        context,
        step,
        fence,
    )
    authorized_context = RegistryAuthorizationContext(
        AuthorizedWorkspaceSet(organization_id=1, user_id=2, workspace_ids=frozenset({3})),
        AuthorizedNamespaceSet((AuthorizedNamespace(_NAMESPACE.value, 3),)),
    )
    assignment = evaluate_execution_worker_assignment(command, authorized_context)
    assert assignment.status.value == "assigned"
    outcome = ExecutionEngineOutcome(
        command,
        ExecutionEngineOutcomeKind.SUCCEEDED,
        ExecutionResult(plan_ref, ExecutionResultStatus.SUCCESS),
    )
    assert outcome.result is not None

    denied = evaluate_execution_worker_assignment(
        command,
        RegistryAuthorizationContext(
            AuthorizedWorkspaceSet(organization_id=1, user_id=2, workspace_ids=frozenset({3})),
            AuthorizedNamespaceSet(()),
        ),
    )
    assert denied.status.value == "rejected"
    with pytest.raises(
        ExecutionRequestValidationError, match="execution engine fence does not match"
    ):
        ExecutionEngineCommand(
            command.snapshot, plan, context, step, ExecutionAttemptFence("stale")
        )


def test_output_registration_requires_completed_authorized_same_namespace_evidence() -> None:
    """Admit only owner-bound exact outputs and lineage without owner mutation."""
    request = _build_request()
    plan_ref = ExecutionPlanRef("plan-1", "checksum-1")
    output = ResourceKey(_NAMESPACE, ResourceTypeKey("atlas.document.content"), "document-2")
    registration = ExecutionOutputRegistrationRequest(
        ExecutionInstanceRef("execution-1"),
        ExecutionLifecycleSnapshot(
            ExecutionInstanceRef("execution-1"), _NAMESPACE, ExecutionLifecycleState.COMPLETED
        ),
        ExecutionResult(plan_ref, ExecutionResultStatus.SUCCESS, (output,)),
        OwnerDescriptor("dataset", OwnershipClassification.DOMAIN_BACKED),
        (output,),
        (ExecutionLineageReference(request.inputs[0].target, output),),
    )
    authorized_context = RegistryAuthorizationContext(
        AuthorizedWorkspaceSet(organization_id=1, user_id=2, workspace_ids=frozenset({3})),
        AuthorizedNamespaceSet((AuthorizedNamespace(_NAMESPACE.value, 3),)),
    )
    admitted = evaluate_execution_output_registration(registration, authorized_context)
    assert admitted.status.value == "admitted"
    assert (
        admitted.idempotency_key
        == evaluate_execution_output_registration(registration, authorized_context).idempotency_key
    )

    denied = evaluate_execution_output_registration(
        registration,
        RegistryAuthorizationContext(
            AuthorizedWorkspaceSet(organization_id=1, user_id=2, workspace_ids=frozenset({3})),
            AuthorizedNamespaceSet(()),
        ),
    )
    assert denied.status.value == "rejected"


def test_shadow_plan_is_deterministic_observational_and_non_authoritative() -> None:
    """Produce only a tenant-scoped route proposal and legacy comparison evidence."""
    request = _build_request()
    context = RegistryAuthorizationContext(
        AuthorizedWorkspaceSet(organization_id=1, user_id=2, workspace_ids=frozenset({3})),
        AuthorizedNamespaceSet((AuthorizedNamespace(_NAMESPACE.value, 3),)),
    )
    declarations = CapabilityImplementationConformanceDeclarationSet(
        (
            CapabilityImplementationConformanceDeclaration(
                CapabilityImplementationRef("implementation-a"),
                request.contract_ref,
                ConformanceEvidenceRef("evidence-a"),
            ),
        )
    )
    proposed = build_execution_shadow_plan(
        request,
        context,
        declarations,
        ExecutionLegacyRouteEvidence("legacy-route", "different-checksum"),
    )
    assert proposed.status is ExecutionShadowPlanStatus.PROPOSED
    assert proposed.differences == ("route_checksum_differs",)
    denied = build_execution_shadow_plan(
        request,
        RegistryAuthorizationContext(
            AuthorizedWorkspaceSet(organization_id=1, user_id=2, workspace_ids=frozenset({3})),
            AuthorizedNamespaceSet(()),
        ),
        declarations,
    )
    assert denied.status is ExecutionShadowPlanStatus.REJECTED


@pytest.mark.parametrize(
    ("factory", "message"),
    (
        (lambda: ExecutionRequestRef(""), "invalid execution request reference"),
        (
            lambda: ExecutionInputReference("source", "document-1"),  # type: ignore[arg-type]
            "invalid execution input target",
        ),
        (
            lambda: ExecutionRequest(
                ExecutionRequestRef("request-1"),
                CapabilityContractRef(
                    CapabilityKey("atlas.document.content.read"), CapabilityVersion("1.0.0")
                ),
                requested_deadline_at=datetime(2026, 7, 30, 12),
            ),
            "invalid requested execution deadline",
        ),
    ),
)
def test_request_contracts_reject_malformed_values(factory: object, message: str) -> None:
    """Reject malformed values without interpreting caller intent.

    Args:
        factory: Callable fixture producing one malformed contract value.
        message: Exact expected validation message.
    """
    with pytest.raises(ExecutionRequestValidationError, match=f"^{message}$"):
        factory()  # type: ignore[operator]


def test_request_contracts_have_no_runtime_or_authorization_dependencies() -> None:
    """Keep request contracts independent from runtime and authorization boundaries."""
    syntax_tree = ast.parse(_CONTRACTS_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        (node.module or "").split(".", maxsplit=1)[0]
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }

    assert imported_modules.isdisjoint(
        {
            "django",
            "BotPlatform",
            "BotServer",
            "MCPServer",
            "authorization",
            "providers",
            "execution",
            "workers",
            "routing",
            "settings",
        }
    )


def test_lifecycle_contracts_have_no_provider_worker_or_framework_dependencies() -> None:
    """Keep Coordinator lifecycle decisions independent from providers and runtime infrastructure."""
    syntax_tree = ast.parse(_LIFECYCLE_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        (node.module or "").split(".", maxsplit=1)[0]
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }

    assert imported_modules.isdisjoint(
        {
            "django",
            "BotPlatform",
            "BotServer",
            "MCPServer",
            "providers",
            "workers",
            "celery",
            "requests",
        }
    )
