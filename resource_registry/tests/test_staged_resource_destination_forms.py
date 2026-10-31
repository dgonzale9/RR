"""Verify destination action forms retain safe, accessible submission behavior."""

from uuid import UUID

import pytest
from bs4 import BeautifulSoup
from django.forms import Form
from django.http import QueryDict
from django.template.loader import render_to_string

from resource_registry.admin import PublishDestinationsForm, RemoveDestinationsForm
from resource_registry.models import StagedResourceIntake

FIRST_DESTINATION = "10000000-0000-0000-0000-000000000001"
SECOND_DESTINATION = "10000000-0000-0000-0000-000000000002"


def build_destination_form(
    action: str, *, data: QueryDict | None = None, empty: bool = False
) -> Form:
    """Build the real Admin form with local destination choices.

    Args:
        action: Publication or removal template name.
        data: Optional submitted values.
        empty: Whether the user has no eligible destinations.

    Returns:
        Configured publication or removal form.
    """
    choices = (
        []
        if empty
        else [
            (FIRST_DESTINATION, "Digitas — Main"),
            (SECOND_DESTINATION, '<script>alert("destination")</script>'),
        ]
    )
    if action == "publish":
        return PublishDestinationsForm(data, choices=[] if empty else [("Digitas", choices)])
    return RemoveDestinationsForm(data, choices=choices)


def render_destination_form(action: str, form: Form) -> BeautifulSoup:
    """Render an action page without accessing a database or external storage.

    Args:
        action: Publication or removal template name.
        form: The bound or unbound Admin form to render.

    Returns:
        Parsed HTML for inspecting form behavior.
    """
    intake = StagedResourceIntake(
        id=UUID("20000000-0000-0000-0000-000000000001"),
        normalized_filename="sales.json",
        status="PUBLISHED",
        resolved_file_format="JSON",
    )
    return BeautifulSoup(
        render_to_string(
            f"admin/resource_registry/stagedresourceintake/{action}.html",
            {"form": form, "intake": intake, "csrf_token": "test-csrf-token"},
        ),
        "html.parser",
    )


@pytest.mark.parametrize("action", ["publish", "remove_destinations"])
def test_selected_checkboxes_preserve_submission_values_and_csrf(action: str) -> None:
    """Keep multiple destination values and escape destination labels.

    Args:
        action: Publication or removal template name.
    """
    field_name = "namespace_ids" if action == "publish" else "publication_ids"
    data = QueryDict(mutable=True)
    data.setlist(field_name, [FIRST_DESTINATION, SECOND_DESTINATION])
    data.update({"reason": "Retire these assignments.", "confirmation": "on"})
    form = build_destination_form(action, data=data)
    assert form.is_valid(), form.errors

    document = render_destination_form(action, form)
    checkboxes = document.select(f'input[type="checkbox"][name="{field_name}"]')

    assert {checkbox["value"] for checkbox in checkboxes} == {
        FIRST_DESTINATION,
        SECOND_DESTINATION,
    }
    assert all(checkbox.has_attr("checked") for checkbox in checkboxes)
    assert form.cleaned_data[field_name] == [FIRST_DESTINATION, SECOND_DESTINATION]
    assert document.select_one(f'select[name="{field_name}"]') is None
    assert document.select_one('form[method="post"] input[name="csrfmiddlewaretoken"]')
    assert '<script>alert("destination")</script>' in document.get_text()
    assert not any("alert(" in script.get_text() for script in document.find_all("script"))


@pytest.mark.parametrize("action", ["publish", "remove_destinations"])
def test_invalid_destination_shows_errors_and_retains_valid_selection(action: str) -> None:
    """Reject forged choices while retaining the user's eligible selection.

    Args:
        action: Publication or removal template name.
    """
    field_name = "namespace_ids" if action == "publish" else "publication_ids"
    data = QueryDict(mutable=True)
    data.setlist(field_name, [FIRST_DESTINATION, "unauthorized-destination"])
    form = build_destination_form(action, data=data)
    assert not form.is_valid()

    document = render_destination_form(action, form)
    selected = document.select(f'input[name="{field_name}"][checked]')

    assert [checkbox["value"] for checkbox in selected] == [FIRST_DESTINATION]
    assert str(form.errors[field_name][0]) in document.get_text()
    if action == "remove_destinations":
        assert "reason" in form.errors
        assert "confirmation" in form.errors
        assert str(form.errors["reason"][0]) in document.get_text()
        assert document.select_one('textarea[name="reason"]')
        assert document.select_one('input[name="confirmation"][required]')


@pytest.mark.parametrize("action", ["publish", "remove_destinations"])
def test_no_destinations_disables_submission_and_keeps_cancel_link(action: str) -> None:
    """Explain an empty destination list without offering an unusable submit action.

    Args:
        action: Publication or removal template name.
    """
    document = render_destination_form(action, build_destination_form(action, empty=True))

    assert document.select_one('[type="submit"][disabled]')
    assert "No eligible" in document.get_text()
    assert document.find("a", string="Cancel")
