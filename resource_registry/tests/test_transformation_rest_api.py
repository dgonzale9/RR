from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from django.contrib.auth.models import Group, Permission, User
from django.urls import Resolver404, resolve
from guardian.shortcuts import remove_perm
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIClient

from BotServer.util.user_group_management import UserGroupManagement
from BotServer.workspace_scope import invalidate_organization_host_cache
from Organization.models import Organization, OrganizationHost, Workspace
from resource_registry.api.transformation.dependencies import TransformationRestApplicationServices
from resource_registry.api.transformation.permissions import resolve_tenant_id
from resource_registry.application.contracts import (
    AuthorizationError,
    ExecutionState,
    HealthState,
    OutputKind,
    OutputStatus,
    RunStatus,
    RuntimeUnavailableError,
    StepStatus,
    TransformationDiagnosticSummary,
    TransformationExecutionAccepted,
    TransformationExecutionRequest,
    TransformationOutputReference,
    TransformationRunReference,
    TransformationStepSummary,
)
from resource_registry.application.read import (
    VIEW_TRANSFORMATION_DIAGNOSTICS_PERMISSION,
    VIEW_TRANSFORMATION_RUN_PERMISSION,
    TransformationDiagnosticsReadService,
    TransformationOutputReadService,
    TransformationReadContext,
    TransformationRunFilters,
    TransformationRunReadService,
    TransformationStepReadService,
)
from resource_registry.application.services import (
    EXECUTE_TRANSFORMATION_PERMISSION,
    USE_TRANSFORMATION_PROVIDER_PERMISSION,
    TransformationExecutionApplicationService,
    TransformationExecutionCallerContext,
)
from resource_registry.services.registry_namespace_provisioning_service import (
    RegistryNamespaceProvisioningService,
)
from resource_registry.tests.workspace_permission_helpers import add_workspace_user

pytestmark = pytest.mark.django_db(transaction=True)

_API_ROOT = Path("resource_registry/api/transformation")
_FORBIDDEN_IMPORT_PREFIXES = (
    "resource_registry.models",
    "resource_registry.storage",
    "resource_registry.services.transformation_run_worker",
    "resource_registry.services.transformation_output_finalization_worker",
    "resource_registry.transformations.execution",
    "resource_registry.transformations.providers",
    "resource_registry.transformations.output_finalization",
    "resource_registry.services.registry.lineage_service",
    "BotServer.built_in_functions",
)


class _ExecutionService:
    def __init__(self) -> None:
        self.calls: list[
            tuple[TransformationExecutionRequest, TransformationExecutionCallerContext]
        ] = []
        self.error: Exception | None = None

    def execute(
        self,
        *,
        request: TransformationExecutionRequest,
        caller: TransformationExecutionCallerContext,
    ) -> TransformationExecutionAccepted:
        self.calls.append((request, caller))
        if self.error is not None:
            raise self.error
        return TransformationExecutionAccepted(
            run=TransformationRunReference(
                tenant_id=request.tenant_id,
                run_id=str(uuid.UUID(int=7)),
                definition_id=request.definition_id,
                definition_version_id=request.definition_version_id,
                status=RunStatus.QUEUED,
            ),
            execution_state=ExecutionState.ACCEPTED,
            idempotent_replay=False,
            correlation_id=request.correlation_id,
        )


class _RunService:
    def __init__(self, run: TransformationRunReference) -> None:
        self.run = run
        self.list_calls: list[tuple[object, TransformationRunFilters, int, int]] = []

    def get_run(self, *, context: object, run_id: str) -> TransformationRunReference:
        _ = (context, run_id)
        return self.run

    def list_runs(
        self,
        *,
        context: object,
        filters: TransformationRunFilters,
        limit: int,
        offset: int,
    ) -> tuple[TransformationRunReference, ...]:
        self.list_calls.append((context, filters, limit, offset))
        return (self.run,)


class _StepService:
    def list_steps(self, *, context: object, run_id: str) -> tuple[TransformationStepSummary, ...]:
        _ = (context, run_id)
        return (
            TransformationStepSummary(
                step_id="project",
                attempt_number=1,
                status=StepStatus.SUCCEEDED,
                duration_seconds=0.5,
            ),
        )


class _OutputService:
    def list_outputs(
        self,
        *,
        context: object,
        run_id: str,
    ) -> tuple[TransformationOutputReference, ...]:
        _ = (context, run_id)
        return (
            TransformationOutputReference(
                output_id=str(uuid.UUID(int=8)),
                logical_name="result",
                kind=OutputKind.STANDALONE,
                status=OutputStatus.REGISTERED,
                artifact_id=str(uuid.UUID(int=9)),
            ),
        )


class _DiagnosticsService:
    def get_run_diagnostics(
        self,
        *,
        context: object,
        run_id: str,
    ) -> TransformationDiagnosticSummary:
        _ = (context, run_id)
        return TransformationDiagnosticSummary(
            health_state=HealthState.READY,
            aggregate_counts={"retry_count": 0},
            warnings=(),
            durations={"execution": 0.5},
            duration_seconds=0.5,
            progress=1.0,
        )


@pytest.fixture
def service_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, _ExecutionService, _RunService]:
    tenant_id = str(uuid.UUID(int=1))
    execution = _ExecutionService()
    runs = _RunService(
        TransformationRunReference(
            tenant_id=tenant_id,
            run_id=str(uuid.UUID(int=7)),
            definition_id=str(uuid.UUID(int=2)),
            definition_version_id=str(uuid.UUID(int=3)),
            status=RunStatus.QUEUED,
        ),
    )
    services = TransformationRestApplicationServices(
        execution=cast(TransformationExecutionApplicationService, execution),
        runs=cast(TransformationRunReadService, runs),
        steps=cast(TransformationStepReadService, _StepService()),
        outputs=cast(TransformationOutputReadService, _OutputService()),
        diagnostics=cast(TransformationDiagnosticsReadService, _DiagnosticsService()),
    )
    monkeypatch.setattr(
        "resource_registry.api.transformation.views.get_transformation_rest_application_services",
        lambda **_kwargs: services,
    )
    return services, execution, runs


@pytest.fixture
def authenticated_client() -> APIClient:
    user = User.objects.create_user(username="transformation-rest-user")
    _assign_transformation_permissions(user=user)
    organization = Organization.objects.create(
        name="Transformation REST Organization",
        slug="transformation-rest-organization",
        last_modified_by=user,
    )
    # Organization creation grants the creator an object-level view permission. Remove that
    # implicit grant so this fixture exposes exactly one authorized Workspace namespace.
    remove_perm(Organization.VIEW_PERMISSION_CODENAME, user, organization)
    workspace = Workspace.objects.create(
        organization=organization,
        name="Transformation REST Workspace",
        slug="transformation-rest-workspace",
        last_modified_by=user,
    )
    add_workspace_user(workspace=workspace, user=user)
    RegistryNamespaceProvisioningService().provision_verified_existing_namespace(
        workspace_id=workspace.pk,
        namespace_id=uuid.UUID(int=1),
    )
    organization.resource_registry_enabled = True
    organization.save(update_fields=["resource_registry_enabled"])
    workspace.resource_registry_enabled = True
    workspace.save(update_fields=["resource_registry_enabled"])
    invalidate_organization_host_cache("testserver")
    OrganizationHost.objects.create(organization=organization, fqdn="testserver")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _assign_transformation_permissions(*, user: User) -> None:
    permission_names = {
        EXECUTE_TRANSFORMATION_PERMISSION,
        USE_TRANSFORMATION_PROVIDER_PERMISSION,
        VIEW_TRANSFORMATION_RUN_PERMISSION,
        VIEW_TRANSFORMATION_DIAGNOSTICS_PERMISSION,
    }
    codenames = {permission_name.rsplit(".", maxsplit=1)[1] for permission_name in permission_names}
    permissions = Permission.objects.filter(
        content_type__app_label="resource_registry",
        codename__in=codenames,
    )
    user.user_permissions.add(*permissions)


def _execution_payload() -> dict[str, object]:
    return {
        "tenant_id": str(uuid.UUID(int=1)),
        "definition_id": str(uuid.UUID(int=2)),
        "definition_version_id": str(uuid.UUID(int=3)),
        "parameters": {"threshold": 0.5},
        "input_bindings": [
            {
                "input_name": "source",
                "dataset_id": str(uuid.UUID(int=4)),
                "dataset_version_id": str(uuid.UUID(int=5)),
                "artifact_id": str(uuid.UUID(int=6)),
            },
        ],
        "caller_idempotency_key": "caller-key-1",
        "correlation_id": "correlation-1",
    }


def _create_workspace_namespace(
    *,
    organization: Organization,
    user: User,
    namespace_id: uuid.UUID,
    slug: str,
    is_active: bool = True,
    assign_user: bool = True,
) -> Workspace:
    workspace = Workspace.objects.create(
        organization=organization,
        name=slug.replace("-", " ").title(),
        slug=slug,
        is_active=is_active,
        last_modified_by=user,
    )
    if assign_user:
        add_workspace_user(workspace=workspace, user=user)
    RegistryNamespaceProvisioningService().provision_verified_existing_namespace(
        workspace_id=workspace.pk,
        namespace_id=namespace_id,
    )
    workspace.resource_registry_enabled = True
    workspace.save(update_fields=["resource_registry_enabled"])
    return workspace


def test_execute_endpoint_delegates_and_returns_async_acceptance(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _services, execution, _runs = service_bundle

    response = authenticated_client.post(
        "/api/v1/transformations/runs/",
        _execution_payload(),
        format="json",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.data["execution_state"] == "ACCEPTED"
    assert response.data["run"]["status"] == "QUEUED"
    assert response.data["correlation_id"] == "correlation-1"
    assert len(execution.calls) == 1
    application_request, caller = execution.calls[0]
    assert application_request.caller_idempotency_key == "caller-key-1"
    assert caller.tenant_id == str(uuid.UUID(int=1))
    assert {
        EXECUTE_TRANSFORMATION_PERMISSION,
        USE_TRANSFORMATION_PROVIDER_PERMISSION,
    }.issubset(caller.permissions)


def test_execute_rejects_malformed_transport_before_delegation(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _services, execution, _runs = service_bundle
    payload = _execution_payload()
    payload["definition_id"] = "invalid"

    response = authenticated_client.post(
        "/api/v1/transformations/runs/",
        payload,
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["code"] == "invalid_transformation_rest_request"
    assert response.data["category"] == "VALIDATION"
    assert execution.calls == []


def test_application_error_maps_to_stable_http_error(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _services, execution, _runs = service_bundle
    execution.error = RuntimeUnavailableError(
        code="transformation_runtime_unavailable",
        public_message="Transformation runtime unavailable.",
        retryable=True,
        correlation_id="correlation-1",
    )

    response = authenticated_client.post(
        "/api/v1/transformations/runs/",
        _execution_payload(),
        format="json",
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.data == {
        "contract_version": "transformation-application/1.0",
        "code": "transformation_runtime_unavailable",
        "category": "RUNTIME_UNAVAILABLE",
        "message": "Transformation runtime unavailable.",
        "retryable": True,
        "correlation_id": "correlation-1",
        "metadata": {},
    }


def test_list_endpoint_passes_only_story_two_filters_and_pagination(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _services, _execution, runs = service_bundle
    tenant_id = str(uuid.UUID(int=1))
    definition_id = str(uuid.UUID(int=2))

    response = authenticated_client.get(
        "/api/v1/transformations/runs/",
        cast(
            dict[str, str],
            {
                "status": "QUEUED",
                "definition_id": definition_id,
                "definition_version_id": str(uuid.UUID(int=3)),
                "created_after": "2026-01-01T00:00:00Z",
                "created_before": "2026-02-01T00:00:00Z",
                "correlation_id": "filter-correlation",
                "limit": "25",
                "offset": "10",
            },
        ),
        HTTP_X_TENANT_ID=tenant_id,
        HTTP_X_CORRELATION_ID="read-correlation",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data[0]["run_id"] == str(uuid.UUID(int=7))
    context, filters, limit, offset = runs.list_calls[0]
    assert (
        VIEW_TRANSFORMATION_RUN_PERMISSION in cast(TransformationReadContext, context).permissions
    )
    assert filters.status == RunStatus.QUEUED
    assert filters.definition_id == definition_id
    assert filters.correlation_id == "filter-correlation"
    assert (limit, offset) == (25, 10)


def test_single_authorized_namespace_is_selected_when_hint_is_omitted(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _services, _execution, runs = service_bundle
    response = authenticated_client.get("/api/v1/transformations/runs/")

    assert response.status_code == status.HTTP_200_OK
    context, _filters, _limit, _offset = runs.list_calls[0]
    assert cast(TransformationReadContext, context).tenant_id == str(uuid.UUID(int=1))


def test_single_authorized_namespace_is_selected_for_submission_without_body_hint(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _services, execution, _runs = service_bundle
    payload = _execution_payload()
    del payload["tenant_id"]

    response = authenticated_client.post(
        "/api/v1/transformations/runs/",
        payload,
        format="json",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    application_request, caller = execution.calls[0]
    assert application_request.tenant_id == str(uuid.UUID(int=1))
    assert caller.tenant_id == str(uuid.UUID(int=1))


def test_unauthorized_or_unmapped_namespace_is_rejected(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _ = service_bundle
    response = authenticated_client.get(
        "/api/v1/transformations/runs/",
        HTTP_X_TENANT_ID=str(uuid.UUID(int=99)),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["code"] == "transformation_tenant_not_authorized"


def test_malformed_namespace_hint_is_rejected(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _ = service_bundle
    response = authenticated_client.get(
        "/api/v1/transformations/runs/",
        HTTP_X_TENANT_ID="not-a-uuid",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["code"] == "invalid_transformation_tenant"


def test_namespace_outside_host_organization_is_rejected(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _ = service_bundle
    user = User.objects.get(username="transformation-rest-user")
    other_organization = Organization.objects.create(
        name="Other Transformation REST Organization",
        slug="other-transformation-rest-organization",
        last_modified_by=user,
    )
    outside_namespace_id = uuid.UUID(int=2)
    _create_workspace_namespace(
        organization=other_organization,
        user=user,
        namespace_id=outside_namespace_id,
        slug="outside-organization-workspace",
    )

    response = authenticated_client.get(
        "/api/v1/transformations/runs/",
        HTTP_X_TENANT_ID=str(outside_namespace_id),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["code"] == "transformation_tenant_not_authorized"


def test_namespace_for_inactive_workspace_is_rejected(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _ = service_bundle
    user = User.objects.get(username="transformation-rest-user")
    organization = Organization.objects.get(slug="transformation-rest-organization")
    inactive_namespace_id = uuid.UUID(int=2)
    _create_workspace_namespace(
        organization=organization,
        user=user,
        namespace_id=inactive_namespace_id,
        slug="inactive-transformation-workspace",
        is_active=False,
    )

    response = authenticated_client.get(
        "/api/v1/transformations/runs/",
        HTTP_X_TENANT_ID=str(inactive_namespace_id),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["code"] == "transformation_tenant_not_authorized"


def test_multiple_authorized_namespaces_require_explicit_selection(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _ = service_bundle
    user = User.objects.get(username="transformation-rest-user")
    organization = Organization.objects.get(slug="transformation-rest-organization")
    _create_workspace_namespace(
        organization=organization,
        user=user,
        namespace_id=uuid.UUID(int=2),
        slug="second-transformation-workspace",
    )

    response = authenticated_client.get("/api/v1/transformations/runs/")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["code"] == "transformation_tenant_selection_required"


def test_multiple_authorized_namespaces_reject_submission_without_selection(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _services, execution, _runs = service_bundle
    user = User.objects.get(username="transformation-rest-user")
    organization = Organization.objects.get(slug="transformation-rest-organization")
    _create_workspace_namespace(
        organization=organization,
        user=user,
        namespace_id=uuid.UUID(int=2),
        slug="second-submission-workspace",
    )
    payload = _execution_payload()
    del payload["tenant_id"]

    response = authenticated_client.post(
        "/api/v1/transformations/runs/",
        payload,
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["code"] == "transformation_tenant_selection_required"
    assert execution.calls == []


def test_user_without_authorized_namespace_fails_closed(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _ = (authenticated_client, service_bundle)
    user = User.objects.create_user(username="transformation-no-workspace-user")
    _assign_transformation_permissions(user=user)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/v1/transformations/runs/")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["code"] == "transformation_tenant_not_authorized"


def test_unauthorized_submission_body_namespace_cannot_grant_access(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _services, execution, _runs = service_bundle
    payload = _execution_payload()
    payload["tenant_id"] = str(uuid.UUID(int=99))

    response = authenticated_client.post(
        "/api/v1/transformations/runs/",
        payload,
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["code"] == "transformation_tenant_not_authorized"
    assert execution.calls == []


def test_conflicting_header_and_submission_body_fail_deterministically(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _services, execution, _runs = service_bundle
    payload = _execution_payload()
    payload["tenant_id"] = str(uuid.UUID(int=99))

    response = authenticated_client.post(
        "/api/v1/transformations/runs/",
        payload,
        format="json",
        HTTP_X_TENANT_ID=str(uuid.UUID(int=1)),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["code"] == "transformation_tenant_not_authorized"
    assert execution.calls == []


@pytest.mark.parametrize("session_key", ("resource_registry_tenant_id", "tenant_id"))
def test_legacy_top_level_session_hint_is_ignored_when_namespace_is_authorized(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
    session_key: str,
) -> None:
    _ = service_bundle
    session = authenticated_client.session
    session[session_key] = str(uuid.UUID(int=1))
    session.save()

    response = authenticated_client.get("/api/v1/transformations/runs/")

    assert response.status_code == status.HTTP_200_OK


def test_stale_top_level_session_namespace_is_ignored(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    _ = service_bundle
    session = authenticated_client.session
    session["resource_registry_tenant_id"] = str(uuid.UUID(int=99))
    session.save()

    response = authenticated_client.get("/api/v1/transformations/runs/")

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.parametrize(
    "group_name",
    (UserGroupManagement.GROUP_ADMIN,),
)
def test_admin_role_may_select_organization_namespace_without_direct_membership(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
    group_name: str,
) -> None:
    _ = (authenticated_client, service_bundle)
    user = User.objects.create_user(username=f"transformation-{group_name}")
    _assign_transformation_permissions(user=user)
    group, _created = Group.objects.get_or_create(name=group_name)
    user.groups.add(group)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get(
        "/api/v1/transformations/runs/",
        HTTP_X_TENANT_ID=str(uuid.UUID(int=1)),
    )

    assert response.status_code == status.HTTP_200_OK


def test_scoped_admin_may_select_workspace_namespace_without_direct_membership(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
) -> None:
    """Authorize a scoped admin for the selected active workspace namespace."""
    _ = (authenticated_client, service_bundle)
    user = User.objects.create_user(username="transformation-scoped-admin")
    _assign_transformation_permissions(user=user)
    group, _created = Group.objects.get_or_create(
        name=UserGroupManagement.GROUP_SCOPED_ADMIN,
    )
    user.groups.add(group)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get(
        "/api/v1/transformations/runs/",
        HTTP_X_TENANT_ID=str(uuid.UUID(int=1)),
    )

    assert response.status_code == status.HTTP_200_OK


def test_request_attribute_is_only_an_authorized_namespace_selection_hint(
    authenticated_client: APIClient,
) -> None:
    _ = authenticated_client
    user = User.objects.get(username="transformation-rest-user")
    organization = Organization.objects.get(slug="transformation-rest-organization")
    request = cast(
        Request,
        SimpleNamespace(
            user=user,
            organization=organization,
            tenant_id=str(uuid.UUID(int=1)),
            session={},
            headers={},
        ),
    )

    assert resolve_tenant_id(request=request) == str(uuid.UUID(int=1))


def test_forged_request_attribute_cannot_grant_namespace_access(
    authenticated_client: APIClient,
) -> None:
    _ = authenticated_client
    user = User.objects.get(username="transformation-rest-user")
    organization = Organization.objects.get(slug="transformation-rest-organization")
    request = cast(
        Request,
        SimpleNamespace(
            user=user,
            organization=organization,
            tenant_id=str(uuid.UUID(int=99)),
            session={},
            headers={},
        ),
    )

    with pytest.raises(AuthorizationError) as captured:
        resolve_tenant_id(request=request)
    assert captured.value.code == "transformation_tenant_not_authorized"


@pytest.mark.parametrize(
    ("suffix", "expected_key"),
    (
        ("", "run_id"),
        ("/steps/", "step_id"),
        ("/outputs/", "output_id"),
        ("/diagnostics/", "health_state"),
    ),
)
def test_read_endpoints_delegate_and_serialize_story_one_dtos(
    authenticated_client: APIClient,
    service_bundle: tuple[object, _ExecutionService, _RunService],
    suffix: str,
    expected_key: str,
) -> None:
    _ = service_bundle
    run_id = str(uuid.UUID(int=7))
    response = authenticated_client.get(
        f"/api/v1/transformations/runs/{run_id}{suffix}",
        HTTP_X_TENANT_ID=str(uuid.UUID(int=1)),
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.data[0] if isinstance(response.data, list) else response.data
    assert expected_key in body


def test_authentication_failure_uses_stable_error_schema() -> None:
    response = APIClient().get(
        "/api/v1/transformations/runs/",
        HTTP_X_TENANT_ID=str(uuid.UUID(int=1)),
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.data["code"] == "transformation_authentication_required"
    assert response.data["category"] == "AUTHORIZATION"


def test_openapi_31_schema_is_complete_stable_and_sanitized() -> None:
    response = APIClient().get(
        "/api/schema/?format=json",
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == status.HTTP_200_OK
    schema = json.loads(response.content)
    assert schema["openapi"] == "3.1.0"
    expected_paths = {
        "/api/v1/transformations/runs/",
        "/api/v1/transformations/runs/{run_id}",
        "/api/v1/transformations/runs/{run_id}/steps/",
        "/api/v1/transformations/runs/{run_id}/outputs/",
        "/api/v1/transformations/runs/{run_id}/diagnostics/",
    }
    assert expected_paths.issubset(schema["paths"])
    operations = [
        operation
        for path_name, path in schema["paths"].items()
        if path_name in expected_paths
        for operation in path.values()
    ]
    operation_ids = [operation["operationId"] for operation in operations]
    assert len(operation_ids) == len(set(operation_ids))
    assert set(schema["components"]["securitySchemes"]) == {"cookieAuth", "tokenAuth"}
    list_parameters = schema["paths"]["/api/v1/transformations/runs/"]["get"]["parameters"]
    assert {
        "status",
        "definition_id",
        "definition_version_id",
        "created_after",
        "created_before",
        "correlation_id",
        "limit",
        "offset",
        "X-Tenant-ID",
        "X-Correlation-ID",
    }.issubset({parameter["name"] for parameter in list_parameters})
    tenant_parameter = next(
        parameter for parameter in list_parameters if parameter["name"] == "X-Tenant-ID"
    )
    assert tenant_parameter.get("required", False) is False
    post_operation = schema["paths"]["/api/v1/transformations/runs/"]["post"]
    post_tenant_parameter = next(
        parameter
        for parameter in post_operation["parameters"]
        if parameter["name"] == "X-Tenant-ID"
    )
    assert post_tenant_parameter.get("required", False) is False
    request_schema_reference = post_operation["requestBody"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    request_schema_name = request_schema_reference.rsplit("/", maxsplit=1)[1]
    request_schema = schema["components"]["schemas"][request_schema_name]
    assert "tenant_id" not in request_schema["required"]
    post_responses = post_operation["responses"]
    assert {"202", "400", "401", "403", "409", "500", "503"}.issubset(post_responses)
    serialized_schema = json.dumps(schema)
    for forbidden in (
        "execution_plan",
        "checkpoint",
        "workspace_lease",
        "signed_url",
        "storage_path",
        "fencing_token",
        "provider_response",
    ):
        assert forbidden not in serialized_schema.lower()


def test_scalar_uses_canonical_schema_and_other_documentation_uis_are_absent() -> None:
    user = User.objects.create_user(username="scalar-docs-user", password="test-password")
    organization = Organization.objects.create(
        name="Scalar Documentation", slug="scalar-documentation", last_modified_by=user,
    )
    invalidate_organization_host_cache("testserver")
    OrganizationHost.objects.create(organization=organization, fqdn="testserver")
    client = APIClient()
    client.force_login(user)
    response = client.get("/uai/botserver/api-documentation")

    assert response.status_code == status.HTTP_200_OK
    html = response.content.decode("utf-8")
    assert "Scalar.createApiReference" in html
    assert '"url": "/api/schema/?format=json"' in html
    for unsupported_path in (
        "/api/v1/schema/",
        "/api/v1/docs/",
        "/api/schema/redoc/",
        "/api/v1/redoc/",
        "/api/v1/swagger/",
    ):
        with pytest.raises(Resolver404):
            resolve(unsupported_path)


def test_rest_package_has_no_forbidden_runtime_dependencies() -> None:
    for source_path in _API_ROOT.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = _imported_modules(tree)
        assert not any(
            module.startswith(prefix)
            for module in imported_modules
            for prefix in _FORBIDDEN_IMPORT_PREFIXES
        ), source_path


def test_rest_namespace_admission_has_no_persona_fallback() -> None:
    source = (_API_ROOT / "permissions.py").read_text(encoding="utf-8")
    assert "Persona" not in source
    assert "metadata" not in source


def _imported_modules(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported
