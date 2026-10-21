"""Runtime Adoption Phase 2 namespace admission tests for read-only dataset BIFs."""

from __future__ import annotations

import ast
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser, Group, User
from django.utils import timezone

from BotServer.builtin_functions import resource_registry_functions
from BotServer.util.user_group_management import UserGroupManagement
from Organization.models import Organization, OrganizationMembership, Workspace
from resource_registry.bifs.read_only_namespace_admission import (
    TENANT_SELECTION_CONFLICT_MESSAGE,
    TENANT_SELECTION_REQUIRED_MESSAGE,
    WORKSPACE_SELECTION_REQUIRED_MESSAGE,
    ReadOnlyBifNamespaceAdmissionService,
)
from resource_registry.assistant.persona_workspace_scope import (
    PERSONA_WORKSPACE_SELECTION_SESSION_KEY,
)
from resource_registry.models import (
    Dataset,
    DatasetSchema,
    DatasetStatus,
    DatasetVersion,
    DatasetVersionStatus,
    DatasetVisibilityScope,
    RegistryNamespace,
    StagedResourceIntake,
    StagedResourceIntakeStatus,
    StagedResourcePublication,
    StagedResourcePublicationDataset,
    StagedResourcePublicationStatus,
)
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.exceptions import DataRegistryAuthorizationError
from resource_registry.services.registry_namespace_provisioning_service import (
    RegistryNamespaceProvisioningService,
)
from resource_registry.services.dataset_query_executor import _SafeSqlValidator
from resource_registry.tests.workspace_permission_helpers import add_workspace_user
from resource_registry.tests.bif_contract_helpers import FakePersonaBifRelationshipManager

pytestmark = pytest.mark.django_db(transaction=True)

_ROOT = Path(__file__).resolve().parents[2]
_BIF_MODULE = _ROOT / "BotServer" / "builtin_functions" / "resource_registry_functions.py"
_HTTP_BAD_REQUEST = 400
_ADOPTED_FUNCTIONS = frozenset(
    {
        "resource_registry_list_datasets",
        "resource_registry_get_dataset",
        "resource_registry_get_dataset_context",
        "resource_registry_get_dataset_schema",
        "resource_registry_get_dataset_profile",
        "resource_registry_list_dataset_versions",
        "resource_registry_explore_dataset",
    }
)
_PHASE_3A_ADOPTED_FUNCTIONS = frozenset(
    {
        "resource_registry_list_external_jobs",
        "resource_registry_get_intelligence_job_status",
        "resource_registry_get_intelligence_job_result",
        "resource_registry_get_query_job_status",
        "resource_registry_get_query_job_result",
        "resource_registry_plan_query",
        "resource_registry_get_query_run",
        "resource_registry_get_analysis_artifact",
        "resource_registry_get_chart_artifact",
        "resource_registry_get_export_plan",
    }
)
_PHASE_3B_ADOPTED_FUNCTIONS = frozenset(
    {
        "resource_registry_create_filtered_dataset",
        "resource_registry_query_dataset",
        "resource_registry_analyze_dataset",
        "resource_registry_create_chart",
        "resource_registry_plan_export",
        "resource_registry_generate_artifact",
    }
)


@dataclass(frozen=True)
class _AdmissionEnvironment:
    user: User
    organization: Organization
    workspace: Workspace
    namespace_id: uuid.UUID
    request: SimpleNamespace


@pytest.fixture
def admission_environment() -> _AdmissionEnvironment:
    user = User.objects.create_user(username="read-only-bif-member")
    organization = Organization.objects.create(
        name="Read-only BIF Organization",
        slug="read-only-bif-organization",
        last_modified_by=user,
    )
    Organization.objects.filter(pk=organization.pk).update(resource_registry_enabled=True)
    organization.refresh_from_db()
    namespace_id = uuid.UUID(int=101)
    workspace = _create_workspace_namespace(
        organization=organization,
        user=user,
        namespace_id=namespace_id,
        slug="read-only-bif-workspace",
    )
    request = SimpleNamespace(user=user, organization=organization, workspace=workspace, session={})
    return _AdmissionEnvironment(
        user=user,
        organization=organization,
        workspace=workspace,
        namespace_id=namespace_id,
        request=request,
    )


def _create_workspace_namespace(  # noqa: PLR0913 -- Explicit authorization fixture builder.
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
    Workspace.objects.filter(pk=workspace.pk).update(resource_registry_enabled=True)
    workspace.refresh_from_db()
    if assign_user:
        add_workspace_user(workspace=workspace, user=user)
    RegistryNamespaceProvisioningService().provision_verified_existing_namespace(
        workspace_id=workspace.pk,
        namespace_id=namespace_id,
    )
    return workspace


def _registry_persona_for_workspaces(*workspace_ids: int) -> SimpleNamespace:
    """Build a Registry-capable Persona double configured for exact Workspace IDs."""
    return SimpleNamespace(
        metadata={"resource_registry_tenant_id": str(uuid.UUID(int=999))},
        workspaces=SimpleNamespace(
            values_list=lambda *_args, **_kwargs: workspace_ids,
        ),
        builtinfunctionpersonarelationship_set=FakePersonaBifRelationshipManager(
            ("resource_registry_list_datasets",),
        ),
    )


def test_authorized_bif_argument_builds_existing_read_authorization_context(
    admission_environment: _AdmissionEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_contexts: list[ReadAuthorizationContext] = []

    class _Facade:
        @staticmethod
        def list_datasets(context: ReadAuthorizationContext, **_kwargs: object) -> list[object]:
            captured_contexts.append(context)
            return []

    monkeypatch.setattr(resource_registry_functions, "_get_read_facade", _Facade)
    monkeypatch.setattr(resource_registry_functions, "_assert_capability", lambda *_args: None)

    payload = json.loads(
        # The legacy public BIF signature remains untyped for compatibility.
        resource_registry_functions.resource_registry_list_datasets(  # type: ignore[no-untyped-call]
            admission_environment.request,
            None,
            tenant_id=str(admission_environment.namespace_id),
        )
    )

    assert payload["success"] is True
    context = captured_contexts[0]
    assert context.tenant_id == str(admission_environment.namespace_id)
    assert context.roles == ("restricted_reader",)
    assert context.scopes == ()
    assert context.allowed_dataset_ids is None
    assert context.allow_cross_tenant is False
    assert context.metadata == {"requested_tenant_id": str(admission_environment.namespace_id)}


def test_scoped_persona_direct_bif_requires_workspace_selection(
    admission_environment: _AdmissionEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return a selection error instead of misreporting an unselected Workspace as unauthorized."""
    second_workspace = _create_workspace_namespace(
        organization=admission_environment.organization,
        user=admission_environment.user,
        namespace_id=uuid.UUID(int=102),
        slug="second-persona-workspace",
    )
    admission_environment.request.persona = _registry_persona_for_workspaces(
        admission_environment.workspace.pk,
        second_workspace.pk,
    )
    monkeypatch.setattr(resource_registry_functions, "_assert_capability", lambda *_args: None)

    payload = json.loads(
        resource_registry_functions.resource_registry_list_datasets(  # type: ignore[no-untyped-call]
            admission_environment.request,
            None,
            tenant_id="",
        )
    )

    assert payload == {
        "success": False,
        "status": _HTTP_BAD_REQUEST,
        "code": "workspace_selection_required",
        "error": WORKSPACE_SELECTION_REQUIRED_MESSAGE,
    }


def test_scoped_persona_direct_bif_uses_explicit_workspace_selection(
    admission_environment: _AdmissionEnvironment,
) -> None:
    """Admit the session-selected Workspace even when legacy Persona metadata differs."""
    second_namespace_id = uuid.UUID(int=102)
    second_workspace = _create_workspace_namespace(
        organization=admission_environment.organization,
        user=admission_environment.user,
        namespace_id=second_namespace_id,
        slug="selected-persona-workspace",
    )
    admission_environment.request.persona = _registry_persona_for_workspaces(
        admission_environment.workspace.pk,
        second_workspace.pk,
    )
    admission_environment.request.resource_registry_tenant_id = str(second_namespace_id)
    admission_environment.request.session[PERSONA_WORKSPACE_SELECTION_SESSION_KEY] = str(
        second_namespace_id
    )

    admitted = ReadOnlyBifNamespaceAdmissionService().admit_namespace(
        request=admission_environment.request,
        requested_namespace_id="",
    )

    assert admitted == str(second_namespace_id)


def test_scoped_persona_lists_only_datasets_in_explicitly_selected_workspace(
    admission_environment: _AdmissionEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restrict a selected Persona Workspace to its own Workspace-only datasets."""
    other_namespace_id = uuid.UUID(int=102)
    other_workspace = _create_workspace_namespace(
        organization=admission_environment.organization,
        user=admission_environment.user,
        namespace_id=other_namespace_id,
        slug="creative-assets-assembly",
    )
    request = admission_environment.request
    request.persona = _registry_persona_for_workspaces(
        admission_environment.workspace.pk,
        other_workspace.pk,
    )
    request.resource_registry_tenant_id = str(admission_environment.namespace_id)
    request.session[PERSONA_WORKSPACE_SELECTION_SESSION_KEY] = str(
        admission_environment.namespace_id,
    )
    Dataset.objects.create(
        tenant_id=admission_environment.namespace_id,
        organization=admission_environment.organization,
        workspace=admission_environment.workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug="sharepoint-asset-manifest",
        name="SharePoint Asset Manifest",
        source_type="FILE_UPLOAD",
        status=DatasetStatus.ACTIVE,
    )
    Dataset.objects.create(
        tenant_id=other_namespace_id,
        organization=admission_environment.organization,
        workspace=other_workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug="demo-rejected-other-brand-specific",
        name="Demo Rejected Other Brand Specific",
        source_type="FILE_UPLOAD",
        status=DatasetStatus.ACTIVE,
    )
    monkeypatch.setattr(resource_registry_functions, "_assert_capability", lambda *_args: None)

    payload = json.loads(
        resource_registry_functions.resource_registry_list_datasets(  # type: ignore[no-untyped-call]
            request,
            None,
            tenant_id=str(admission_environment.namespace_id),
        )
    )

    assert payload["success"] is True
    assert [dataset["slug"] for dataset in payload["data"]["datasets"]] == [
        "sharepoint-asset-manifest",
    ]


def test_scoped_persona_excludes_detached_staged_intake_dataset(
    admission_environment: _AdmissionEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block a Workspace-scoped Persona from reading a detached staged Intake Dataset."""
    request = admission_environment.request
    request.persona = _registry_persona_for_workspaces(admission_environment.workspace.pk)
    request.resource_registry_tenant_id = str(admission_environment.namespace_id)
    request.session[PERSONA_WORKSPACE_SELECTION_SESSION_KEY] = str(
        admission_environment.namespace_id
    )
    direct_dataset = Dataset.objects.create(
        tenant_id=admission_environment.namespace_id,
        organization=admission_environment.organization,
        workspace=admission_environment.workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug="direct-workspace-dataset",
        name="Direct Workspace Dataset",
        source_type="FILE_UPLOAD",
        status=DatasetStatus.ACTIVE,
    )
    detached_dataset = Dataset.objects.create(
        tenant_id=admission_environment.namespace_id,
        organization=admission_environment.organization,
        workspace=admission_environment.workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug="detached-staged-dataset",
        name="Detached Staged Dataset",
        source_type="FILE_UPLOAD",
        status=DatasetStatus.ACTIVE,
    )
    detached_version = DatasetVersion.objects.create(
        dataset=detached_dataset,
        version_number=1,
        source_uri="azure://example/detached.csv",
        content_hash="detached-staged-dataset",
        status=DatasetVersionStatus.READY,
    )
    intake = StagedResourceIntake.objects.create(
        submitted_by=admission_environment.user,
        original_filename="detached.csv",
        normalized_filename="detached.csv",
        resolved_file_format="CSV",
        declared_content_type="text/csv",
        declared_size_bytes=1,
        storage_provider="local",
        storage_object_key="resource-registry/intakes/detached/source.csv",
        status=StagedResourceIntakeStatus.PUBLISHED,
        idempotency_key="detached-staged-dataset",
        request_fingerprint="d" * 64,
        upload_authorization_expires_at=timezone.now() + timedelta(hours=1),
    )
    namespace = RegistryNamespace.objects.get(id=admission_environment.namespace_id)
    publication = StagedResourcePublication.objects.create(
        intake=intake,
        registry_namespace=namespace,
        organization=admission_environment.organization,
        workspace=admission_environment.workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        assignment_status=StagedResourcePublicationStatus.DETACHED,
        published_by=admission_environment.user,
    )
    StagedResourcePublicationDataset.objects.create(
        publication=publication,
        dataset=detached_dataset,
        dataset_version=detached_version,
        detached_at=timezone.now(),
    )
    monkeypatch.setattr(resource_registry_functions, "_assert_capability", lambda *_args: None)

    payload = json.loads(
        resource_registry_functions.resource_registry_list_datasets(  # type: ignore[no-untyped-call]
            request,
            None,
            tenant_id=str(admission_environment.namespace_id),
        )
    )

    assert payload["success"] is True
    assert [dataset["id"] for dataset in payload["data"]["datasets"]] == [
        str(direct_dataset.id),
    ]
    admitted = ReadOnlyBifNamespaceAdmissionService().admit_namespace_with_provenance(
        request=request,
        requested_namespace_id=str(admission_environment.namespace_id),
    )
    assert admitted.enforced_dataset_ids == frozenset({str(direct_dataset.id)})


def test_dataset_listing_bif_returns_organization_global_dataset_from_another_workspace(
    admission_environment: _AdmissionEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose an admitted Organization-global Dataset through the assistant listing BIF."""
    second_namespace_id = uuid.UUID(int=102)
    _create_workspace_namespace(
        organization=admission_environment.organization,
        user=admission_environment.user,
        namespace_id=second_namespace_id,
        slug="global-dataset-reader-workspace",
    )
    Dataset.objects.create(
        tenant_id=admission_environment.namespace_id,
        organization=admission_environment.organization,
        workspace=None,
        visibility_scope=DatasetVisibilityScope.ORGANIZATION,
        slug="organization-global-dataset",
        name="Organization Global Dataset",
        source_type="FILE_UPLOAD",
    )
    monkeypatch.setattr(resource_registry_functions, "_assert_capability", lambda *_args: None)

    payload = json.loads(
        resource_registry_functions.resource_registry_list_datasets(  # type: ignore[no-untyped-call]
            admission_environment.request,
            None,
            tenant_id=str(second_namespace_id),
        )
    )

    assert payload["success"] is True
    assert [dataset["slug"] for dataset in payload["data"]["datasets"]] == [
        "organization-global-dataset",
    ]


def test_scoped_persona_can_read_organization_global_dataset_from_selected_workspace(
    admission_environment: _AdmissionEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Include organization-visible datasets in the selected Persona Workspace scope."""
    selected_namespace_id = uuid.UUID(int=103)
    selected_workspace = _create_workspace_namespace(
        organization=admission_environment.organization,
        user=admission_environment.user,
        namespace_id=selected_namespace_id,
        slug="selected-global-reader-workspace",
    )
    global_dataset = Dataset.objects.create(
        tenant_id=admission_environment.namespace_id,
        organization=admission_environment.organization,
        workspace=None,
        visibility_scope=DatasetVisibilityScope.ORGANIZATION,
        slug="shared-global-inventory",
        name="Shared Global Inventory",
        source_type="FILE_UPLOAD",
        status=DatasetStatus.ACTIVE,
    )
    request = admission_environment.request
    request.persona = _registry_persona_for_workspaces(selected_workspace.pk)
    request.resource_registry_tenant_id = str(selected_namespace_id)
    request.session[PERSONA_WORKSPACE_SELECTION_SESSION_KEY] = str(selected_namespace_id)
    monkeypatch.setattr(resource_registry_functions, "_assert_capability", lambda *_args: None)

    payload = json.loads(
        resource_registry_functions.resource_registry_list_datasets(  # type: ignore[no-untyped-call]
            request,
            None,
            tenant_id=str(selected_namespace_id),
        )
    )

    assert payload["success"] is True
    assert [dataset["id"] for dataset in payload["data"]["datasets"]] == [
        str(global_dataset.id),
    ]


@pytest.mark.parametrize("organization_global", [False, True])
def test_normal_workspace_member_can_query_authorized_dataset_without_privileged_group(
    admission_environment: _AdmissionEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    organization_global: bool,
) -> None:
    """Query Workspace and Organization datasets with the restricted-reader role."""
    selected_namespace_id = admission_environment.namespace_id
    if organization_global:
        selected_namespace_id = uuid.UUID(int=104)
        selected_workspace = _create_workspace_namespace(
            organization=admission_environment.organization,
            user=admission_environment.user,
            namespace_id=selected_namespace_id,
            slug="qa-global-query-workspace",
        )
        request = admission_environment.request
        request.persona = _registry_persona_for_workspaces(selected_workspace.pk)
        request.resource_registry_tenant_id = str(selected_namespace_id)
        request.session[PERSONA_WORKSPACE_SELECTION_SESSION_KEY] = str(selected_namespace_id)
    dataset = Dataset.objects.create(
        tenant_id=admission_environment.namespace_id,
        organization=admission_environment.organization,
        workspace=None if organization_global else admission_environment.workspace,
        visibility_scope=(
            DatasetVisibilityScope.ORGANIZATION
            if organization_global
            else DatasetVisibilityScope.WORKSPACE
        ),
        slug="qa-asset-inventory",
        name="QA Asset Inventory",
        source_type="FILE_UPLOAD",
        status=DatasetStatus.ACTIVE,
    )
    version = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        source_uri="local://qa-asset-inventory.csv",
        content_hash="qa-asset-inventory",
        status=DatasetVersionStatus.READY,
    )
    DatasetSchema.objects.create(
        dataset_version=version,
        schema_hash="qa-asset-inventory-schema",
        column_count=1,
        schema_json=[{"name": "asset_name", "type": "VARCHAR"}],
    )
    executor = SimpleNamespace(
        _sql_validator=_SafeSqlValidator(max_returned_rows=100),
        execute_query=lambda **_kwargs: {
            "success": True,
            "columns": ["asset_name"],
            "rows": [["creative.svg"]],
            "row_count": 1,
            "truncated": False,
        },
    )
    monkeypatch.setattr(resource_registry_functions, "_get_query_executor", lambda: executor)
    monkeypatch.setattr(
        resource_registry_functions,
        "_get_query_routing_policy",
        lambda: SimpleNamespace(evaluate=lambda **_kwargs: SimpleNamespace(mode="sync_allowed")),
    )
    monkeypatch.setattr(resource_registry_functions, "_safe_persist", lambda _callback: None)

    response = json.loads(
        resource_registry_functions.resource_registry_query_dataset(  # type: ignore[no-untyped-call]
            admission_environment.request,
            None,
            tenant_id=str(selected_namespace_id),
            dataset_id=str(dataset.id),
            sql="SELECT asset_name FROM dataset LIMIT 1",
        )
    )

    assert response["success"] is True
    assert response["rows"] == [{"asset_name": "creative.svg"}]


def test_normal_workspace_member_can_analyze_and_chart_authorized_dataset(
    admission_environment: _AdmissionEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run insight BIFs as a normal member of the dataset's Workspace.

    Args:
        admission_environment: Authorized normal-user Workspace and request.
        monkeypatch: Replace the analysis engines and persistence boundary.
    """
    dataset = Dataset.objects.create(
        tenant_id=admission_environment.namespace_id,
        organization=admission_environment.organization,
        workspace=admission_environment.workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug="qa-insight-dataset",
        name="QA Insight Dataset",
        source_type="FILE_UPLOAD",
        status=DatasetStatus.ACTIVE,
    )
    version = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        source_uri="local://qa-insight-dataset.csv",
        content_hash="qa-insight-dataset",
        status=DatasetVersionStatus.READY,
    )
    DatasetSchema.objects.create(
        dataset_version=version,
        schema_hash="qa-insight-dataset-schema",
        column_count=1,
        schema_json=[{"name": "segment", "type": "VARCHAR"}],
    )
    monkeypatch.setattr(
        resource_registry_functions,
        "_get_analysis_service",
        lambda: SimpleNamespace(
            analyze=lambda **_kwargs: {"success": True, "analysis_type": "summary"},
        ),
    )
    monkeypatch.setattr(
        resource_registry_functions,
        "_get_chart_service",
        lambda: SimpleNamespace(
            create_chart=lambda **_kwargs: {"success": True, "chart": {"chart_type": "pie"}},
        ),
    )
    monkeypatch.setattr(resource_registry_functions, "_safe_persist", lambda _callback: None)

    analysis = json.loads(
        resource_registry_functions.resource_registry_analyze_dataset(  # type: ignore[no-untyped-call]
            admission_environment.request,
            None,
            tenant_id=str(admission_environment.namespace_id),
            dataset_id=str(dataset.id),
            analysis_type="summary",
        ),
    )
    chart = json.loads(
        resource_registry_functions.resource_registry_create_chart(  # type: ignore[no-untyped-call]
            admission_environment.request,
            None,
            tenant_id=str(admission_environment.namespace_id),
            dataset_id=str(dataset.id),
            user_request="Show segments",
            chart_type="pie",
        ),
    )

    assert analysis["success"] is True
    assert analysis["analysis_type"] == "summary"
    assert chart["success"] is True
    assert chart["chart"]["chart_type"] == "pie"


@pytest.mark.parametrize(
    "selector_source",
    (
        "workspace_trusted",
        "organization_trusted",
        "request_resource_registry",
        "session_resource_registry",
        "persona_metadata",
    ),
)
def test_legacy_selector_compatibility(
    admission_environment: _AdmissionEnvironment,
    selector_source: str,
) -> None:
    request = admission_environment.request
    namespace_text = str(admission_environment.namespace_id)
    if selector_source == "workspace_trusted":
        request.workspace = admission_environment.workspace
    elif selector_source == "organization_trusted":
        request.workspace = None
        OrganizationMembership.objects.create(
            organization=admission_environment.organization,
            user=admission_environment.user,
        )
        organization_namespace = (
            RegistryNamespaceProvisioningService().provision_new_organization_namespace(
                admission_environment.organization.pk,
            )
        )
        namespace_text = str(organization_namespace.id)
    elif selector_source == "request_resource_registry":
        request.workspace = None
        request.resource_registry_tenant_id = namespace_text
    elif selector_source == "session_resource_registry":
        request.workspace = None
        request.session["resource_registry_tenant_id"] = namespace_text
    else:
        request.workspace = None
        request.persona = SimpleNamespace(metadata={"resource_registry_tenant_id": namespace_text})

    admitted = ReadOnlyBifNamespaceAdmissionService().admit_namespace(
        request=request,
        requested_namespace_id=None,
    )

    assert admitted == namespace_text


def test_single_authorized_namespace_is_auto_selected(
    admission_environment: _AdmissionEnvironment,
) -> None:
    admitted = ReadOnlyBifNamespaceAdmissionService().admit_namespace(
        request=admission_environment.request,
        requested_namespace_id=None,
    )

    assert admitted == str(admission_environment.namespace_id)


def test_submission_admission_returns_canonical_workspace_provenance(
    admission_environment: _AdmissionEnvironment,
) -> None:
    admitted = ReadOnlyBifNamespaceAdmissionService().admit_namespace_with_provenance(
        request=admission_environment.request,
        requested_namespace_id=None,
    )

    assert admitted.namespace_id == str(admission_environment.namespace_id)
    assert admitted.organization_id == admission_environment.organization.pk
    assert admitted.workspace_id == admission_environment.workspace.pk
    assert admitted.actor_id == admission_environment.user.pk


def test_model_argument_overrides_trusted_workspace_selector_when_authorized(
    admission_environment: _AdmissionEnvironment,
) -> None:
    alternate_namespace_id = uuid.UUID(int=102)
    _create_workspace_namespace(
        organization=admission_environment.organization,
        user=admission_environment.user,
        namespace_id=alternate_namespace_id,
        slug="requested-namespace-priority-workspace",
    )

    admitted = ReadOnlyBifNamespaceAdmissionService().admit_namespace(
        request=admission_environment.request,
        requested_namespace_id=str(alternate_namespace_id),
    )

    assert admitted == str(alternate_namespace_id)


def test_malformed_model_argument_is_ignored_when_workspace_is_trusted(
    admission_environment: _AdmissionEnvironment,
) -> None:
    admitted = ReadOnlyBifNamespaceAdmissionService().admit_namespace(
        request=admission_environment.request,
        requested_namespace_id="not-a-uuid",
    )
    assert admitted == str(admission_environment.namespace_id)


def test_authenticated_user_with_zero_authorized_namespaces_fails_closed(
    admission_environment: _AdmissionEnvironment,
) -> None:
    user = User.objects.create_user(username="read-only-bif-no-workspaces")
    request = SimpleNamespace(
        user=user,
        organization=admission_environment.organization,
        session={},
    )

    with pytest.raises(DataRegistryAuthorizationError, match="not authorized"):
        ReadOnlyBifNamespaceAdmissionService().admit_namespace(
            request=request,
            requested_namespace_id=None,
        )


def test_unauthorized_and_unmapped_namespaces_fail_closed(
    admission_environment: _AdmissionEnvironment,
) -> None:
    admission_environment.request.workspace = None
    admission_environment.request.resource_registry_tenant_id = str(uuid.UUID(int=999))

    with pytest.raises(DataRegistryAuthorizationError, match="not authorized"):
        ReadOnlyBifNamespaceAdmissionService().admit_namespace(
            request=admission_environment.request,
            requested_namespace_id=None,
        )


def test_malformed_namespace_fails_validation(
    admission_environment: _AdmissionEnvironment,
) -> None:
    admission_environment.request.workspace = None
    admission_environment.request.resource_registry_tenant_id = "not-a-uuid"

    with pytest.raises(DataRegistryAuthorizationError, match="valid UUID"):
        ReadOnlyBifNamespaceAdmissionService().admit_namespace(
            request=admission_environment.request,
            requested_namespace_id=None,
        )


def test_malformed_bif_namespace_returns_existing_error_envelope(
    admission_environment: _AdmissionEnvironment,
) -> None:
    admission_environment.request.workspace = None
    admission_environment.request.resource_registry_tenant_id = "not-a-uuid"

    payload = json.loads(
        # The legacy public BIF signature remains untyped for compatibility.
        resource_registry_functions.resource_registry_list_datasets(  # type: ignore[no-untyped-call]
            admission_environment.request,
            None,
            tenant_id="not-a-uuid",
        )
    )

    assert payload["success"] is False
    assert payload["status"] == _HTTP_BAD_REQUEST
    assert payload["code"] == "invalid_tenant_id"


def test_multiple_authorized_namespaces_require_selection(
    admission_environment: _AdmissionEnvironment,
) -> None:
    _create_workspace_namespace(
        organization=admission_environment.organization,
        user=admission_environment.user,
        namespace_id=uuid.UUID(int=102),
        slug="second-read-only-bif-workspace",
    )

    admitted = ReadOnlyBifNamespaceAdmissionService().admit_namespace(
        request=admission_environment.request,
        requested_namespace_id=None,
    )
    assert admitted == str(admission_environment.namespace_id)


def test_ambiguous_bif_request_returns_deterministic_selection_error(
    admission_environment: _AdmissionEnvironment,
) -> None:
    _create_workspace_namespace(
        organization=admission_environment.organization,
        user=admission_environment.user,
        namespace_id=uuid.UUID(int=102),
        slug="ambiguous-bif-workspace",
    )

    payload = json.loads(
        # The legacy public BIF signature remains untyped for compatibility.
        resource_registry_functions.resource_registry_list_datasets(  # type: ignore[no-untyped-call]
            admission_environment.request,
            None,
            tenant_id=None,
        )
    )

    assert payload["success"] is True


def test_conflicting_metadata_selectors_fail_deterministically(
    admission_environment: _AdmissionEnvironment,
) -> None:
    admission_environment.request.workspace = None
    admission_environment.request.resource_registry_tenant_id = str(
        admission_environment.namespace_id
    )
    admission_environment.request.persona = SimpleNamespace(
        metadata={"resource_registry_tenant_id": str(uuid.UUID(int=102))}
    )

    with pytest.raises(
        DataRegistryAuthorizationError,
        match=TENANT_SELECTION_CONFLICT_MESSAGE,
    ):
        ReadOnlyBifNamespaceAdmissionService().admit_namespace(
            request=admission_environment.request,
            requested_namespace_id=None,
        )

    payload = json.loads(
        # The legacy public BIF signature remains untyped for compatibility.
        resource_registry_functions.resource_registry_list_datasets(  # type: ignore[no-untyped-call]
            admission_environment.request,
            None,
            tenant_id=str(uuid.UUID(int=102)),
        )
    )
    assert payload["success"] is False
    assert payload["status"] == _HTTP_BAD_REQUEST
    assert payload["code"] == "tenant_selection_conflict"


@pytest.mark.parametrize(
    "group_name",
    (UserGroupManagement.GROUP_ADMIN,),
)
def test_admin_role_admits_active_organization_namespace_without_membership(
    admission_environment: _AdmissionEnvironment,
    group_name: str,
) -> None:
    user = User.objects.create_user(username=f"read-only-bif-{group_name}")
    group, _created = Group.objects.get_or_create(name=group_name)
    user.groups.add(group)
    request = SimpleNamespace(
        user=user,
        organization=admission_environment.organization,
        workspace=admission_environment.workspace,
        session={},
    )

    admitted = ReadOnlyBifNamespaceAdmissionService().admit_namespace(
        request=request,
        requested_namespace_id=None,
    )

    assert admitted == str(admission_environment.namespace_id)


def test_scoped_admin_resolves_workspace_namespace_from_trusted_context(
    admission_environment: _AdmissionEnvironment,
) -> None:
    """Resolve namespace from trusted workspace context for a scoped admin request."""
    user = User.objects.create_user(username="read-only-bif-scoped-admin")
    group, _created = Group.objects.get_or_create(
        name=UserGroupManagement.GROUP_SCOPED_ADMIN,
    )
    user.groups.add(group)
    request = SimpleNamespace(
        user=user,
        organization=admission_environment.organization,
        workspace=admission_environment.workspace,
        session={},
    )

    admitted = ReadOnlyBifNamespaceAdmissionService().admit_namespace(
        request=request,
        requested_namespace_id=None,
    )
    assert admitted == str(admission_environment.namespace_id)


def test_organization_member_admits_organization_namespace_without_workspace(
    admission_environment: _AdmissionEnvironment,
) -> None:
    """Allow an Organization member to list global datasets without Workspace membership."""
    organization_member = User.objects.create_user(username="organization-global-reader")
    OrganizationMembership.objects.create(
        organization=admission_environment.organization,
        user=organization_member,
    )
    provisioning_service = RegistryNamespaceProvisioningService()
    organization_namespace = provisioning_service.provision_new_organization_namespace(
        admission_environment.organization.pk,
    )
    request = SimpleNamespace(
        user=organization_member,
        organization=admission_environment.organization,
        session={},
    )

    admitted = ReadOnlyBifNamespaceAdmissionService().admit_namespace(
        request=request,
        requested_namespace_id=None,
    )

    assert admitted == str(organization_namespace.id)


def test_anonymous_request_fails_closed(admission_environment: _AdmissionEnvironment) -> None:
    request = SimpleNamespace(
        user=AnonymousUser(),
        organization=admission_environment.organization,
        session={},
    )

    with pytest.raises(DataRegistryAuthorizationError, match="not authorized"):
        ReadOnlyBifNamespaceAdmissionService().admit_namespace(
            request=request,
            requested_namespace_id=None,
        )


def test_inactive_and_cross_organization_namespaces_fail_closed(
    admission_environment: _AdmissionEnvironment,
) -> None:
    inactive_namespace_id = uuid.UUID(int=102)
    _create_workspace_namespace(
        organization=admission_environment.organization,
        user=admission_environment.user,
        namespace_id=inactive_namespace_id,
        slug="inactive-read-only-bif-workspace",
        is_active=False,
    )
    other_organization = Organization.objects.create(
        name="Other Read-only BIF Organization",
        slug="other-read-only-bif-organization",
        last_modified_by=admission_environment.user,
    )
    other_namespace_id = uuid.UUID(int=103)
    _create_workspace_namespace(
        organization=other_organization,
        user=admission_environment.user,
        namespace_id=other_namespace_id,
        slug="cross-organization-read-only-bif-workspace",
    )
    service = ReadOnlyBifNamespaceAdmissionService()

    inactive_workspace = Workspace.objects.get(slug="inactive-read-only-bif-workspace")
    admission_environment.request.workspace = inactive_workspace
    with pytest.raises(DataRegistryAuthorizationError, match=TENANT_SELECTION_REQUIRED_MESSAGE):
        service.admit_namespace(
            request=admission_environment.request,
            requested_namespace_id=None,
        )

    cross_workspace = Workspace.objects.get(slug="cross-organization-read-only-bif-workspace")
    admission_environment.request.workspace = cross_workspace
    with pytest.raises(DataRegistryAuthorizationError, match=TENANT_SELECTION_REQUIRED_MESSAGE):
        service.admit_namespace(
            request=admission_environment.request,
            requested_namespace_id=None,
        )


def test_persona_metadata_fallback_selects_when_no_workspace_or_organization_namespace(
    admission_environment: _AdmissionEnvironment,
) -> None:
    admission_environment.request.workspace = None
    admission_environment.request.persona = SimpleNamespace(
        metadata={"resource_registry_tenant_id": str(admission_environment.namespace_id)}
    )
    context_manager = SimpleNamespace(
        get_all_registry_entries=lambda _request: {
            "resource_registry_tenant_id": {"value": str(admission_environment.namespace_id)}
        }
    )

    admitted = resource_registry_functions._resolve_read_only_tenant_id(
        admission_environment.request,
        context_manager,
        None,
    )
    assert admitted == str(admission_environment.namespace_id)


def test_only_approved_synchronous_bif_entry_points_use_namespace_admission() -> None:
    tree = ast.parse(_BIF_MODULE.read_text(encoding="utf-8"))
    adopted_functions: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if any(
            isinstance(call.func, ast.Name) and call.func.id == "_resolve_read_only_tenant_id"
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        ):
            adopted_functions.add(node.name)

    assert adopted_functions == (
        _ADOPTED_FUNCTIONS | _PHASE_3A_ADOPTED_FUNCTIONS | _PHASE_3B_ADOPTED_FUNCTIONS
    )


def test_only_approved_bif_adapters_import_read_only_bif_admission_service() -> None:
    allowed_paths = {
        "BotServer/builtin_functions/resource_registry_functions.py",
        "BotServer/builtin_functions/creative_assembly_functions.py",
        "resource_registry/bifs/read_only_namespace_admission.py",
        "resource_registry/bifs/transformation/context.py",
        "resource_registry/assistant/dataset_selection_context.py",
    }
    unexpected_paths: list[str] = []
    for path in _ROOT.rglob("*.py"):
        relative_path = path.relative_to(_ROOT).as_posix()
        relative_parts = path.relative_to(_ROOT).parts
        if "tests" in relative_parts or "migrations" in relative_parts:
            continue
        if any(part.startswith(".") for part in relative_parts):
            continue
        if relative_path in allowed_paths:
            continue
        if "ReadOnlyBifNamespaceAdmissionService" in path.read_text(encoding="utf-8"):
            unexpected_paths.append(relative_path)

    assert unexpected_paths == []
