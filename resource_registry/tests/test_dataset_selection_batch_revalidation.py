"""Verify bounded cached-selection revalidation against persisted Registry rows."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from Organization.models import Organization, Workspace
from resource_registry.assistant import dataset_selection_context
from resource_registry.assistant.dataset_selection_context import (
    _revalidate_cached_selections,
    get_registry_selection_prompt_context,
    get_verified_registry_dataset_ids,
    resolve_registry_selection_tool_arguments,
)
from resource_registry.assistant.dataset_selection_store import (
    RegistryConversationSelectionScope,
    VerifiedDatasetSelection,
    save_selections,
)
from resource_registry.assistant.persona_capabilities import DataRegistryPersonaCapabilityService
from resource_registry.bifs.read_only_namespace_admission import AdmittedBifNamespace
from resource_registry.models import (
    Dataset,
    DatasetSourceType,
    DatasetStatus,
    DatasetVersion,
    DatasetVersionStatus,
    DatasetVisibilityScope,
)
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.registry_namespace_provisioning_service import (
    RegistryNamespaceProvisioningService,
)
from resource_registry.tests.workspace_permission_helpers import add_workspace_user


@dataclass(frozen=True)
class _SelectionBatchFixture:
    """Hold one persisted visibility scope for cached-selection revalidation tests."""

    user: User
    organization: Organization
    workspace: Workspace
    namespace_id: uuid.UUID
    datasets: tuple[Dataset, ...]


class _SelectionSession(dict[str, object]):
    """Provide the mapping and session-key behavior used by Registry authorization helpers."""

    session_key = "selection-batch-session"


@pytest.fixture
def selection_batch_fixture() -> Iterator[_SelectionBatchFixture]:
    """Create an admitted Workspace and enough visible Datasets for all batch boundaries."""
    with override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    ):
        cache.clear()
        user = User.objects.create_user(username="selection-batch-reader")
        organization = Organization.objects.create(
            name="Selection Batch Organization",
            slug="selection-batch-organization",
            last_modified_by=user,
        )
        Organization.objects.filter(pk=organization.pk).update(resource_registry_enabled=True)
        workspace = Workspace.objects.create(
            organization=organization,
            name="Selection Batch Workspace",
            slug="selection-batch-workspace",
            last_modified_by=user,
        )
        Workspace.objects.filter(pk=workspace.pk).update(resource_registry_enabled=True)
        add_workspace_user(workspace=workspace, user=user)
        namespace_id = uuid.uuid4()
        RegistryNamespaceProvisioningService().provision_verified_existing_namespace(
            workspace_id=workspace.pk,
            namespace_id=namespace_id,
        )
        datasets = tuple(
            Dataset.objects.create(
                tenant_id=namespace_id,
                organization=organization,
                workspace=workspace,
                visibility_scope=DatasetVisibilityScope.WORKSPACE,
                slug=f"selection-batch-{index}",
                name=f"Selection batch {index}",
                source_type=DatasetSourceType.FILE_UPLOAD,
                status=DatasetStatus.ACTIVE,
            )
            for index in range(105)
        )
        yield _SelectionBatchFixture(user, organization, workspace, namespace_id, datasets)
        cache.clear()


def _build_request(fixture: _SelectionBatchFixture) -> SimpleNamespace:
    """Build a request carrying only trusted persisted selection scope inputs."""
    return SimpleNamespace(
        user=fixture.user,
        organization=fixture.organization,
        workspace=fixture.workspace,
        persona=SimpleNamespace(pk=17),
        session=_SelectionSession(),
        parent_conversation_id="selection-batch-conversation",
        META={},
    )


def _build_selection(
    fixture: _SelectionBatchFixture,
    dataset: Dataset,
    *,
    version_id: str | None = None,
) -> VerifiedDatasetSelection:
    """Build one cache-shaped selection for the fixture's admitted Workspace."""
    return VerifiedDatasetSelection(
        namespace_id=str(fixture.namespace_id),
        workspace_id=fixture.workspace.pk,
        dataset_id=str(dataset.pk),
        version_id=version_id,
    )


@pytest.mark.django_db
def test_revalidation_processes_all_scale_counts_and_a_second_bounded_batch(
    selection_batch_fixture: _SelectionBatchFixture,
) -> None:
    """Return every visible selection with SQL growing by batches rather than selections."""
    request = _build_request(selection_batch_fixture)
    all_selections = tuple(
        _build_selection(selection_batch_fixture, dataset)
        for dataset in selection_batch_fixture.datasets
    )

    for count in (1, 5, 10, 25, 50):
        assert (
            _revalidate_cached_selections(
                request,
                str(selection_batch_fixture.namespace_id),
                all_selections[:count],
            )
            == all_selections[:count]
        )

    with CaptureQueriesContext(connection) as single_batch_queries:
        single_batch_visible = _revalidate_cached_selections(
            request,
            str(selection_batch_fixture.namespace_id),
            all_selections[:50],
        )
    with CaptureQueriesContext(connection) as second_batch_queries:
        visible = _revalidate_cached_selections(
            request,
            str(selection_batch_fixture.namespace_id),
            all_selections[:51],
        )

    assert single_batch_visible == all_selections[:50]
    assert visible == all_selections[:51]
    assert len(second_batch_queries) == len(single_batch_queries) + 1


@pytest.mark.django_db
def test_revalidation_preserves_exact_ready_version_pairing_and_duplicate_order(
    selection_batch_fixture: _SelectionBatchFixture,
) -> None:
    """Omit stale pins while retaining versionless and distinct READY selections in cache order."""
    request = _build_request(selection_batch_fixture)
    first_dataset, second_dataset, third_dataset = selection_batch_fixture.datasets[:3]
    first_ready_version = DatasetVersion.objects.create(
        dataset=first_dataset,
        version_number=1,
        source_uri="benchmark://selection/first-ready",
        content_hash="selection-first-ready",
        status=DatasetVersionStatus.READY,
    )
    second_ready_version = DatasetVersion.objects.create(
        dataset=first_dataset,
        version_number=2,
        source_uri="benchmark://selection/second-ready",
        content_hash="selection-second-ready",
        status=DatasetVersionStatus.READY,
    )
    pending_version = DatasetVersion.objects.create(
        dataset=second_dataset,
        version_number=1,
        source_uri="benchmark://selection/pending",
        content_hash="selection-pending",
        status=DatasetVersionStatus.PENDING,
    )
    other_dataset_ready_version = DatasetVersion.objects.create(
        dataset=third_dataset,
        version_number=1,
        source_uri="benchmark://selection/other-ready",
        content_hash="selection-other-ready",
        status=DatasetVersionStatus.READY,
    )
    versionless = _build_selection(selection_batch_fixture, first_dataset)
    first_pinned = _build_selection(
        selection_batch_fixture,
        first_dataset,
        version_id=str(first_ready_version.pk),
    )
    second_pinned = _build_selection(
        selection_batch_fixture,
        first_dataset,
        version_id=str(second_ready_version.pk),
    )
    selections = (
        versionless,
        first_pinned,
        second_pinned,
        first_pinned,
        _build_selection(
            selection_batch_fixture,
            second_dataset,
            version_id=str(pending_version.pk),
        ),
        _build_selection(
            selection_batch_fixture,
            first_dataset,
            version_id=str(other_dataset_ready_version.pk),
        ),
        _build_selection(
            selection_batch_fixture,
            third_dataset,
            version_id=str(uuid.uuid4()),
        ),
    )

    visible = _revalidate_cached_selections(
        request,
        str(selection_batch_fixture.namespace_id),
        selections,
    )

    assert visible == (versionless, first_pinned, second_pinned, first_pinned)


@pytest.mark.django_db
def test_revalidation_enforces_visibility_scope_and_authorized_dataset_ids(
    selection_batch_fixture: _SelectionBatchFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omit archived and cross-scope rows without reading or mutating their domain data."""
    fixture = selection_batch_fixture
    request = _build_request(fixture)
    visible_dataset, archived_dataset, unallowed_visible_dataset = fixture.datasets[:3]
    Dataset.objects.filter(pk=archived_dataset.pk).update(archived_at=timezone.now())
    other_workspace = Workspace.objects.create(
        organization=fixture.organization,
        name="Selection Batch Other Workspace",
        slug="selection-batch-other-workspace",
        last_modified_by=fixture.user,
    )
    Workspace.objects.filter(pk=other_workspace.pk).update(resource_registry_enabled=True)
    add_workspace_user(workspace=other_workspace, user=fixture.user)
    other_namespace_id = uuid.uuid4()
    RegistryNamespaceProvisioningService().provision_verified_existing_namespace(
        workspace_id=other_workspace.pk,
        namespace_id=other_namespace_id,
    )
    other_organization = Organization.objects.create(
        name="Selection Batch Other Organization",
        slug="selection-batch-other-organization",
        last_modified_by=fixture.user,
    )
    Organization.objects.filter(pk=other_organization.pk).update(resource_registry_enabled=True)
    other_organization_workspace = Workspace.objects.create(
        organization=other_organization,
        name="Selection Batch Other Organization Workspace",
        slug="selection-batch-other-organization-workspace",
        last_modified_by=fixture.user,
    )
    Workspace.objects.filter(pk=other_organization_workspace.pk).update(
        resource_registry_enabled=True
    )
    cross_workspace_dataset = Dataset.objects.create(
        tenant_id=fixture.namespace_id,
        organization=fixture.organization,
        workspace=other_workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug="selection-batch-cross-workspace",
        name="Selection Batch Cross Workspace",
        source_type=DatasetSourceType.FILE_UPLOAD,
        status=DatasetStatus.ACTIVE,
    )
    cross_organization_dataset = Dataset.objects.create(
        tenant_id=fixture.namespace_id,
        organization=other_organization,
        workspace=other_organization_workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug="selection-batch-cross-organization",
        name="Selection Batch Cross Organization",
        source_type=DatasetSourceType.FILE_UPLOAD,
        status=DatasetStatus.ACTIVE,
    )
    cross_namespace_dataset = Dataset.objects.create(
        tenant_id=other_namespace_id,
        organization=fixture.organization,
        workspace=other_workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug="selection-batch-cross-namespace",
        name="Selection Batch Cross Namespace",
        source_type=DatasetSourceType.FILE_UPLOAD,
        status=DatasetStatus.ACTIVE,
    )
    selections = tuple(
        _build_selection(fixture, dataset)
        for dataset in (
            visible_dataset,
            unallowed_visible_dataset,
            archived_dataset,
            cross_workspace_dataset,
            cross_organization_dataset,
            cross_namespace_dataset,
        )
    )
    before_dataset_count = Dataset.objects.count()
    monkeypatch.setattr(
        "BotServer.builtin_functions.resource_registry_functions._build_authorization_context",
        lambda request, namespace_id: ReadAuthorizationContext(
            tenant_id=namespace_id,
            roles=("reader",),
            allowed_dataset_ids=frozenset(
                {
                    str(visible_dataset.pk),
                    str(archived_dataset.pk),
                    str(cross_workspace_dataset.pk),
                    str(cross_organization_dataset.pk),
                    str(cross_namespace_dataset.pk),
                }
            ),
        ),
    )

    visible = _revalidate_cached_selections(request, str(fixture.namespace_id), selections)

    assert visible == (_build_selection(fixture, visible_dataset),)
    assert Dataset.objects.count() == before_dataset_count


@pytest.mark.django_db
def test_revalidation_omits_deleted_dataset_rows_without_affecting_neighbors(
    selection_batch_fixture: _SelectionBatchFixture,
) -> None:
    """Omit a missing cached Dataset while preserving still-visible neighboring selections."""
    fixture = selection_batch_fixture
    request = _build_request(fixture)
    visible_dataset, deleted_dataset = fixture.datasets[:2]
    selections = (
        _build_selection(fixture, visible_dataset),
        _build_selection(fixture, deleted_dataset),
    )
    Dataset.objects.filter(pk=deleted_dataset.pk).delete()

    visible = _revalidate_cached_selections(request, str(fixture.namespace_id), selections)

    assert visible == (selections[0],)


@pytest.mark.django_db
def test_prompt_keeps_its_last_fifty_limit_while_comparison_discovery_processes_all(
    selection_batch_fixture: _SelectionBatchFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep prompt presentation bounded without truncating verified comparison discovery."""
    fixture = selection_batch_fixture
    request = _build_request(fixture)
    selections = tuple(_build_selection(fixture, dataset) for dataset in fixture.datasets[:51])
    scope = RegistryConversationSelectionScope(
        user_id=fixture.user.pk,
        organization_id=fixture.organization.pk,
        root_persona_id=request.persona.pk,
        conversation_id=request.parent_conversation_id,
        browser_session_key=request.session.session_key,
    )
    save_selections(scope, list(selections))

    def admit(request: object, selector: object) -> str:
        """Bind the persisted fixture namespace as trusted admission provenance."""
        _ = selector
        setattr(  # noqa: B010
            request,
            "resource_registry_admitted_namespace",
            AdmittedBifNamespace(
                namespace_id=str(fixture.namespace_id),
                organization_id=fixture.organization.pk,
                workspace_id=fixture.workspace.pk,
                actor_id=fixture.user.pk,
            ),
        )
        return str(fixture.namespace_id)

    monkeypatch.setattr(
        "resource_registry.assistant.dataset_selection_context._admit_current_namespace",
        admit,
    )
    monkeypatch.setattr(
        DataRegistryPersonaCapabilityService,
        "has_resource_registry_capability",
        lambda service, persona: True,
    )

    prompt = get_registry_selection_prompt_context(request, request.persona)
    verified_ids = get_verified_registry_dataset_ids(request, str(fixture.namespace_id))

    assert selections[0].dataset_id not in prompt
    assert all(selection.dataset_id in prompt for selection in selections[1:])
    assert verified_ids == tuple(selection.dataset_id for selection in selections)


@pytest.mark.django_db
def test_discovery_omits_newly_archived_rows_and_direct_resolution_remains_fresh(
    selection_batch_fixture: _SelectionBatchFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep discovery response parity while a later direct resolution rejects a revoked row."""
    fixture = selection_batch_fixture
    request = _build_request(fixture)
    selected_dataset = fixture.datasets[0]
    selection = _build_selection(fixture, selected_dataset)
    scope = RegistryConversationSelectionScope(
        user_id=fixture.user.pk,
        organization_id=fixture.organization.pk,
        root_persona_id=request.persona.pk,
        conversation_id=request.parent_conversation_id,
        browser_session_key=request.session.session_key,
    )
    save_selections(scope, [selection])

    def admit(request: object, selector: object) -> str:
        """Bind the persisted fixture namespace as trusted admission provenance."""
        _ = selector
        setattr(  # noqa: B010
            request,
            "resource_registry_admitted_namespace",
            AdmittedBifNamespace(
                namespace_id=str(fixture.namespace_id),
                organization_id=fixture.organization.pk,
                workspace_id=fixture.workspace.pk,
                actor_id=fixture.user.pk,
            ),
        )
        return str(fixture.namespace_id)

    monkeypatch.setattr(
        "resource_registry.assistant.dataset_selection_context._admit_current_namespace",
        admit,
    )
    monkeypatch.setattr(
        DataRegistryPersonaCapabilityService,
        "has_resource_registry_capability",
        lambda service, persona: True,
    )

    assert get_verified_registry_dataset_ids(request, str(fixture.namespace_id)) == (
        str(selected_dataset.pk),
    )
    prompt = get_registry_selection_prompt_context(request, request.persona)
    assert prompt.count(str(selected_dataset.pk)) == 1
    Dataset.objects.filter(pk=selected_dataset.pk).update(archived_at=timezone.now())

    assert get_verified_registry_dataset_ids(request, str(fixture.namespace_id)) == ()
    with pytest.raises(ValueError, match="no longer authorized or available"):
        resolve_registry_selection_tool_arguments(
            request=request,
            function_name="resource_registry_query_dataset",
            function_args={
                "tenant_id": str(fixture.namespace_id),
                "dataset_id": str(selected_dataset.pk),
            },
            parameter_names=frozenset({"tenant_id", "dataset_id"}),
        )


@pytest.mark.django_db
def test_revalidation_omits_a_row_revoked_between_deterministic_batches(
    selection_batch_fixture: _SelectionBatchFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply each batch at its statement-time boundary without claiming an atomic snapshot."""
    fixture = selection_batch_fixture
    request = _build_request(fixture)
    selections = tuple(_build_selection(fixture, dataset) for dataset in fixture.datasets[:51])
    original_batches = dataset_selection_context._iter_selection_identifier_batches

    def archive_before_second_batch(identifiers: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
        """Archive the second-batch Dataset after the first query has completed."""
        batches = iter(original_batches(identifiers))
        yield next(batches)
        Dataset.objects.filter(pk=fixture.datasets[50].pk).update(archived_at=timezone.now())
        yield from batches

    monkeypatch.setattr(
        dataset_selection_context,
        "_iter_selection_identifier_batches",
        archive_before_second_batch,
    )

    visible = _revalidate_cached_selections(request, str(fixture.namespace_id), selections)

    assert visible == selections[:50]
