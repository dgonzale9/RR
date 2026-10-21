from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from BotServer.builtin_functions import resource_registry_functions
from resource_registry.models import Dataset
from resource_registry.read.dtos import (
    DatasetArtifactSummary,
    DatasetContextSummary,
    DatasetDetails,
    DatasetProfileSummary,
    DatasetSchemaSummary,
    DatasetSummary,
    DatasetVersionSummary,
    WorkbookContextSummary,
)
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.exceptions import (
    DatasetAccessDeniedError,
    MissingAuthorizationContextError,
    PermissionDeniedError,
    TenantAccessDeniedError,
)
from resource_registry.read_auth.field_filtering import FieldFilterService
from resource_registry.read_auth.permissions import ReadAuthorizationService
from resource_registry.read_auth.policy import AuthorizedDataRegistryReadFacade


def _context(**overrides):
    defaults = {
        "tenant_id": "tenant-a",
        "actor": "user-1",
        "roles": ("reader",),
        "scopes": (),
        "allowed_dataset_ids": None,
        "allow_cross_tenant": False,
        "metadata": {},
    }
    defaults.update(overrides)
    return ReadAuthorizationContext(**defaults)


def _dataset_summary(dataset_id: str) -> DatasetSummary:
    return DatasetSummary(
        id=dataset_id,
        tenant_id="tenant-a",
        slug=f"slug-{dataset_id}",
        name=f"Dataset {dataset_id}",
        source_type="FILE_UPLOAD",
        status="ACTIVE",
        created_at="2026-06-24T00:00:00+00:00",
        updated_at="2026-06-24T00:00:00+00:00",
    )


def _dataset_details(dataset_id: str = "d-1") -> DatasetDetails:
    return DatasetDetails(
        id=dataset_id,
        tenant_id="tenant-a",
        slug="sales",
        name="Sales",
        source_type="FILE_UPLOAD",
        status="ACTIVE",
        description=None,
        owner_team=None,
        tags=[],
        created_at="2026-06-24T00:00:00+00:00",
        updated_at="2026-06-24T00:00:00+00:00",
        archived_at=None,
    )


def _version_summary() -> DatasetVersionSummary:
    return DatasetVersionSummary(
        id="v-1",
        dataset_id="d-1",
        tenant_id="tenant-a",
        version_number=1,
        status="READY",
        source_uri="https://blob.example/data.parquet?sig=secret",
        row_count=10,
        column_count=3,
        created_at="2026-06-24T00:00:00+00:00",
    )


def _artifact_summary() -> DatasetArtifactSummary:
    return DatasetArtifactSummary(
        id="a-1",
        dataset_version_id="v-1",
        tenant_id="tenant-a",
        artifact_role="PRIMARY",
        uri="https://blob.example/data.parquet?sig=secret&x=1",
        format="PARQUET",
        size_bytes=100,
        checksum="sha256:abc",
        storage_provider="azure_blob",
        lifecycle_status="ACTIVE",
        created_at="2026-06-24T00:00:00+00:00",
        metadata_json={
            "preview": [{"id": 1}],
            "raw_content": "secret",
            "credentials": {"token": "abc"},
            "safe": "value",
        },
    )


def _schema_summary() -> DatasetSchemaSummary:
    return DatasetSchemaSummary(
        id="s-1",
        dataset_version_id="v-1",
        tenant_id="tenant-a",
        schema_hash="hash",
        column_count=2,
        schema_json=[{"name": "id", "type": "BIGINT", "preview": [1]}],
        created_at="2026-06-24T00:00:00+00:00",
    )


def _profile_summary() -> DatasetProfileSummary:
    return DatasetProfileSummary(
        id="p-1",
        dataset_version_id="v-1",
        tenant_id="tenant-a",
        profile_type="BASIC",
        profile_hash="hash",
        metrics_json={"row_count": 10, "credentials": {"secret": "x"}},
        computed_at="2026-06-24T00:00:00+00:00",
    )


def test_authorization_context_missing_tenant_rejected() -> None:
    auth = ReadAuthorizationService()
    with pytest.raises(MissingAuthorizationContextError):
        auth.assert_can_list_datasets(_context(tenant_id=""))


def test_default_fail_closed_without_permissions() -> None:
    auth = ReadAuthorizationService()
    with pytest.raises(PermissionDeniedError):
        auth.assert_can_read_dataset(_context(roles=(), scopes=()), "d-1")


def test_admin_and_reader_can_read_tenant_datasets() -> None:
    auth = ReadAuthorizationService()
    auth.assert_can_read_dataset(_context(roles=("admin",)), "d-1")
    auth.assert_can_read_dataset(_context(roles=("reader",)), "d-1")


def test_restricted_reader_limited_to_allowed_dataset_ids() -> None:
    auth = ReadAuthorizationService()
    context = _context(roles=("restricted_reader",), allowed_dataset_ids=frozenset({"d-1"}))
    auth.assert_can_read_dataset(context, "d-1")
    with pytest.raises(DatasetAccessDeniedError):
        auth.assert_can_read_dataset(context, "d-2")


def test_enforced_workspace_dataset_ids_exclude_known_global_and_other_workspace_ids() -> None:
    """Restrict listings and direct reads to the selected Workspace Dataset IDs."""
    auth = ReadAuthorizationService()
    context = _context(enforced_dataset_ids=frozenset({"workspace-dataset"}))

    assert auth.filter_allowed_dataset_ids(
        context,
        ["workspace-dataset", "organization-dataset", "other-workspace-dataset"],
    ) == ["workspace-dataset"]
    auth.assert_can_read_dataset(context, "workspace-dataset")
    for blocked_dataset_id in ("organization-dataset", "other-workspace-dataset"):
        with pytest.raises(DatasetAccessDeniedError):
            auth.assert_can_read_dataset(context, blocked_dataset_id)


def test_admin_cannot_bypass_enforced_workspace_dataset_ids() -> None:
    """Reject an administrator's known Dataset ID outside the forced Workspace."""
    auth = ReadAuthorizationService()
    context = _context(
        roles=("admin",),
        enforced_dataset_ids=frozenset({"workspace-dataset"}),
    )

    with pytest.raises(DatasetAccessDeniedError):
        auth.assert_can_read_dataset(context, "organization-dataset")


def test_dataset_acl_intersects_enforced_workspace_dataset_ids() -> None:
    """Intersect per-Dataset ACL grants with the Persona Workspace Dataset set."""
    auth = ReadAuthorizationService()
    context = _context(
        roles=("restricted_reader",),
        allowed_dataset_ids=frozenset({"workspace-dataset", "organization-dataset"}),
        enforced_dataset_ids=frozenset({"workspace-dataset", "other-workspace-dataset"}),
    )

    assert auth.get_effective_allowed_dataset_ids(context) == frozenset({"workspace-dataset"})
    assert auth.filter_allowed_dataset_ids(
        context,
        ["workspace-dataset", "organization-dataset", "other-workspace-dataset"],
    ) == ["workspace-dataset"]


def test_generic_registry_consumer_remains_unrestricted_without_persona_scope() -> None:
    """Preserve existing organization visibility when no Persona scope is supplied."""
    auth = ReadAuthorizationService()
    context = _context(roles=("admin",))

    assert auth.get_effective_allowed_dataset_ids(context) is None
    assert auth.filter_allowed_dataset_ids(context, ["organization-dataset", "other-workspace-dataset"]) == [
        "organization-dataset",
        "other-workspace-dataset",
    ]


def test_cross_tenant_rejected_by_default() -> None:
    auth = ReadAuthorizationService()
    context = _context(metadata={"requested_tenant_id": "tenant-b"})
    with pytest.raises(TenantAccessDeniedError):
        auth.assert_can_list_datasets(context)


def test_scope_grants_schema_read_and_missing_scope_denies() -> None:
    auth = ReadAuthorizationService()
    denied = _context(roles=(), scopes=())
    with pytest.raises(PermissionDeniedError):
        auth.assert_can_read_schema(denied, "d-1")

    granted = _context(roles=(), scopes=("dataset:schema:read",))
    auth.assert_can_read_schema(granted, "d-1")


def test_field_filter_sensitive_metadata_removed_without_permission() -> None:
    filter_service = FieldFilterService()
    filtered = filter_service.filter_dataset_artifact_summary(
        _artifact_summary(),
        _context(roles=("reader",)),
    )
    assert filtered.metadata_json == {}


def test_field_filter_sensitive_metadata_retained_with_permission() -> None:
    filter_service = FieldFilterService()
    context = _context(
        roles=("reader",),
        scopes=("dataset:sensitive_metadata:read",),
    )
    filtered = filter_service.filter_dataset_artifact_summary(_artifact_summary(), context)
    assert filtered.metadata_json.get("safe") == "value"
    assert "preview" not in filtered.metadata_json
    assert "credentials" not in filtered.metadata_json


def test_artifact_uri_hidden_without_artifact_permission() -> None:
    filter_service = FieldFilterService()
    context = _context(roles=(), scopes=("dataset:schema:read",))
    filtered = filter_service.filter_dataset_artifact_summary(_artifact_summary(), context)
    assert filtered.uri.startswith("redacted://")


def test_sas_tokens_removed_always() -> None:
    filter_service = FieldFilterService()
    filtered = filter_service.filter_dataset_artifact_summary(
        _artifact_summary(),
        _context(roles=("reader",)),
    )
    assert "sig=" not in filtered.uri


def test_dto_not_mutated_in_place() -> None:
    filter_service = FieldFilterService()
    original = _artifact_summary()
    source = replace(original)
    filtered = filter_service.filter_dataset_artifact_summary(source, _context(roles=("reader",)))
    assert source.metadata_json == original.metadata_json
    assert filtered.metadata_json == {}


def test_raw_preview_credential_fields_removed_always() -> None:
    filter_service = FieldFilterService()
    context = _context(
        roles=("reader",),
        scopes=("dataset:sensitive_metadata:read",),
    )
    filtered = filter_service.filter_dataset_artifact_summary(_artifact_summary(), context)
    assert "raw_content" not in filtered.metadata_json
    assert "preview" not in filtered.metadata_json
    assert "credentials" not in filtered.metadata_json


def test_authorized_facade_list_datasets_respects_allowed_ids() -> None:
    """Retain only the allowed UUID even if a read service returns extra candidates."""
    allowed_dataset_id = "22222222-2222-2222-2222-222222222222"
    dataset_read_service = MagicMock()
    dataset_read_service.list_datasets.return_value = [
        _dataset_summary("d-1"),
        _dataset_summary(allowed_dataset_id),
    ]
    facade = AuthorizedDataRegistryReadFacade(dataset_read_service=dataset_read_service)
    context = _context(
        roles=("restricted_reader",), allowed_dataset_ids=frozenset({allowed_dataset_id})
    )

    results = facade.list_datasets(context)

    assert [item.id for item in results] == [allowed_dataset_id]


@pytest.mark.parametrize("search", [None, "inventory"])
@pytest.mark.parametrize("scope_field", ["allowed_dataset_ids", "enforced_dataset_ids"])
@pytest.mark.parametrize("invalid_id", ["stale-invalid-id", "", "None"])
def test_authorized_listing_rejects_malformed_scope_before_querying(
    search: str | None, scope_field: str, invalid_id: str
) -> None:
    """Reject malformed scope IDs without querying or exposing their values.

    Args:
        search: Optional search term selecting the search path.
        scope_field: Authorization restriction containing the malformed ID.
        invalid_id: Invalid identifier mixed with an otherwise valid grant.
    """
    service = MagicMock()
    context = _context(
        **{scope_field: frozenset({"22222222-2222-2222-2222-222222222222", invalid_id})}
    )
    facade = AuthorizedDataRegistryReadFacade(dataset_read_service=service)

    with pytest.raises(DatasetAccessDeniedError) as error:
        facade.list_datasets(context, search=search, limit=2)

    assert str(error.value) == "Dataset authorization scope contains an invalid identifier."
    service.list_datasets.assert_not_called()
    service.search_datasets.assert_not_called()


def test_dataset_listing_bif_returns_safe_authorization_error_for_malformed_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return a controlled BIF error before ORM access for a malformed effective scope.

    Args:
        monkeypatch: Fixture replacing admission with a pre-resolved server context.
    """
    context = _context(
        tenant_id="11111111-1111-1111-1111-111111111111",
        allowed_dataset_ids=frozenset({"stale-invalid-id"}),
    )
    query = MagicMock()
    monkeypatch.setattr(Dataset.objects, "filter", query)
    monkeypatch.setattr(
        resource_registry_functions,
        "_resolve_read_only_tenant_id",
        lambda *_args: context.tenant_id,
    )
    monkeypatch.setattr(resource_registry_functions, "_assert_capability", lambda *_args: None)
    monkeypatch.setattr(
        resource_registry_functions, "_build_authorization_context", lambda *_args: context
    )

    payload = json.loads(
        resource_registry_functions.resource_registry_list_datasets(
            SimpleNamespace(), None, tenant_id=context.tenant_id, limit=2
        )
    )

    assert payload["success"] is False
    assert payload["status"] == 403
    assert payload["code"] == "authorization_error"
    assert "stale-invalid-id" not in json.dumps(payload)
    query.assert_not_called()


def test_authorized_facade_list_datasets_forwards_workbook_filters() -> None:
    dataset_read_service = MagicMock()
    dataset_read_service.search_datasets.return_value = [_dataset_summary("d-1")]
    facade = AuthorizedDataRegistryReadFacade(dataset_read_service=dataset_read_service)

    _ = facade.list_datasets(
        _context(roles=("reader",)),
        search="sales",
        workbook_group_id="group-1",
        sheet_name="Summary",
        relevance_query="revenue trend",
    )

    kwargs = dataset_read_service.search_datasets.call_args.kwargs
    assert kwargs["workbook_group_id"] == "group-1"
    assert kwargs["sheet_name"] == "Summary"
    assert kwargs["relevance_query"] == "revenue trend"


def test_authorized_facade_get_dataset_denied_if_not_allowed() -> None:
    facade = AuthorizedDataRegistryReadFacade(dataset_read_service=MagicMock())
    context = _context(roles=("restricted_reader",), allowed_dataset_ids=frozenset({"d-2"}))
    with pytest.raises(DatasetAccessDeniedError):
        facade.get_dataset(context, "d-1")


def test_authorized_facade_get_context_applies_filtering() -> None:
    context_read_service = MagicMock()
    context_read_service.get_dataset_context.return_value = DatasetContextSummary(
        dataset=_dataset_details("d-1"),
        latest_version=_version_summary(),
        schema=_schema_summary(),
        profile=_profile_summary(),
        artifact=_artifact_summary(),
        policy_provenance={"token": "secret", "safe": "ok"},
    )
    facade = AuthorizedDataRegistryReadFacade(dataset_context_read_service=context_read_service)
    filtered = facade.get_dataset_context(_context(roles=("reader",)), "d-1")

    assert filtered.artifact is not None
    assert filtered.artifact.metadata_json == {}
    assert "sig=" not in filtered.latest_version.source_uri
    assert filtered.policy_provenance == {"safe": "ok"}


def test_authorized_facade_get_workbook_context_returns_service_payload() -> None:
    workbook_context_service = MagicMock()
    workbook_context_service.get_workbook_context.return_value = WorkbookContextSummary(
        workbook_group_id="group-1",
        tenant_id="tenant-a",
        status="COMPLETED",
        source_file_name="sales.xlsx",
        source_uri="azure://datasets/sales.xlsx",
        source_hash="hash-1",
        total_sheets=2,
        total_created=2,
        total_deduplicated=0,
        total_skipped=0,
        sheets=[],
    )
    facade = AuthorizedDataRegistryReadFacade(
        workbook_context_read_service=workbook_context_service,
    )

    result = facade.get_workbook_context(_context(roles=("reader",)), "group-1")

    assert result.workbook_group_id == "group-1"
    workbook_context_service.get_workbook_context.assert_called_once_with(
        tenant_id="tenant-a",
        workbook_group_id="group-1",
        allowed_dataset_ids=None,
    )


def test_authorized_facade_get_workbook_context_propagates_dataset_scope() -> None:
    """Constrain workbook context to the reader's exact Dataset scope."""
    workbook_context_service = MagicMock()
    workbook_context_service.get_workbook_context.return_value = MagicMock()
    facade = AuthorizedDataRegistryReadFacade(
        workbook_context_read_service=workbook_context_service,
    )
    allowed_dataset_ids = frozenset({"dataset-1"})

    facade.get_workbook_context(
        _context(roles=("restricted_reader",), allowed_dataset_ids=allowed_dataset_ids),
        "group-1",
    )

    workbook_context_service.get_workbook_context.assert_called_once_with(
        tenant_id="tenant-a",
        workbook_group_id="group-1",
        allowed_dataset_ids=allowed_dataset_ids,
    )


def test_authorized_facade_schema_profile_artifact_job_authorization() -> None:
    schema_service = MagicMock()
    profile_service = MagicMock()
    artifact_service = MagicMock()
    job_service = MagicMock()

    schema_service.get_latest_schema.return_value = _schema_summary()
    profile_service.get_latest_profile.return_value = _profile_summary()
    artifact_service.list_artifacts.return_value = [_artifact_summary()]
    job_service.list_jobs.return_value = [SimpleNamespace()]

    facade = AuthorizedDataRegistryReadFacade(
        schema_read_service=schema_service,
        profile_read_service=profile_service,
        artifact_read_service=artifact_service,
        job_read_service=job_service,
    )
    denied_context = _context(roles=(), scopes=("dataset:read",))

    with pytest.raises(PermissionDeniedError):
        facade.get_schema(denied_context, "d-1")
    with pytest.raises(PermissionDeniedError):
        facade.get_profile(denied_context, "d-1")
    with pytest.raises(PermissionDeniedError):
        facade.list_artifacts(denied_context, "d-1")
    with pytest.raises(PermissionDeniedError):
        facade.list_jobs(denied_context, dataset_id="d-1")
