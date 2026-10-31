"""Provide staff dataset maintenance screens backed by shared domain services."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from functools import wraps
from typing import Concatenate, ParamSpec, cast
from uuid import UUID, uuid4

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.urls.resolvers import URLPattern

from resource_registry.api.intakes.dependencies import get_staged_resource_intake_service
from resource_registry.cleanup.admin_views import DatasetCleanupAdminMixin
from resource_registry.dataset_admin_filters import DatasetDateRangeFilter
from resource_registry.management_domain.authorization import DatasetManagementAuthorization
from resource_registry.management_domain.background import schedule_intake_inspection
from resource_registry.management_domain.catalog import DatasetCatalogService, change_payload
from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.lifecycle import DatasetLifecycleService
from resource_registry.models import Dataset, DatasetChange, RegistryNamespace
from resource_registry.uploads.backend_blocks import build_backend_upload_descriptor
from resource_registry.uploads.exceptions import UploadApplicationError

AdminViewParameters = ParamSpec("AdminViewParameters")


def admin_domain_errors(
    view: Callable[Concatenate[DatasetAdmin, HttpRequest, AdminViewParameters], HttpResponse],
) -> Callable[Concatenate[DatasetAdmin, HttpRequest, AdminViewParameters], HttpResponse]:
    """Convert expected service failures into safe Admin responses."""

    @wraps(view)
    def wrapped(
        self: DatasetAdmin,
        request: HttpRequest,
        /,
        *args: AdminViewParameters.args,
        **kwargs: AdminViewParameters.kwargs,
    ) -> HttpResponse:
        try:
            return view(self, request, *args, **kwargs)
        except DatasetManagementError as error:
            return JsonResponse({"code": error.code, "detail": str(error)}, status=error.status)
        except UploadApplicationError as error:
            return JsonResponse(
                {"code": error.code, "detail": str(error)}, status=error.http_status
            )
        except (ValueError, ValidationError) as error:
            return JsonResponse(
                {
                    "code": "invalid_request",
                    "detail": "Check the submitted fields and reload the page.",
                },
                status=400,
            )

    return wrapped


class DatasetMetadataForm(forms.Form):
    """Expose editable metadata and a hidden optimistic revision."""

    name = forms.CharField(max_length=255, label="Name")
    description = forms.CharField(required=False, widget=forms.Textarea, label="Description")
    owner_team = forms.CharField(max_length=255, required=False, label="Responsible team")
    tags = forms.CharField(required=False, label="Tags", help_text="Separate tags with commas.")
    responsible_user_id = forms.ChoiceField(required=False, label="Responsible user")
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def clean_tags(self) -> list[str]:
        """Normalize the human-readable tag list before invoking the metadata service."""
        return list(
            dict.fromkeys(
                tag.strip() for tag in str(self.cleaned_data["tags"]).split(",") if tag.strip()
            )
        )


class DatasetDestinationFilter(admin.SimpleListFilter):
    """Show only destinations visible in the current catalog."""

    title = "Destination"
    parameter_name = "destination"

    def lookups(
        self, request: HttpRequest, model_admin: admin.ModelAdmin[Dataset]
    ) -> list[tuple[str, str]]:
        """Build selector choices from admitted namespaces."""
        try:
            return [
                (str(namespace.id), str(namespace.workspace or namespace.organization))
                for namespace in DatasetAdmin.authority(request).namespaces()
            ]
        except DatasetManagementError:
            return []

    def queryset(self, request: HttpRequest, queryset: QuerySet[Dataset]) -> QuerySet[Dataset]:
        """Apply the selected destination within the already authorized queryset."""
        return queryset.filter(tenant_id=str(self.value())) if self.value() else queryset


class DatasetResponsibleFilter(admin.SimpleListFilter):
    """Limit responsible-user choices to visible datasets."""

    title = "Responsible user"
    parameter_name = "responsible"

    def lookups(
        self, request: HttpRequest, model_admin: admin.ModelAdmin[Dataset]
    ) -> list[tuple[str, str]]:
        """List responsible users without querying foreign datasets."""
        return [
            (str(identifier), name)
            for identifier, name in model_admin.get_queryset(request)
            .exclude(responsible_user_id=None)
            .values_list("responsible_user_id", "responsible_user__username")
            .distinct()
        ]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Dataset]) -> QuerySet[Dataset]:
        """Filter by the selected responsible principal."""
        return (
            queryset.filter(responsible_user_id=int(str(self.value())))
            if self.value()
            else queryset
        )


class DatasetTagFilter(admin.SimpleListFilter):
    """Offer tags found in the authorized catalog."""

    title = "Tag"
    parameter_name = "tag"

    def lookups(
        self, request: HttpRequest, model_admin: admin.ModelAdmin[Dataset]
    ) -> list[tuple[str, str]]:
        """Read a bounded set of authorized tags."""
        tags = {
            str(tag)
            for values in model_admin.get_queryset(request).values_list("tags", flat=True)[:500]
            for tag in values
        }
        return [(tag, tag) for tag in sorted(tags)]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Dataset]) -> QuerySet[Dataset]:
        """Filter by the selected tag."""
        return queryset.filter(tags__contains=[self.value()]) if self.value() else queryset


@admin.register(Dataset)
class DatasetAdmin(DatasetCleanupAdminMixin, admin.ModelAdmin):  # type: ignore[type-arg]  # Runtime Django Admin is not generic.
    """Manage stable datasets through authorized application services."""

    list_display = (
        "name",
        "destination",
        "responsible_user",
        "current_version_label",
        "last_successful_update",
        "pending_update",
        "status",
    )
    list_filter = (
        DatasetDateRangeFilter,
        DatasetDestinationFilter,
        "visibility_scope",
        DatasetResponsibleFilter,
        DatasetTagFilter,
        "source_type",
        "status",
    )
    search_fields = ("name", "description", "slug")
    list_per_page = 50
    actions = None
    change_list_template = "admin/resource_registry/dataset/change_list.html"

    @staticmethod
    def authority(request: HttpRequest) -> DatasetManagementAuthorization:
        """Build authority only for an active staff request."""
        if not request.user.is_active or not request.user.is_staff:
            raise DatasetManagementError(
                "staff_required", "Staff access is required for Django Admin.", 403
            )
        return DatasetManagementAuthorization(request.user, getattr(request, "organization", None))

    def has_module_permission(self, request: HttpRequest) -> bool:
        """Expose the catalog to staff with admitted destination access."""
        try:
            return bool(self.authority(request).namespace_ids)
        except DatasetManagementError:
            return False

    def has_view_permission(self, request: HttpRequest, obj: Dataset | None = None) -> bool:
        """Allow scoped readers without requiring global model permissions."""
        if not self.has_module_permission(request):
            return False
        return obj is None or self.get_queryset(request).filter(pk=obj.pk).exists()

    def has_change_permission(self, request: HttpRequest, obj: Dataset | None = None) -> bool:
        """Require destination management or explicit editing assignment."""
        try:
            authority = self.authority(request)
            authority.require_enabled()
            if obj is None:
                return bool(authority.namespace_ids)
            if obj.purge_started_at is not None:
                return False
            authority.require_edit(obj)
            return True
        except DatasetManagementError:
            return False

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Allow creation only where a manager enabled the actor."""
        try:
            authority = self.authority(request)
            authority.require_enabled()
            for namespace in authority.namespaces():
                try:
                    authority.require_create(namespace)
                    return True
                except DatasetManagementError:
                    continue
        except DatasetManagementError:
            pass
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Dataset | None = None) -> bool:
        """Disable direct deletion and destructive bulk actions."""
        return False

    def get_queryset(self, request: HttpRequest) -> QuerySet[Dataset]:
        """Eager-load catalog rows after host-bound authorization."""
        try:
            self.authority(request)
            return (
                DatasetCatalogService(
                    cast(User, request.user), getattr(request, "organization", None)
                )
                .queryset(include_archived=True)
                .select_related("workspace__organization")
            )
        except DatasetManagementError:
            return Dataset.objects.none()

    @admin.display(description="Destination")
    def destination(self, obj: Dataset) -> str:
        """Display the preloaded destination name."""
        return str(obj.workspace or obj.organization or "—")

    @admin.display(description="Current version")
    def current_version_label(self, obj: Dataset) -> str:
        """Display the selected version or a migration review warning."""
        return str(obj.current_version.version_number) if obj.current_version else "Needs review"

    @admin.display(description="Last successful update")
    def last_successful_update(self, obj: Dataset) -> datetime | str:
        """Display the preloaded current-version time in the Admin's local timezone."""
        return obj.current_version.created_at if obj.current_version else "—"

    @admin.display(description="Pending update")
    def pending_update(self, obj: Dataset) -> str:
        """Display the status annotated by the bounded catalog query."""
        return str(getattr(obj, "pending_update_status", None) or "—")

    def get_urls(self) -> list[URLPattern]:
        """Register CSRF-protected Admin views ahead of generic model routes."""
        endpoints: list[tuple[str, Callable[..., HttpResponse], str]] = [
            ("cleanup/", self.cleanup_list_view, "cleanup"),
            ("cleanup/<uuid:request_id>/", self.cleanup_detail_view, "cleanup_detail"),
            ("<uuid:dataset_id>/deletion/", self.deletion_view, "deletion"),
            ("upload/", self.upload_view, "upload"),
            ("changes/", self.changes_view, "changes"),
            ("changes/<uuid:change_id>/", self.review_view, "review"),
            ("changes/<uuid:change_id>/status/", self.progress_view, "progress"),
            ("<uuid:dataset_id>/preview/", self.preview_view, "preview"),
            ("intakes/create/", self.intake_create_view, "intake_create"),
            ("intakes/<uuid:intake_id>/<str:operation>/", self.intake_action_view, "intake_action"),
            (
                "intakes/<uuid:intake_id>/blocks/<int:block_index>/",
                self.intake_block_view,
                "intake_block",
            ),
        ]
        return [
            path(route, self.admin_site.admin_view(view), name=f"resource_registry_dataset_{name}")
            for route, view, name in endpoints
        ] + super().get_urls()

    def _render(self, request: HttpRequest, template: str, **context: object) -> TemplateResponse:
        return TemplateResponse(
            request,
            f"admin/resource_registry/dataset/{template}.html",
            {**self.admin_site.each_context(request), "opts": self.model._meta, **context},
        )

    @admin_domain_errors
    def add_view(
        self,
        request: HttpRequest,
        form_url: str = "",
        extra_context: dict[str, object] | None = None,
    ) -> HttpResponse:
        """Route creation to the verified-file workflow."""
        if not self.has_add_permission(request):
            raise PermissionDenied
        return HttpResponseRedirect(reverse("admin:resource_registry_dataset_upload"))

    @admin_domain_errors
    def change_view(
        self,
        request: HttpRequest,
        object_id: str,
        form_url: str = "",
        extra_context: dict[str, object] | None = None,
    ) -> HttpResponse:
        """Render metadata, immutable history and explicit lifecycle actions."""
        authority = self.authority(request)
        service = DatasetLifecycleService(cast(User, request.user), authority.organization)
        dataset = service.get_dataset(object_id)
        namespace = RegistryNamespace.objects.select_related("workspace", "organization").get(
            pk=dataset.tenant_id
        )
        editable = self.has_change_permission(request, dataset)
        managed = authority.can_manage_namespace(namespace)
        form = DatasetMetadataForm(
            (
                request.POST
                if request.method == "POST" and request.POST.get("operation") == "metadata"
                else None
            ),
            initial={
                "name": dataset.name,
                "description": dataset.description,
                "owner_team": dataset.owner_team,
                "tags": ", ".join(dataset.tags),
                "responsible_user_id": dataset.responsible_user_id or "",
                "expected_revision": dataset.revision,
            },
        )
        cast(forms.ChoiceField, form.fields["responsible_user_id"]).choices = [
            ("", "Unassigned")
        ] + [(str(user.id), user.get_username()) for user in authority.eligible_users(namespace)]
        if request.method == "POST":
            operation = request.POST.get("operation")
            try:
                if operation == "metadata" and form.is_valid():
                    values = dict(form.cleaned_data)
                    revision = values.pop("expected_revision")
                    values["responsible_user_id"] = (
                        int(values["responsible_user_id"])
                        if values["responsible_user_id"]
                        else None
                    )
                    values["tags"] = values["tags"] or []
                    service.update_metadata(dataset.id, values, expected_revision=revision)
                elif operation in {"archive", "unarchive", "restore-version"}:
                    expected_revision = int(request.POST.get("expected_revision", "-1"))
                    reason = request.POST.get("reason", "")
                    if operation == "restore-version":
                        service.restore_version(
                            dataset.id,
                            version_id=request.POST.get("version_id", ""),
                            expected_revision=expected_revision,
                            reason=reason,
                        )
                    else:
                        {"archive": service.archive, "unarchive": service.unarchive}[operation](
                            dataset.id, expected_revision=expected_revision, reason=reason
                        )
                else:
                    form.add_error(None, "Check the form fields.")
                if not form.errors:
                    messages.success(request, "Changes saved.")
                    return HttpResponseRedirect(request.path)
            except (DatasetManagementError, ValidationError) as error:
                form.add_error(None, str(error))
        catalog = DatasetCatalogService(cast(User, request.user), authority.organization)
        return self._render(
            request,
            "detail",
            title=dataset.name,
            dataset=dataset,
            form=form,
            editable=editable,
            managed=managed,
            schema=catalog.get_detail(dataset.id)["schema"],
            versions=dataset.versions.filter(status="READY").order_by("-version_number")[:100],
            activity=dataset.management_activity.select_related("actor").order_by("-created_at")[
                :100
            ],
            usage=catalog.get_usage(dataset.id),
            preview_url=reverse("admin:resource_registry_dataset_preview", args=[dataset.id]),
        )

    @admin_domain_errors
    def preview_view(self, request: HttpRequest, dataset_id: UUID) -> JsonResponse:
        """Load the authorized current-version preview on demand."""
        authority = self.authority(request)
        return JsonResponse(
            DatasetCatalogService(cast(User, request.user), authority.organization).get_preview(
                dataset_id
            )
        )

    @admin_domain_errors
    def upload_view(self, request: HttpRequest) -> HttpResponse:
        """Select a permitted destination and an explicit target before uploading."""
        authority = self.authority(request)
        authority.require_enabled()
        dataset_id = request.GET.get("dataset_id") or request.POST.get("dataset_id")
        dataset = (
            DatasetLifecycleService(cast(User, request.user), authority.organization).get_dataset(
                dataset_id
            )
            if dataset_id
            else None
        )
        if dataset:
            if dataset.external_source_id:
                raise DatasetManagementError(
                    "source_managed_dataset", "Content is managed by its external source."
                )
            authority.require_edit(dataset)
            namespaces = [authority.get_namespace(dataset.tenant_id)]
        else:
            namespaces = []
            for namespace in authority.namespaces():
                try:
                    authority.require_create(namespace)
                    namespaces.append(namespace)
                except DatasetManagementError:
                    continue
        if not namespaces:
            raise DatasetManagementError("create_denied", "No permitted creation destination.", 403)
        if request.method == "POST":
            specification: dict[str, object] = {"name": request.POST.get("name", "")}
            if dataset:
                specification = (
                    {
                        "workbook_id": str(dataset.workbook_id),
                        "workbook_revision": dataset.workbook.revision,
                    }
                    if dataset.workbook
                    else {
                        "dataset_id": str(dataset.id),
                        "expected_revision": int(request.POST.get("expected_revision", "-1")),
                    }
                )
            change = DatasetChangeService(cast(User, request.user), authority.organization).create(
                namespace_id=request.POST.get("namespace_id", ""),
                intake_id=request.POST.get("intake_id", ""),
                idempotency_key=request.POST.get("idempotency_key", ""),
                specification=specification,
            )
            return HttpResponseRedirect(
                reverse("admin:resource_registry_dataset_review", args=[change.id])
            )
        return self._render(
            request,
            "upload",
            title="Update data" if dataset else "Create from file",
            dataset=dataset,
            namespaces=namespaces,
            request_key=str(uuid4()),
        )

    @admin_domain_errors
    def intake_create_view(self, request: HttpRequest) -> JsonResponse:
        """Create an owned private upload after validating scope-specific write authority."""
        if request.method != "POST":
            return JsonResponse({}, status=405)
        authority = self.authority(request)
        authority.require_enabled()
        namespace = authority.get_namespace(request.POST.get("namespace_id", ""))
        if request.POST.get("dataset_id"):
            dataset = DatasetLifecycleService(
                cast(User, request.user), authority.organization
            ).get_dataset(request.POST["dataset_id"])
            authority.require_edit(dataset)
            if dataset.tenant_id != namespace.id:
                raise DatasetManagementError(
                    "destination_mismatch", "The dataset belongs to another destination.", 400
                )
        else:
            authority.require_create(namespace)
        result = get_staged_resource_intake_service().create_intake(
            user=cast(User, request.user),
            filename=request.POST.get("filename", ""),
            content_type=request.POST.get("content_type", ""),
            size_bytes=int(request.POST.get("size_bytes", "0")),
            checksum_algorithm=None,
            checksum=None,
            idempotency_key=request.POST.get("idempotency_key", ""),
            issue_direct_authorization=False,
        )
        identifier = result.intake.intake_id
        return JsonResponse(
            {
                "intake_id": str(identifier),
                "status": result.intake.status,
                "upload": build_backend_upload_descriptor(
                    reverse("admin:resource_registry_dataset_intake_block", args=[identifier, 0])
                ),
                "status_url": reverse(
                    "admin:resource_registry_dataset_intake_action", args=[identifier, "status"]
                ),
                "complete_url": reverse(
                    "admin:resource_registry_dataset_intake_action", args=[identifier, "complete"]
                ),
            }
        )

    @admin_domain_errors
    def intake_action_view(
        self, request: HttpRequest, intake_id: UUID, operation: str
    ) -> JsonResponse:
        """Read or complete only the caller's private intake using existing services."""
        authority = self.authority(request)
        authority.require_enabled()
        service = get_staged_resource_intake_service()
        if operation == "status" and request.method == "GET":
            snapshot = service.get_intake(user=cast(User, request.user), intake_id=intake_id)
        elif operation == "complete" and request.method == "POST":
            snapshot = service.complete_intake(
                user=cast(User, request.user),
                intake_id=intake_id,
                expected_etag=None,
                commit_backend_blocks=True,
            )
        else:
            return JsonResponse({}, status=405)
        if snapshot.status in {"COMPLETED", "ANALYZING"} and authority.organization is not None:
            schedule_intake_inspection(
                snapshot.intake_id, organization_id=authority.organization.pk
            )
        return JsonResponse(
            {
                "intake_id": str(snapshot.intake_id),
                "status": snapshot.status,
                "failure_code": snapshot.failure_code,
            }
        )

    @admin_domain_errors
    def intake_block_view(
        self, request: HttpRequest, intake_id: UUID, block_index: int
    ) -> JsonResponse:
        """Upload a bounded CSRF-protected block without global intake permissions."""
        self.authority(request).require_enabled()
        if request.method != "PUT" or request.content_type != "application/octet-stream":
            return JsonResponse({}, status=405)
        get_staged_resource_intake_service().upload_backend_block(
            user=cast(User, request.user),
            intake_id=intake_id,
            block_index=block_index,
            read_chunk=request.read,
        )
        return JsonResponse({"block_index": block_index})

    @admin_domain_errors
    def changes_view(self, request: HttpRequest) -> HttpResponse:
        """Show persistent owned, pending-publication and failed proposals."""
        authority = self.authority(request)
        managed = [
            namespace.id
            for namespace in authority.namespaces()
            if authority.can_manage_namespace(namespace)
        ]
        queryset = DatasetChange.objects.filter(namespace_id__in=authority.namespace_ids).filter(
            Q(submitted_by=cast(User, request.user)) | Q(namespace_id__in=managed)
        )
        selection = request.GET.get("view", "mine")
        if selection == "mine":
            queryset = queryset.filter(submitted_by=cast(User, request.user))
        elif selection == "pending":
            queryset = queryset.filter(status__in=["READY", "NEEDS_MANAGER"])
        elif selection == "failed":
            queryset = queryset.filter(status="FAILED")
        return self._render(
            request,
            "changes",
            title="Dataset updates",
            changes=queryset.select_related("submitted_by").order_by("-created_at")[:200],
            selection=selection,
        )

    @admin_domain_errors
    def progress_view(self, request: HttpRequest, change_id: UUID) -> JsonResponse:
        """Return sanitized progress to its owner or destination manager."""
        authority = self.authority(request)
        return JsonResponse(
            change_payload(
                DatasetChangeService(cast(User, request.user), authority.organization).get_change(
                    change_id
                )
            )
        )

    @admin_domain_errors
    def review_view(self, request: HttpRequest, change_id: UUID) -> HttpResponse:
        """Review differences, map worksheets and publish through the atomic service."""
        authority = self.authority(request)
        service = DatasetChangeService(cast(User, request.user), authority.organization)
        change = service.get_change(change_id)
        namespace = authority.get_namespace(change.namespace_id)
        managed = authority.can_manage_namespace(namespace)
        target = (
            authority.datasets().filter(pk=change.specification.get("dataset_id")).first()
            if change.specification.get("dataset_id")
            else None
        )
        if request.method == "POST":
            revision = int(request.POST.get("expected_revision", "-1"))
            operation = request.POST.get("operation")
            try:
                if operation == "revise":
                    specification = dict(change.specification)
                    specification.pop("dataset_id", None)
                    specification.pop("expected_revision", None)
                    specification["sheets"] = []
                    for index, sheet in enumerate(change.prepared.get("available_sheets", [])):
                        identifier, _, reviewed_revision = request.POST.get(
                            f"dataset_{index}", ""
                        ).partition("|")
                        dataset = (
                            Dataset.objects.filter(
                                pk=identifier, tenant_id=change.namespace_id
                            ).first()
                            if identifier
                            else None
                        )
                        if identifier and dataset is None:
                            raise DatasetManagementError(
                                "not_found", "The selected dataset is unavailable.", 404
                            )
                        specification["sheets"].append(
                            {
                                "sheet_name": sheet,
                                "selected": f"selected_{index}" in request.POST,
                                "dataset_id": str(dataset.id) if dataset else None,
                                "expected_revision": int(reviewed_revision) if dataset else 0,
                            }
                        )
                    if change.workbook:
                        specification["workbook_revision"] = int(
                            request.POST.get("workbook_revision", "-1")
                        )
                        specification["archive"] = []
                        for selected in request.POST.getlist("archive_dataset"):
                            identifier, _, reviewed_revision = selected.partition("|")
                            specification["archive"].append(
                                {
                                    "dataset_id": identifier,
                                    "expected_revision": int(reviewed_revision),
                                }
                            )
                    service.revise(change.id, specification, expected_revision=revision)
                elif operation == "refresh" and target is not None:
                    specification = dict(change.specification)
                    specification["expected_revision"] = int(
                        request.POST.get("target_revision", "-1")
                    )
                    service.revise(change.id, specification, expected_revision=revision)
                else:
                    methods = {
                        "publish": service.publish,
                        "cancel": service.cancel,
                        "retry": service.retry,
                    }
                    if operation not in methods:
                        raise DatasetManagementError(
                            "invalid_operation", "Select a supported action.", 400
                        )
                    methods[operation](change.id, expected_revision=revision)
                messages.success(request, "Action recorded.")
                return HttpResponseRedirect(request.path)
            except DatasetManagementError as error:
                messages.error(request, str(error))
        candidates = authority.editable_datasets(namespace)
        if change.workbook_id:
            candidates = candidates.filter(workbook_id=change.workbook_id)
        return self._render(
            request,
            "review",
            title="Review update",
            change=change,
            payload=change_payload(change),
            candidates=candidates,
            sheets=change.prepared.get("suggested_sheets", []),
            managed=managed,
            target=target,
            missing_datasets=(
                candidates.exclude(workbook_sheet__in=change.prepared.get("available_sheets", []))
                if change.workbook_id
                else []
            ),
            progress_url=reverse("admin:resource_registry_dataset_progress", args=[change.id]),
        )
