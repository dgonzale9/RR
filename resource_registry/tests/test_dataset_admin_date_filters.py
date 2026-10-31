"""Verify date ranges on the authorized, paginated Django Admin catalog."""

from datetime import datetime
from typing import TypeAlias, cast
from urllib.parse import parse_qs, urlencode
from uuid import uuid4

import pytest
from bs4 import BeautifulSoup
from django.contrib import admin
from django.contrib.admin.views.main import ChangeList
from django.contrib.auth.models import User
from django.db import connection
from django.template.response import TemplateResponse
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from Organization.models import Organization, Workspace
from resource_registry.dataset_admin import DatasetAdmin
from resource_registry.models import Dataset, DatasetVersion, RegistryNamespace
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401
from resource_registry.tests.test_dataset_management_admin import make_admin_request

Scope: TypeAlias = tuple[User, User, Organization, RegistryNamespace, Dataset]


def create_dated_dataset(scope: Scope, created_at: str, published_at: str | None) -> Dataset:
    """Create a dataset with distinct creation and current-version timestamps."""
    manager, _, organization, namespace, original = scope
    dataset = Dataset.objects.create(
        tenant_id=namespace.id,
        organization=organization,
        workspace=original.workspace,
        slug=f"dated-{uuid4()}",
        name="Dated sales",
        source_type="FILE_UPLOAD",
        status="ACTIVE",
        responsible_user=manager,
    )
    Dataset.objects.filter(pk=dataset.pk).update(created_at=datetime.fromisoformat(created_at))
    if published_at:
        version = DatasetVersion.objects.create(
            dataset=dataset,
            version_number=1,
            content_hash="first",
            source_uri="local://dated.csv",
            status="READY",
        )
        DatasetVersion.objects.filter(pk=version.pk).update(
            created_at=datetime.fromisoformat(published_at)
        )
        Dataset.objects.filter(pk=dataset.pk).update(current_version=version)
    return dataset


def render_date_catalog(scope: Scope, parameters: dict[str, str]) -> TemplateResponse:
    """Render the real authorized Admin changelist with the supplied query parameters."""
    manager, _, organization, _, _ = scope
    request = make_admin_request(
        manager, organization, f"/admin/resource_registry/dataset/?{urlencode(parameters)}"
    )
    response = admin.site._registry[Dataset].changelist_view(request)
    assert response.status_code == 200
    assert isinstance(response, TemplateResponse)
    response.render()
    return response


def get_catalog_rows(response: TemplateResponse) -> ChangeList:
    """Return the native changelist containing filtered rows and navigation state."""
    assert response.context_data is not None
    return cast(ChangeList, response.context_data["cl"])


@pytest.mark.parametrize("date_field", ["created", "updated"])
def test_date_range_includes_entire_local_day(management_scope: Scope, date_field: str) -> None:
    """Include both local boundaries, including the 23-hour daylight-saving day."""
    moments = [
        "2026-03-08T04:59:59+00:00",
        "2026-03-08T05:00:00+00:00",
        "2026-03-09T03:59:59.999999+00:00",
        "2026-03-09T04:00:00+00:00",
    ]
    datasets = [create_dated_dataset(management_scope, moment, moment) for moment in moments]
    with timezone.override("America/New_York"):
        response = render_date_catalog(
            management_scope,
            {"date_field": date_field, "date_from": "2026-03-08", "date_to": "2026-03-08"},
        )
    changelist = get_catalog_rows(response)
    assert set(changelist.queryset.values_list("pk", flat=True)) == {
        datasets[1].pk,
        datasets[2].pk,
    }
    assert changelist.result_count == 2
    assert "America/New_York" in response.content.decode()


@pytest.mark.parametrize(
    ("parameters", "expected_indexes"),
    [
        ({"date_from": "2026-05-02"}, {1, 2}),
        ({"date_to": "2026-05-02"}, {0, 1}),
        ({"date_from": "2026-05-02", "date_to": "2026-05-02"}, {1}),
    ],
)
def test_open_date_bounds_default_to_current_version(
    management_scope: Scope, parameters: dict[str, str], expected_indexes: set[int]
) -> None:
    """Support single-sided bounds and exclude datasets without a current version."""
    datasets = [
        create_dated_dataset(
            management_scope, "2025-01-01T12:00:00+00:00", f"2026-05-0{day}T12:00:00+00:00"
        )
        for day in (1, 2, 3)
    ]
    historical = DatasetVersion.objects.create(
        dataset=datasets[0],
        version_number=2,
        content_hash="later-history",
        source_uri="local://history.csv",
        status="READY",
    )
    DatasetVersion.objects.filter(pk=historical.pk).update(
        created_at=datetime.fromisoformat("2026-05-03T12:00:00+00:00")
    )
    response = render_date_catalog(management_scope, parameters)
    assert set(get_catalog_rows(response).queryset.values_list("pk", flat=True)) == {
        datasets[index].pk for index in expected_indexes
    }


def test_creation_filter_includes_dataset_without_published_version(
    management_scope: Scope,
) -> None:
    """Filter creation independently from publication timestamps."""
    draft = create_dated_dataset(management_scope, "2026-05-02T12:00:00+00:00", None)
    create_dated_dataset(management_scope, "2025-01-01T12:00:00+00:00", "2026-05-02T12:00:00+00:00")
    response = render_date_catalog(
        management_scope,
        {"date_field": "created", "date_from": "2026-05-02", "date_to": "2026-05-02"},
    )
    assert list(get_catalog_rows(response).queryset) == [draft]


@pytest.mark.parametrize(
    ("parameters", "error"),
    [
        ({"date_from": "not-a-date"}, "Enter a valid date (YYYY-MM-DD)."),
        ({"date_to": "2026-02-30"}, "Enter a valid date (YYYY-MM-DD)."),
        ({"date_field": "password"}, "Select a valid date field."),
        ({"date_to": "9999-12-31"}, "To must be earlier than 9999-12-31."),
        (
            {"date_from": "2026-05-03", "date_to": "2026-05-01"},
            "From must be on or before To.",
        ),
    ],
)
def test_invalid_date_range_shows_error_without_unfiltered_results(
    management_scope: Scope, parameters: dict[str, str], error: str
) -> None:
    """Reject malformed ranges safely in the sidebar, including manipulated requests."""
    response = render_date_catalog(management_scope, parameters)
    assert get_catalog_rows(response).result_count == 0
    assert error in response.content.decode()


def test_date_form_preserves_other_filters_and_resets_page(
    management_scope: Scope, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep search, sort and filters while applying dates and navigating results."""
    for _ in range(3):
        create_dated_dataset(
            management_scope, "2025-01-01T12:00:00+00:00", "2026-05-02T12:00:00+00:00"
        )
    monkeypatch.setattr(DatasetAdmin, "list_per_page", 1)
    parameters = {
        "q": "Dated",
        "o": "1",
        "p": "2",
        "status__exact": "ACTIVE",
        "date_field": "updated",
        "date_from": "2026-05-02",
        "date_to": "2026-05-02",
    }
    response = render_date_catalog(management_scope, parameters)
    changelist = get_catalog_rows(response)
    assert changelist.result_count == 3
    assert len(changelist.result_list) == 1
    next_query = parse_qs(changelist.get_query_string({"p": 1}).lstrip("?"))
    assert next_query["date_from"] == ["2026-05-02"]
    html = BeautifulSoup(response.content, "html.parser")
    form = html.select_one("#dataset-date-range-form")
    assert form is not None
    hidden = {str(item["name"]): item.get("value") for item in form.select('input[type="hidden"]')}
    assert hidden == {"q": "Dated", "o": "1", "status__exact": "ACTIVE"}
    assert form.select_one('input[name="date_from"][type="date"]') is not None
    assert form.select_one('input[name="date_to"][type="date"]') is not None
    assert "Apply dates" in form.get_text()
    clear_link = form.find("a", string="Clear dates")
    assert clear_link is not None
    assert parse_qs(str(clear_link["href"]).lstrip("?")) == {
        "q": ["Dated"],
        "o": ["1"],
        "status__exact": ["ACTIVE"],
    }


def test_date_filter_keeps_workspace_and_host_authorization(management_scope: Scope) -> None:
    """Keep inaccessible workspaces and other organizations out of rows and counts."""
    manager, editor, organization, namespace, original = management_scope
    visible = create_dated_dataset(
        management_scope, "2026-05-02T12:00:00+00:00", "2026-05-02T12:00:00+00:00"
    )
    foreign_organization = Organization.objects.create(
        name="Foreign", slug="foreign", last_modified_by=manager, resource_registry_enabled=True
    )
    foreign_manager = User.objects.create_user(username="foreign-manager")
    for destination_org in (organization, foreign_organization):
        workspace = Workspace.objects.create(
            organization=destination_org,
            name="Hidden",
            slug="hidden",
            last_modified_by=foreign_manager,
            state=Workspace.State.PRIVATE,
            resource_registry_enabled=True,
        )
        namespace, _ = RegistryNamespace.objects.get_or_create(workspace=workspace)
        hidden = create_dated_dataset(
            management_scope, "2026-05-02T12:00:00+00:00", "2026-05-02T12:00:00+00:00"
        )
        Dataset.objects.filter(pk=hidden.pk).update(
            tenant_id=namespace.id, workspace=workspace, organization=destination_org
        )
    response = render_date_catalog(
        (editor, manager, organization, namespace, original),
        {"date_from": "2026-05-02", "date_to": "2026-05-02"},
    )
    assert list(get_catalog_rows(response).queryset) == [visible]
    assert get_catalog_rows(response).result_count == 1


def test_date_filtered_catalog_has_bounded_queries(management_scope: Scope) -> None:
    """Avoid adding per-row queries when rendering dated datasets with versions and owners."""
    parameters = {"date_from": "2026-05-02", "date_to": "2026-05-02"}
    create_dated_dataset(management_scope, "2026-05-02T12:00:00+00:00", "2026-05-02T12:00:00+00:00")
    render_date_catalog(management_scope, parameters)
    with CaptureQueriesContext(connection) as small:
        render_date_catalog(management_scope, parameters)
    for _ in range(8):
        create_dated_dataset(
            management_scope, "2026-05-02T12:00:00+00:00", "2026-05-02T12:00:00+00:00"
        )
    with CaptureQueriesContext(connection) as large:
        response = render_date_catalog(management_scope, parameters)
    assert get_catalog_rows(response).result_count == 9
    assert len(large) <= len(small)
