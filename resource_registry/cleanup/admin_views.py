"""Render English cleanup forms with direct application-service calls."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, cast
from uuid import UUID, uuid4

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from Organization.models import Organization
from resource_registry.cleanup.service import DatasetCleanupService
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.models import Dataset, ResourceCleanupRequest

ViewParameters = ParamSpec("ViewParameters")


def cleanup_admin_errors(
    view: Callable[ViewParameters, HttpResponse],
) -> Callable[ViewParameters, HttpResponse]:
    """Return sanitized domain errors for direct Admin URLs and manipulated requests."""

    @wraps(view)
    def wrapped(*args: ViewParameters.args, **kwargs: ViewParameters.kwargs) -> HttpResponse:
        try:
            return view(*args, **kwargs)
        except DatasetManagementError as error:
            return JsonResponse({"code": error.code, "detail": str(error)}, status=error.status)

    return wrapped


class DeletionReviewForm(forms.Form):
    """Require a reviewed fingerprint, optimistic revision and explicit confirmation."""

    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    inventory_fingerprint = forms.RegexField(r"^[0-9a-f]{64}$", widget=forms.HiddenInput)
    idempotency_key = forms.CharField(max_length=128, widget=forms.HiddenInput)
    reason = forms.CharField(max_length=500, label="Reason")
    confirmed = forms.BooleanField(
        label="I understand that deletion cannot be undone once execution starts."
    )


def _service(request: HttpRequest) -> DatasetCleanupService:
    if not request.user.is_active or not request.user.is_staff:
        raise PermissionDenied
    organization = getattr(request, "organization", None)
    return DatasetCleanupService(
        request.user, organization if isinstance(organization, Organization) else None
    )


def _render(request: HttpRequest, template: str, **context: object) -> TemplateResponse:
    return TemplateResponse(
        request,
        f"admin/resource_registry/dataset/{template}.html",
        {**admin.site.each_context(request), "opts": Dataset._meta, **context},
    )


class DatasetCleanupAdminMixin:
    """Add business-authorized cleanup screens to the Dataset ModelAdmin."""

    @cleanup_admin_errors
    def deletion_view(self, request: HttpRequest, dataset_id: UUID) -> HttpResponse:
        """Review and explicitly confirm one dataset's irreversible deletion."""
        service = _service(request)
        dataset = service.get_dataset(dataset_id)
        try:
            offset = max(0, int(request.GET.get("offset", 0)))
        except ValueError:
            offset = 0
        preview = service.preview(dataset_id, offset=offset)
        form = DeletionReviewForm(
            request.POST if request.method == "POST" else None,
            initial={
                "expected_revision": dataset.revision,
                "inventory_fingerprint": preview["inventory_fingerprint"],
                "idempotency_key": str(uuid4()),
            },
        )
        if request.method == "POST" and form.is_valid():
            try:
                values = dict(form.cleaned_data)
                values.pop("confirmed")
                cleanup_request = service.request_deletion(dataset_id, **values)
                messages.success(
                    request,
                    "Deletion request saved. Execution follows the configured cleanup mode and recovery deadline.",
                )
                return HttpResponseRedirect(
                    reverse(
                        "admin:resource_registry_dataset_cleanup_detail", args=[cleanup_request.pk]
                    )
                )
            except DatasetManagementError as error:
                form.add_error(None, str(error))
        return _render(
            request,
            "deletion",
            title="Review deletion",
            dataset=dataset,
            preview=preview,
            earliest_execution=parse_datetime(str(preview["execute_after"])),
            derivative_counts=[
                (name.replace("_", " ").capitalize(), count)
                for name, count in cast(dict[str, int], preview["derivatives"]).items()
            ],
            form=form,
        )

    @cleanup_admin_errors
    def cleanup_list_view(self, request: HttpRequest) -> HttpResponse:
        """List scoped requests with pagination and persistent status filters."""
        service = _service(request)
        rows = service.queryset().order_by("-created_at")
        status = request.GET.get("status", "")
        if status:
            rows = rows.filter(status=status)
        if request.GET.get("q"):
            rows = rows.filter(dataset__name__icontains=request.GET["q"])
        return _render(
            request,
            "cleanup_list",
            title="Cleanup candidates",
            page=Paginator(rows, 50).get_page(request.GET.get("page")),
            status=status,
            query=request.GET.get("q", ""),
            statuses=ResourceCleanupRequest.Status.choices,
        )

    @cleanup_admin_errors
    def cleanup_detail_view(self, request: HttpRequest, request_id: UUID) -> HttpResponse:
        """Display progress and apply cancellation, retry or a newly reviewed inventory."""
        service = _service(request)
        cleanup_request = service.get_request(request_id)
        preview = (
            service.preview(cleanup_request.dataset_id) if cleanup_request.dataset_id else None
        )
        form = DeletionReviewForm(
            request.POST if request.method == "POST" else None,
            initial={
                "expected_revision": preview["revision"] if preview else 0,
                "inventory_fingerprint": preview["inventory_fingerprint"] if preview else "",
                "reason": cleanup_request.reason,
                "idempotency_key": cleanup_request.idempotency_key,
            },
        )
        if request.method == "POST":
            try:
                operation = request.POST.get("operation")
                completed = False
                if operation == "confirm" and form.is_valid():
                    service.confirm(
                        request_id,
                        expected_revision=form.cleaned_data["expected_revision"],
                        inventory_fingerprint=form.cleaned_data["inventory_fingerprint"],
                    )
                    completed = True
                elif operation in {"cancel", "retry"}:
                    {"cancel": service.cancel, "retry": service.retry}[operation](request_id)
                    completed = True
                else:
                    form.add_error(None, "Check the confirmation fields.")
                if completed:
                    messages.success(request, "Cleanup request updated.")
                    return HttpResponseRedirect(request.path)
            except DatasetManagementError as error:
                form.add_error(None, str(error))
        return _render(
            request,
            "cleanup_detail",
            title="Cleanup progress",
            cleanup=cleanup_request,
            preview=preview,
            form=form,
            page=Paginator(
                cleanup_request.items.select_related("storage_object").order_by("id"), 50
            ).get_page(request.GET.get("page")),
        )
