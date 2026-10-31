"""Verify safe, readable inspection output in the intake Admin detail."""

import json
from typing import cast

import pytest
from bs4 import BeautifulSoup
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.template.response import TemplateResponse

from resource_registry.admin import StagedResourceIntakeAdmin
from resource_registry.models import StagedResourceIntake, StagedResourceIntakeStatus
from resource_registry.tests.test_staged_resource_intake_admin_permissions import (
    _create_intake,
    _request_for_user,
)


@pytest.mark.parametrize(
    "inspection",
    [
        {},
        None,
        {"sheets": [{"name": '<script>alert("x")</script>', "columns": ["Región", "a&b"]}]},
    ],
)
def test_inspection_details_preserve_json_without_rendering_html(inspection: object) -> None:
    """Keep inspection values readable while escaping untrusted file metadata.

    Args:
        inspection: Empty or untrusted inspection data to display.
    """
    intake = StagedResourceIntake(inspection_json=inspection)
    intake_admin = StagedResourceIntakeAdmin(StagedResourceIntake, admin.site)

    markup = intake_admin.inspection_details(intake)
    document = BeautifulSoup(markup, "html.parser")
    preview = document.find("pre")

    assert preview is not None
    assert document.find("script") is None
    assert preview.get_text() == json.dumps(inspection, indent=2, ensure_ascii=False)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("can_manage", "status"),
    [
        (True, StagedResourceIntakeStatus.READY_FOR_PUBLICATION),
        (True, StagedResourceIntakeStatus.CANCELLED),
        (False, StagedResourceIntakeStatus.READY_FOR_PUBLICATION),
    ],
)
def test_detail_keeps_readonly_inspection_and_permission_gated_actions(
    can_manage: bool, status: StagedResourceIntakeStatus
) -> None:
    """Render safe intake details without exposing unauthorized publication actions.

    Args:
        can_manage: Whether the staff user can operate on intakes.
        status: Lifecycle state controlling publication availability.
    """
    user = get_user_model().objects.create_user(username="intake-layout-user", is_staff=True)
    permission = "manage_stagedresourceintake" if can_manage else "view_stagedresourceintake"
    user.user_permissions.add(Permission.objects.get(codename=permission))
    intake = _create_intake(submitted_by=user, status=status)
    intake.inspection_json = {"name": "<script>untrusted</script>"}
    intake.save(update_fields=["inspection_json"])
    intake_admin = StagedResourceIntakeAdmin(StagedResourceIntake, admin.site)

    response = cast(
        TemplateResponse,
        intake_admin.change_view(_request_for_user(user), str(intake.pk)),
    )
    document = BeautifulSoup(response.render().content, "html.parser")
    links = [link.get_text(strip=True) for link in document.select(".object-tools a")]
    preview = document.select_one(".intake-inspection-json")

    assert response.status_code == 200
    assert ("Publish to destinations" in links) is (
        can_manage and status == StagedResourceIntakeStatus.READY_FOR_PUBLICATION
    )
    assert ("Remove destination assignments" in links) is can_manage
    assert preview is not None
    assert preview.find("script") is None
    assert json.loads(preview.get_text()) == intake.inspection_json
    assert not document.select("input[name='inspection_json'], input[name='status']")
